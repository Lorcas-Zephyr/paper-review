# api_server.py
import asyncio
import sys
import os
from pathlib import Path
from typing import Optional, List, Dict, Any

# 添加项目根目录到Python路径，确保能导入您的模块
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
import uvicorn
import json
import logging
from fastapi.middleware.cors import CORSMiddleware

# 导入您现有项目中的核心模块
try:
    from run import ReflectionOrchestrator
    from src.db import db_manager
    from src.common.models import ReflectionResult
    from src.common.utils_b import dedupe_prioritized_issues, issue_description_dedup_key
    from src.common.report_generator import report_generator
except ImportError as e:
    print(f"导入项目模块失败，请检查路径和依赖: {e}")
    sys.exit(1)

# --- 配置日志 ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- 初始化 FastAPI 应用和全局组件 ---
app = FastAPI(
    title="论文自动评估系统 API",
    description="接收 paper_id，自动从数据库读取数据并生成评估报告",
    version="1.0.0"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有源
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class EvaluatorManager:
    """评估器管理器（单例）"""
    _evaluator = None
    _rules_dict = None

    @classmethod
    async def get_evaluator(cls):
        """获取评估器实例"""
        if cls._evaluator is None:
            cls._evaluator = ReflectionOrchestrator(
                mode="database",
                enable_dialogue=True,
                always_use_llm=False,
                enable_hallucination_filter=True
            )
            cls._evaluator.initialize_modules()
            logger.info("评估引擎初始化完成")
        return cls._evaluator

    @classmethod
    async def get_rules_dict(cls):
        """获取规则字典（缓存）"""
        if cls._rules_dict is None:
            await db_manager.connect()
            rules = await db_manager.fetch_rules()
            cls._rules_dict = {r["rule_id"]: r for r in rules}
            logger.info(f"已加载 {len(rules)} 条评审规则")
        return cls._rules_dict

# --- 数据模型 ---
class EvaluateRequest(BaseModel):
    """评估请求"""
    paper_id: str = Field(..., description="论文ID")
    force_evaluate: bool = Field(False, description="是否强制评估（即使审计组不全）")
    enable_dialogue: bool = Field(True, description="是否生成导师对话/导师评语")
    return_report_path: bool = Field(True, description="是否在响应中包含报告文件路径")

class EvaluationResult(BaseModel):
    """评估结果"""
    success: bool
    paper_id: str
    paper_name: Optional[str] = None
    initial_score: Optional[float] = None
    final_score: Optional[float] = None
    verdict: Optional[str] = None
    needs_human_review: Optional[bool] = None
    human_review_reason: Optional[str] = None
    critical_issues_count: Optional[int] = None
    major_issues_count: Optional[int] = None
    minor_issues_count: Optional[int] = None
    markdown_report_path: Optional[str] = None
    error: Optional[str] = None
    warning: Optional[str] = None


class InlineEvaluateRequest(BaseModel):
    """调度器专用：直接传入各组审计结果，无需事先写入 agent_audit_result 表"""

    paper_id: str = Field(..., description="论文 ID（与调度器一致）")
    paper_title: str = ""
    paper_content: str = ""
    audit_groups: List[Dict[str, Any]] = Field(
        ...,
        description="与 review_engine 兼容的分组列表，含 group_id、group_name、audit_results",
    )
    enable_dialogue: bool = Field(True, description="是否生成导师评语（导师对话）")


# --- 核心功能函数（复用 run.py 中的数据库读取逻辑）---
async def fetch_and_evaluate_paper(paper_id: str, force_evaluate: bool = False,
                                   enable_dialogue: bool = True) -> Dict[str, Any]:
    """
    从数据库获取数据并评估单篇论文
    这是 run.py 中 run_from_database 逻辑的核心部分
    """
    # 连接数据库
    await db_manager.connect()

    try:
        # 1. 获取规则
        rules_dict = await EvaluatorManager.get_rules_dict()

        # 2. 检查4个审计组是否齐全
        has_all, existing_codes = await db_manager.check_paper_has_all_agents(paper_id)
        missing_codes = list({"FMT", "REF", "EXP", "LOG"} - set(existing_codes))

        warning_msg = ""
        if not has_all:
            if force_evaluate:
                warning_msg = f"论文 {paper_id} 的审计组不全，缺少: {', '.join(missing_codes)}。强制模式下继续评估，结果可能不完整。"
                logger.warning(warning_msg)
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"论文 {paper_id} 的审计组不全，缺少: {', '.join(missing_codes)}。如需强制评估，请设置 force_evaluate=true"
                )

        # 3. 检查是否需要重新评估（非强制模式时）
        if not force_evaluate:
            needs_eval = await db_manager.check_needs_reevaluation(paper_id)
            if not needs_eval:
                # 如果不需要重新评估，可以返回已存在的结果
                existing_result = await db_manager.get_existing_verdict(paper_id)
                if existing_result:
                    return {
                        "success": True,
                        "paper_id": paper_id,
                        "data": existing_result,
                        "warning": "论文已评估且无更新，返回历史结果",
                        "from_cache": True
                    }

        # 4. 读取审计结果（按rule_id去重，保留最新时间戳）
        audit_records = await db_manager.fetch_audit_results(paper_id, dedup_by_rule=True)
        if not audit_records:
            raise HTTPException(status_code=404, detail=f"未找到论文 {paper_id} 的审计记录")

        logger.info(f"论文 {paper_id} 读取到 {len(audit_records)} 条审计结果（已去重）")

        # 5. 按 agent_code 分组审计结果
        audit_results_by_agent = {}
        paper_name = None

        for record in audit_records:
            agent_code = record.get("agent_code", "")
            if not paper_name:
                paper_name = record.get("paper_name")

            result_json = record.get("result_json", {})
            if isinstance(result_json, dict) and "audit_results" in result_json:
                if agent_code not in audit_results_by_agent:
                    audit_results_by_agent[agent_code] = result_json
                else:
                    existing = audit_results_by_agent[agent_code]
                    existing["audit_results"].extend(result_json.get("audit_results", []))
            else:
                if agent_code not in audit_results_by_agent:
                    audit_results_by_agent[agent_code] = {
                        "agent_code": agent_code,
                        "audit_results": []
                    }
                rule_info = rules_dict.get(record.get("rule_id", ""), {})
                audit_item = {
                    "result_id": record.get("result_id", ""),
                    "paper_id": paper_id,
                    "point": rule_info.get("rule_name_cn", record.get("rule_id", "")),
                    "rule_id": record.get("rule_id", ""),
                    "score": record.get("score_obtained", 0),
                    "level": "Critical" if rule_info.get("severity") == "CRITICAL" else "Warning",
                    "description": record.get("audit_suggestion", ""),
                    "evidence_quote": "",
                    "location": {},
                    "suggestion": record.get("audit_suggestion", ""),
                }
                if isinstance(result_json, dict):
                    audit_item.update({
                        k: v for k, v in result_json.items()
                        if k in ("evidence_quote", "location", "description", "suggestion",
                                "point", "level", "score")
                    })

                if not audit_item.get("evidence_quote") and isinstance(result_json, dict) and "evidence_quotes" in result_json:
                    quotes = result_json.get("evidence_quotes", [])
                    if quotes and len(quotes) > 0:
                        audit_item["evidence_quote"] = quotes[0]

                # 特殊处理：如果是无描述无建议的，将其设为空字符串而不是 "无描述" / "无建议"，以防幻觉检查拦截
                if audit_item["description"] in ["无描述", "无建议"]:
                    audit_item["description"] = ""
                if audit_item["suggestion"] in ["无描述", "无建议"]:
                    audit_item["suggestion"] = ""

                audit_results_by_agent[agent_code]["audit_results"].append(audit_item)

        audit_results = list(audit_results_by_agent.values())
        logger.info(f"按 agent_code 分组后有 {len(audit_results)} 个审计组的结果")

        # 6. 获取论文内容
        paper_content = await db_manager.get_paper_content(paper_id)

        # 7. 构建不全审计组的提示信息
        incomplete_note = ""
        if not has_all:
            incomplete_note = f"[注意] 审计组不全（缺少{', '.join(missing_codes)}），评估结果可能不完整。"

        # 8. 获取评估器并处理论文
        evaluator = await EvaluatorManager.get_evaluator()

        # 临时启用/禁用对话生成
        original_dialogue_setting = evaluator.enable_dialogue
        if enable_dialogue != original_dialogue_setting:
            evaluator.enable_dialogue = enable_dialogue

        result = await evaluator.process_paper(
            paper_id=paper_id,
            audit_results=audit_results,
            paper_content=paper_content,
            rules_dict=rules_dict,
            incomplete_note=incomplete_note,
            paper_name=paper_name,
            audit_records=audit_records
        )

        # 恢复原来的对话设置
        evaluator.enable_dialogue = original_dialogue_setting

        result.critical_issues = dedupe_prioritized_issues(result.critical_issues)
        result.major_issues = dedupe_prioritized_issues(result.major_issues)
        result.minor_issues = dedupe_prioritized_issues(result.minor_issues)

        # 9. 保存结果到数据库
        if "error" not in result.plugin_metadata:
            initial_score = result.plugin_metadata.get("initial_score", 0.0)
            conflict_penalty = result.plugin_metadata.get("conflict_penalty", 0.0)
            conflict_data = result.plugin_metadata.get("conflict_resolution", {})

            # 构建去重后建议
            all_issues = []
            for issue in result.critical_issues + result.major_issues + result.minor_issues:
                issue_dict = issue.model_dump() if hasattr(issue, 'model_dump') else issue
                all_issues.append(issue_dict)

            seen_descs = set()
            deduped_issues = []
            for iss in all_issues:
                desc = iss.get("description", "")
                key = issue_description_dedup_key(desc) or (desc or "").strip() or "__empty__"
                if key not in seen_descs:
                    seen_descs.add(key)
                    deduped_issues.append(iss)

            # 构建优先级排序后建议
            priority_order = {"critical": 0, "warning": 1, "error": 1, "info": 2}
            high_priority_issues = [
                iss for iss in deduped_issues
                if iss.get("priority", "info") in ("critical", "warning", "error")
            ]
            prioritized = sorted(
                high_priority_issues,
                key=lambda x: priority_order.get(x.get("priority", "info"), 99)
            )

            await db_manager.save_verdict(
                paper_id=paper_id,
                paper_name=paper_name or f"Paper_{paper_id}",
                initial_score=initial_score,
                conflict_resolution=json.dumps(conflict_data, ensure_ascii=False, default=str) if conflict_data else None,
                conflict_penalty=conflict_penalty,
                final_score=result.final_score,
                filtered_suggestions=deduped_issues,
                prioritized_suggestions=prioritized,
                final_verdict=result.verdict,
            )

        # 10. 返回结果
        result_dict = result.model_dump() if hasattr(result, 'model_dump') else dict(result)

        return {
            "success": True,
            "paper_id": paper_id,
            "paper_name": paper_name,
            "data": result_dict,
            "warning": warning_msg if warning_msg else (incomplete_note if incomplete_note else None),
            "from_cache": False
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"评估论文 {paper_id} 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"评估过程中发生错误: {str(e)}")
    finally:
        await db_manager.disconnect()

# --- API 端点 ---
@app.post("/api/evaluate", response_model=EvaluationResult)
async def evaluate_paper_by_id(request: EvaluateRequest):
    """
    核心端点：通过 paper_id 自动评估论文
    """
    try:
        result = await fetch_and_evaluate_paper(
            paper_id=request.paper_id,
            force_evaluate=request.force_evaluate,
            enable_dialogue=request.enable_dialogue
        )

        data = result["data"]

        return EvaluationResult(
            success=True,
            paper_id=request.paper_id,
            paper_name=result.get("paper_name"),
            initial_score=data.get("plugin_metadata", {}).get("initial_score"),
            final_score=data.get("final_score"),
            verdict=data.get("verdict"),
            needs_human_review=data.get("needs_human_review"),
            human_review_reason=data.get("human_review_reason"),
            critical_issues_count=len(data.get("critical_issues", [])),
            major_issues_count=len(data.get("major_issues", [])),
            minor_issues_count=len(data.get("minor_issues", [])),
            markdown_report_path=data.get("plugin_metadata", {}).get("markdown_report_path") if request.return_report_path else None,
            warning=result.get("warning")
        )

    except HTTPException as he:
        return EvaluationResult(
            success=False,
            paper_id=request.paper_id,
            error=f"HTTP错误 {he.status_code}: {he.detail}"
        )
    except Exception as e:
        return EvaluationResult(
            success=False,
            paper_id=request.paper_id,
            error=f"处理失败: {str(e)}"
        )


@app.post("/api/evaluate/inline")
async def evaluate_inline(request: InlineEvaluateRequest):
    """
    调度器 Orchestrator 调用：将已跑完的各 Agent 结果 POST 至此，由本服务执行反思评估管线
    （与数据库按 paper_id 拉取后再评估等价，均走 ReflectionOrchestrator.process_paper）。
    """
    if not request.audit_groups:
        raise HTTPException(status_code=400, detail="audit_groups 不能为空")

    await db_manager.connect()
    try:
        rules_dict = await EvaluatorManager.get_rules_dict()
        evaluator = await EvaluatorManager.get_evaluator()
        original_dialogue = evaluator.enable_dialogue
        if request.enable_dialogue != original_dialogue:
            evaluator.enable_dialogue = request.enable_dialogue
        try:
            result = await evaluator.process_paper(
                paper_id=request.paper_id,
                audit_results=request.audit_groups,
                paper_content=request.paper_content or None,
                rules_dict=rules_dict if rules_dict else None,
                incomplete_note="",
                paper_name=request.paper_title or None,
                audit_records=None,
            )
        finally:
            evaluator.enable_dialogue = original_dialogue

        result.critical_issues = dedupe_prioritized_issues(result.critical_issues)
        result.major_issues = dedupe_prioritized_issues(result.major_issues)
        result.minor_issues = dedupe_prioritized_issues(result.minor_issues)

        data = result.model_dump() if hasattr(result, "model_dump") else dict(result)
        return JSONResponse(
            content={
                "success": True,
                "paper_id": request.paper_id,
                "data": data,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("inline 反思评估失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db_manager.disconnect()


def _report_path_from_verdict_payload(verdict_data: Any, base_dir: Path) -> Optional[Path]:
    """从缓存的反思结果 metadata 中解析已保存的报告绝对/相对路径。"""
    if not verdict_data or not isinstance(verdict_data, dict):
        return None
    meta = verdict_data.get("plugin_metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            meta = {}
    if not isinstance(meta, dict):
        meta = {}
    raw = meta.get("markdown_report_path")
    if not raw:
        return None
    p = Path(raw)
    if p.is_file():
        return p
    alt = base_dir / p
    if alt.is_file():
        return alt
    return None


@app.get("/api/report/{paper_id}")
async def get_report_file(paper_id: str):
    """
    下载生成的 Markdown 评估报告（按 plugin_metadata 路径或 reports 目录下最新匹配文件）。
    """
    base_dir = Path(__file__).resolve().parent
    report_dir = base_dir / "reports"
    report_path: Optional[Path] = None

    try:
        await db_manager.connect()
        getter = getattr(db_manager, "get_existing_verdict", None)
        if callable(getter):
            try:
                verdict_data = await getter(paper_id)
            except Exception as ex:
                logger.warning("读取历史 verdict 失败: %s", ex)
                verdict_data = None
            else:
                report_path = _report_path_from_verdict_payload(verdict_data, base_dir)
    finally:
        await db_manager.disconnect()

    if report_path is None and report_dir.is_dir():
        globs = [
            f"review_report_{paper_id}_*.md",
            f"reflection_report_{paper_id}.md",
            f"report_{paper_id}.md",
            f"{paper_id}_report.md",
        ]
        candidates: List[Path] = []
        for pat in globs:
            candidates.extend(report_dir.glob(pat))
        if candidates:
            report_path = max(candidates, key=lambda p: p.stat().st_mtime)

    if report_path and report_path.is_file():
        return FileResponse(
            path=str(report_path.resolve()),
            filename=report_path.name,
            media_type="text/markdown; charset=utf-8",
        )
    raise HTTPException(status_code=404, detail=f"未找到论文 {paper_id} 的报告文件")

@app.get("/api/paper/{paper_id}/status")
async def get_paper_status(paper_id: str):
    """
    获取论文评估状态
    """
    try:
        await db_manager.connect()

        # 检查审计组是否齐全
        has_all, existing_codes = await db_manager.check_paper_has_all_agents(paper_id)
        missing_codes = list({"FMT", "REF", "EXP", "LOG"} - set(existing_codes))

        # 检查是否有评估结果
        has_verdict = False
        verdict_data = None
        try:
            verdict_data = await db_manager.get_existing_verdict(paper_id)
            has_verdict = verdict_data is not None
        except:
            pass

        # 检查是否需要重新评估
        needs_reevaluation = await db_manager.check_needs_reevaluation(paper_id)

        return {
            "paper_id": paper_id,
            "has_all_agents": has_all,
            "existing_agents": existing_codes,
            "missing_agents": missing_codes if not has_all else [],
            "has_verdict": has_verdict,
            "needs_reevaluation": needs_reevaluation,
            "can_evaluate": has_all or (not has_all and needs_reevaluation)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"检查状态失败: {str(e)}")
    finally:
        await db_manager.disconnect()

@app.post("/api/batch_evaluate")
async def batch_evaluate_papers(paper_ids: List[str], force_evaluate: bool = False, background_tasks: BackgroundTasks = None):
    """
    批量评估多篇论文
    """
    task_id = f"batch_{int(asyncio.get_event_loop().time())}"

    async def process_batch():
        results = []
        for pid in paper_ids:
            try:
                result = await fetch_and_evaluate_paper(pid, force_evaluate)
                results.append({
                    "paper_id": pid,
                    "success": True,
                    "final_score": result["data"].get("final_score"),
                    "verdict": result["data"].get("verdict")
                })
            except Exception as e:
                results.append({
                    "paper_id": pid,
                    "success": False,
                    "error": str(e)
                })

        # 这里可以添加将结果保存到文件或数据库的逻辑
        logger.info(f"批量任务 {task_id} 完成: 处理了 {len(paper_ids)} 篇论文")

    if background_tasks:
        background_tasks.add_task(process_batch)
        return {
            "message": "批量评估任务已开始后台处理",
            "task_id": task_id,
            "paper_count": len(paper_ids)
        }
    else:
        # 同步处理（不推荐大量论文）
        await process_batch()
        return {
            "message": "批量评估完成",
            "task_id": task_id,
            "paper_count": len(paper_ids)
        }

@app.get("/")
async def root():
    return {
        "service": "论文自动评估系统",
        "version": "1.0.0",
        "endpoints": {
            "单篇评估(DB)": "POST /api/evaluate",
            "调度器内联评估": "POST /api/evaluate/inline",
            "获取报告": "GET /api/report/{paper_id}",
            "检查状态": "GET /api/paper/{paper_id}/status",
            "批量评估": "POST /api/batch_evaluate"
        },
        "usage": "调度器请使用 POST /api/evaluate/inline；仅 DB 已有审计记录时用 POST /api/evaluate"
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "paper-auto-evaluator"}

# --- 主程序入口 ---
if __name__ == "__main__":
    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", "8009"))

    print(f"启动论文自动评估API服务...")
    print(f"服务地址: http://{host}:{port}")
    print(f"API文档: http://{host}:{port}/docs")
    print(f"\n使用方法:")
    print(f"1. 评估单篇论文: POST /api/evaluate")
    print(f'   JSON示例: {{"paper_id": "your_paper_id_here"}}')
    print(f"2. 获取报告文件: GET /api/report/your_paper_id")
    print(f"3. 检查论文状态: GET /api/paper/your_paper_id/status")
    print(f"\n按下 Ctrl+C 停止服务")

    uvicorn.run(app, host=host, port=port)
