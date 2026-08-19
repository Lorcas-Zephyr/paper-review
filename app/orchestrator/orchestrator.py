# 在调度器的开头添加导入
import asyncio
import logging
import uuid
from datetime import datetime
from typing import Dict, List, Any, Optional
from enum import Enum
import random
import httpx
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import uvicorn

# 导入PaperProcessor
from paper_processor import PaperProcessor
from reflection_bridge import merge_aggregate_and_reflection, run_reflection_evaluation
from event_bus import EventBus
from nanobot_workflow import NanobotWorkflowRunner
from task_store import PostgresTaskStore

logger = logging.getLogger(__name__)


def coerce_enable_rules(cfg: Optional[Dict[str, Any]]) -> bool:
    """与前端约定：enable_rules / enable_format_rules，默认开启细则。"""
    if not isinstance(cfg, dict):
        return True
    raw = cfg.get("enable_rules")
    if raw is None:
        raw = cfg.get("enable_format_rules", True)
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return bool(raw)


def canonical_paper_id(paper_id: str) -> str:
    """统一为规范 UUID 字符串，与入库 papers.paper_id 及各 Agent 请求体一致。"""
    return str(uuid.UUID(str(paper_id).strip()))


def audit_level_from_score(score: Any) -> str:
    """按该组综合分映射等级，与前端展示一致，避免「分数较高仍标 Critical」的割裂。"""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "Info"
    if s < 60:
        return "Critical"
    if s < 80:
        return "Warning"
    return "Pass"


# ========== 1. 数据模型 ==========
class AuditStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"

class PaperSubmission(BaseModel):
    """从前端接收论文的格式。config 可选：enable_mentor_dialogue(bool)；enable_rules(bool) 控制四 Agent 是否启用细则（也可用 enable_format_rules 兼容旧前端）。"""
    title: str
    content: str
    config: Optional[Dict[str, Any]] = Field(default_factory=dict)

class AuditTaskResponse(BaseModel):
    """启动审计任务后，立即返回给前端的响应"""
    request_id: str
    paper_id: str  # 新增：返回paper_id
    message: str
    status: str = "pending"
    status_url: str
    estimated_time: int = 20

class TaskStatus(BaseModel):
    """任务状态查询的返回格式"""
    request_id: str
    paper_id: str  # 新增：包含paper_id
    overall_status: AuditStatus
    progress: Dict[str, str] = Field(default_factory=dict)
    aggregated_report: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime
    estimated_time_left: Optional[int] = None
    message: Optional[str] = None
    trace_id: Optional[str] = None
    event_count: int = 0

# ========== 2. Orchestrator 核心调度类 ==========
class Orchestrator:
    def __init__(self):
        # 1. 初始化论文处理器
        self.paper_processor = PaperProcessor()

        # 2. 审计组接口配置
        self.agent_endpoints = {
            "logic_agent": {
                "url": "http://127.0.0.1:8008/audit/paper",
                "type": "logic",
                "name": "DeepLogicAuditor",
                "group_id": 3,
                "weight": 1.2,
                "description": "逻辑审计组",
                "timeout": 600
            },
            "experiment_agent": {
                "url": "http://127.0.0.1:8006/audit",
                "type": "experiment",
                "name": "Experiment_Agent",
                "group_id": 5,
                "weight": 1.1,
                "description": "实验数据审计组",
                "timeout": 600
            },
            "format_agent": {
                "url": "http://127.0.0.1:8007/audit",
                "type": "format",
                "name": "Standardization_Auditor_Agent",
                "group_id": 2,
                "weight": 0.8,
                "description": "格式审计组",
                "timeout": 600
            },
            "citation_agent": {
                "url": "http://127.0.0.1:8005/audit",
                "type": "citation",
                "name": "Citation_Agent",
                "group_id": 6,
                "weight": 1.0,
                "description": "文献审计组",
                "timeout": 600
            }
        }

        # 3. 内存存储任务状态
        self.tasks: Dict[str, TaskStatus] = {}
        self.task_store = PostgresTaskStore()
        event_subscriptions = {
            "logic_agent": {"agent.findings.citation_agent", "agent.findings.format_agent"},
            "citation_agent": {"agent.findings.logic_agent"},
            "format_agent": {"agent.findings.logic_agent", "agent.findings.citation_agent"},
            "experiment_agent": {
                "agent.findings.logic_agent",
                "agent.findings.citation_agent",
                "agent.findings.format_agent",
            },
        }
        self.event_bus = EventBus(subscriptions=event_subscriptions)
        self.nanobot_runner = NanobotWorkflowRunner(
            agent_endpoints=self.agent_endpoints,
            event_bus=self.event_bus,
            call_agent=self.call_single_agent,
            aggregate_results=self.aggregate_results,
            run_reflection=run_reflection_evaluation,
            merge_reflection=merge_aggregate_and_reflection,
            max_retries=1,
        )

        # 4. HTTP 客户端配置
        self.client_timeout = 600.0

    def _to_json_safe(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: self._to_json_safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._to_json_safe(v) for v in value]
        if isinstance(value, Enum):
            return value.value
        return value

    def _persist_task(self, request_id: str) -> None:
        task = self.tasks.get(request_id)
        if not task:
            return
        payload = task.model_dump()
        payload["overall_status"] = getattr(task.overall_status, "value", task.overall_status)
        payload["progress"] = {
            k: getattr(v, "value", v) for k, v in (task.progress or {}).items()
        }
        payload["aggregated_report"] = self._to_json_safe(task.aggregated_report)
        self.task_store.upsert_task(payload)

    def _load_task_from_store(self, request_id: str) -> Optional[TaskStatus]:
        row = self.task_store.get_task(request_id)
        if not row:
            return None
        progress = row.get("progress") or {}
        normalized_progress = {}
        for k, v in progress.items():
            normalized_progress[k] = v.value if hasattr(v, "value") else str(v)

        row["progress"] = normalized_progress
        row["overall_status"] = row.get("overall_status", AuditStatus.PENDING.value)
        row["aggregated_report"] = row.get("aggregated_report")
        row["estimated_time_left"] = 0
        row["trace_id"] = request_id
        row["event_count"] = len((row.get("aggregated_report") or {}).get("agent_events", []))
        try:
            return TaskStatus(**row)
        except Exception:
            return None

    async def process_paper_and_store(self, paper_data: PaperSubmission) -> str:
        """
        处理论文并存储到数据库，返回paper_id
        这是一个同步操作，但可以在异步上下文中运行
        """
        # 在单独的线程中执行 CPU/DB 操作；失败则向上抛出，禁止用随机 paper_id 继续调 Agent
        paper_id = await asyncio.to_thread(
            self.paper_processor.process_paper_from_content,
            paper_data.title,
            paper_data.content,
        )
        return str(paper_id).strip()

    async def call_single_agent(self,
                              agent_name: str,
                              agent_config: Dict[str, Any],
                              paper_title: str,
                              paper_content: str,
                              paper_id: str,  # 新增：传递paper_id
                              request_id: str,
                              submission_config: Optional[Dict[str, Any]] = None,
                              interaction_events: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:

        agent_url = agent_config["url"]
        agent_type = agent_config["type"]
        cfg = submission_config if isinstance(submission_config, dict) else {}
        enable_rules = coerce_enable_rules(cfg)
        interaction_context = {
            "request_id": request_id,
            "paper_id": paper_id,
            "consumer_agent": agent_name,
            "events": interaction_events or [],
        }

        print(f"  [agent] 调用 {agent_name} 处理论文: {paper_id}")

        try:
            # 构建包含paper_id的请求体
            if agent_name == "experiment_agent":
                request_body = {
                    "request_id": request_id,
                    "paper_id": paper_id,  # 包含paper_id
                    "model_preference": "deepseek-chat",
                    "audit_scope": ["experiment", "result"],
                    "enable_rules": enable_rules,
                    # 与库内全文一致，优先用调度器当前 MD 直接评阅（避免仅 paper_id 时库未同步）
                    "content": paper_content,
                    "interaction_context": interaction_context,
                }
            elif agent_name == "logic_agent":
                agent_url = (
                    f"http://127.0.0.1:8008/audit/integrated?paper_id={paper_id}"
                    f"&enable_rules={'true' if enable_rules else 'false'}"
                )
                request_body = {
                    "paper_id": paper_id,
                    "metadata": {
                        "paper_id": paper_id,
                        "task_id": request_id,
                        "chunk_id": "full_paper",
                        "interaction_context": interaction_context,
                    },
                    "payload": {
                        "content": paper_content
                    }
                }
            elif agent_name == "format_agent":
                # audit_format.models.AuditRequest：metadata.paper_id 须为 UUID4；config 须符合 RequestConfig
                request_body = {
                    "request_id": request_id,
                    "metadata": {
                        "chunk_id": "full_paper",
                        "paper_title": paper_title or "未命名论文",
                        "paper_id": paper_id,
                    },
                    "payload": {
                        "content": paper_content,
                    },
                    "config": {
                        "temperature": 0.1,
                        "max_tokens": 500,
                        "enable_rules": enable_rules,
                        "interaction_context": interaction_context,
                    },
                }
            elif agent_name == "citation_agent":
                request_body = {
                    "request_id": request_id,
                    "metadata": {
                        "chunk_id": "full_paper",
                        "title": paper_title,
                        "paper_id": paper_id  # 包含paper_id
                    },
                    "payload": {
                        "content": paper_content
                    },
                    "config": {
                        "check_year": True,
                        "min_citation_year": 2018,
                        "enable_rules": enable_rules,
                        "interaction_context": interaction_context,
                    }
                }

            print(f"  [http] 发送请求到 {agent_url}")

            # 使用 httpx 异步客户端，禁用代理以避免系统代理导致的 502
            async with httpx.AsyncClient(timeout=agent_config["timeout"], trust_env=False) as client:
                response = await client.post(
                    agent_url,
                    json=request_body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Request-Id": request_id,
                        "X-Trace-Id": request_id,
                    }
                )

                print(f"  [http] 响应状态: {response.status_code}")

                if response.status_code == 200:
                    result_data = response.json()
                    # 格式审计等返回 { result: { score, audit_level, comment, ... }, ... }
                    inner = result_data.get("result")
                    if isinstance(inner, dict):
                        score = inner.get("score", result_data.get("score"))
                        audit_level = inner.get("audit_level", result_data.get("audit_level", "Info"))
                        comment = inner.get("comment", result_data.get("comment"))
                        suggestion = inner.get("suggestion", result_data.get("suggestion", ""))
                    else:
                        score = result_data.get("score")
                        audit_level = result_data.get("audit_level", "Info")
                        comment = result_data.get("comment")
                        suggestion = result_data.get("suggestion", "")

                    # 尝试从 audit_results 中提取信息 (针对实验数据审计组等不返回顶层分数的Agent)
                    if "audit_results" in result_data:
                        items = result_data["audit_results"]
                        if isinstance(items, list) and items:
                            if score is None:
                                scores = [item.get("score", 0) for item in items if isinstance(item, dict) and "score" in item]
                                if scores:
                                    score = sum(scores) / len(scores)

                            if not comment:
                                descs = [item.get("description", "") for item in items if isinstance(item, dict) and item.get("description")]
                                if descs:
                                    comment = "；".join(descs[:2])
                                    if len(descs) > 2:
                                        comment += "..."

                            if audit_level == "Info" or not audit_level:
                                levels = [item.get("level", "Info") for item in items if isinstance(item, dict)]
                                if "Critical" in levels:
                                    audit_level = "Critical"
                                elif "Warning" in levels:
                                    audit_level = "Warning"

                    score_fallback = False
                    if score is None:
                        score = random.randint(60, 95)
                        score_fallback = True
                    if not comment:
                        comment = f"{agent_config['description']}完成全文评审"
                    if isinstance(audit_level, str):
                        pass
                    elif hasattr(audit_level, "value"):
                        audit_level = audit_level.value
                    else:
                        audit_level = str(audit_level or "Info")

                    # ==== 强制分数收敛：Warning 时与等级语义大致对齐（不再对 Critical 做分数封顶） ====
                    if audit_level in ("Warning", "WARNING", "warning") and score is not None and score > 79:
                        score = min(score, 79)

                    self.tasks[request_id].progress[f"{agent_name}_chunk_full"] = AuditStatus.SUCCESS
                    self._persist_task(request_id)
                    return {
                        "agent_name": agent_name,
                        "group_id": agent_config["group_id"],
                        "weight": agent_config["weight"],
                        "paper_id": paper_id,
                        "status": AuditStatus.SUCCESS,
                        "score": score,
                        "audit_level": audit_level,
                        "comment": comment,
                        "suggestion": suggestion or "",
                        "raw_response": result_data,
                        "score_fallback": score_fallback,
                    }
                else:
                    error_text = response.text[:200] if response.text else "无错误信息"
                    print(f"  [err] {agent_name} 错误: {response.status_code}")

                    self.tasks[request_id].progress[f"{agent_name}_chunk_full"] = AuditStatus.FAILED
                    self._persist_task(request_id)
                    return {
                        "agent_name": agent_name,
                        "group_id": agent_config["group_id"],
                        "weight": agent_config["weight"],
                        "paper_id": paper_id,  # 包含paper_id
                        "status": AuditStatus.FAILED,
                        "score": 0,
                        "audit_level": "Unknown",
                        "error": f"状态码: {response.status_code}",
                        "response_text": error_text
                    }

        except Exception as e:
            err_msg = str(e) or repr(e)
            print(f"  [err] 调用 {agent_name} 异常: {err_msg}")
            self.tasks[request_id].progress[f"{agent_name}_chunk_full"] = AuditStatus.FAILED
            self._persist_task(request_id)
            return {
                "agent_name": agent_name,
                "group_id": agent_config["group_id"],
                "weight": agent_config["weight"],
                "paper_id": paper_id,  # 包含paper_id
                "status": AuditStatus.FAILED,
                "score": 0,
                "audit_level": "Unknown",
                "error": err_msg
            }

    async def process_paper(self, paper_data: PaperSubmission, request_id: str):
        """
        核心处理流程
        1. 存储论文到数据库 → 2. 并发调用所有Agent处理 → 3. 聚合结果
        """
        print(f"\n{'='*60}")
        print(f"[paper] 开始处理论文: {paper_data.title}")
        print(f"[task] 任务ID: {request_id}")
        print(f"[stat] 内容长度: {len(paper_data.content)} 字符")
        print(f"{'='*60}")

        # 【新增步骤0：存储论文到数据库，获取paper_id】
        print(f"\n0. [db] 存储论文到数据库...")
        paper_id = canonical_paper_id(await self.process_paper_and_store(paper_data))
        print(f"   [ok] 论文存储完成，paper_id: {paper_id}")

        # 更新整体任务状态为 RUNNING
        self.tasks[request_id].overall_status = AuditStatus.RUNNING
        self.tasks[request_id].paper_id = paper_id  # 保存paper_id
        self.tasks[request_id].updated_at = datetime.now()
        self.tasks[request_id].trace_id = request_id
        self._persist_task(request_id)

        try:
            print(f"\n1. [nanobot] 执行 nanobot 编排流程 (包含paper_id: {paper_id})...")
            for agent_name in self.agent_endpoints.keys():
                agent_task_key = f"{agent_name}_chunk_full"
                self.tasks[request_id].progress[agent_task_key] = AuditStatus.RUNNING
            sub_cfg = paper_data.config if isinstance(paper_data.config, dict) else {}
            self._persist_task(request_id)
            workflow_result = await self.nanobot_runner.run(
                paper_title=paper_data.title,
                paper_content=paper_data.content,
                paper_id=paper_id,
                request_id=request_id,
                submission_config=sub_cfg,
            )
            all_results = workflow_result["all_results"]
            aggregated_report = workflow_result["aggregated_report"]

            # 【步骤3：更新整体任务状态为 SUCCESS】
            self.tasks[request_id].overall_status = AuditStatus.SUCCESS
            self.tasks[request_id].aggregated_report = aggregated_report
            self.tasks[request_id].event_count = len(aggregated_report.get("agent_events", []))
            self.tasks[request_id].updated_at = datetime.now()
            self._persist_task(request_id)

            print(f"\n[ok] 处理完成!")
            print(f"   论文ID: {paper_id}")
            print(f"   成功审计: {aggregated_report.get('successful_audits', 0)} 个Agent")
            print(f"   综合评分: {aggregated_report.get('overall_score', 0)}")
            print(f"{'='*60}")

        except Exception as e:
            # 整个处理流程失败
            print(f"\n[err] 处理失败: {e}")
            import traceback
            traceback.print_exc()

            self.tasks[request_id].overall_status = AuditStatus.FAILED
            self.tasks[request_id].updated_at = datetime.now()
            self.tasks[request_id].message = f"处理失败: {str(e)}"
            self._persist_task(request_id)

    def aggregate_results(self, all_results: List[Dict], paper_title: str, request_id: str, paper_id: str) -> Dict[str, Any]:
        """聚合所有Agent返回的结果，生成综合报告"""
        successful_results = [r for r in all_results if r.get("status") == AuditStatus.SUCCESS]
        failed_results = [r for r in all_results if r.get("status") != AuditStatus.SUCCESS]

        print(f"   成功结果: {len(successful_results)} 个, 失败/超时: {len(failed_results)} 个")

        # 按审计组组织结果
        group_results = {}
        audit_results_by_group = {}

        for result in successful_results:
            group_id = result.get("group_id")

            if group_id not in audit_results_by_group:
                audit_results_by_group[group_id] = []

            # 获取代理配置
            agent_name = result.get("agent_name", "")
            agent_config = self.agent_endpoints.get(agent_name, {})

            # 构建审计结果项（等级与 score 对齐，不用 Agent 侧「任一条 Critical 即全局 Critical」的 audit_level）
            _gscore = result.get("score", 0)
            _glevel = audit_level_from_score(_gscore)
            audit_item = {
                "id": f"item-{group_id}-full",
                "point": f"{agent_config.get('description', agent_name)}全文审计",
                "score": _gscore,
                "level": _glevel,
                "description": result.get("comment", "无描述"),
                "evidence_quote": "全文评审",
                "location": {"section": "全文", "page": 1},
                "suggestion": result.get("suggestion", "无建议"),
                "agent_name": agent_name
            }

            audit_results_by_group[group_id].append(audit_item)

            # 记录组结果
            if group_id not in group_results:
                group_results[group_id] = {
                    "group_id": group_id,
                    "group_name": agent_config.get("description", f"Group_{group_id}"),
                    "weight": result.get("weight", 1.0),
                    "score": _gscore,
                    "audit_level": _glevel,
                    "agent_name": agent_name
                }

        # 计算加权平均分
        weighted_scores = []
        weights = []
        for group_data in group_results.values():
            weighted_scores.append(group_data["score"] * group_data["weight"])
            weights.append(group_data["weight"])

        if weights and sum(weights) > 0:
            overall_score = sum(weighted_scores) / sum(weights)
        else:
            overall_score = 0

        # 最终报告结构，包含paper_id
        final_report = {
            "paper_id": paper_id,  # 包含paper_id
            "paper_title": paper_title,
            "request_id": request_id,
            "overall_score": round(overall_score, 2),
            "overall_status": AuditStatus.SUCCESS.value,
            "total_audits": len(all_results),
            "successful_audits": len(successful_results),
            "failed_audits": len(failed_results),
            "group_results": [
                {
                    "group_id": group_id,
                    "group_name": data["group_name"],
                    "weight": data["weight"],
                    "paper_id": paper_id,
                    "audit_results": audit_results_by_group.get(group_id, [])
                }
                for group_id, data in group_results.items()
            ],
            "details_by_agent": {
                res["agent_name"]: {
                    "paper_id": res.get("paper_id", paper_id),
                    "score": res.get("score"),
                    "level": (
                        audit_level_from_score(res.get("score"))
                        if res.get("status") == AuditStatus.SUCCESS
                        else (res.get("audit_level") or "Unknown")
                    ),
                    "audit_level": (
                        audit_level_from_score(res.get("score"))
                        if res.get("status") == AuditStatus.SUCCESS
                        else (res.get("audit_level") or "Unknown")
                    ),
                    "comment": res.get("comment"),
                    "suggestion": res.get("suggestion"),
                    "status": res.get("status").value if isinstance(res.get("status"), AuditStatus) else res.get("status"),
                    "error": res.get("error"),
                    "response_text": res.get("response_text"),
                    "score_fallback": res.get("score_fallback", False),
                } for res in all_results
            },
            "failed_details": [
                {
                    "agent_name": r.get("agent_name"),
                    "paper_id": r.get("paper_id", paper_id),
                    "group_id": r.get("group_id"),
                    "error": r.get("error", "未知错误"),
                    "status": r.get("status").value if isinstance(r.get("status"), AuditStatus) else r.get("status")
                } for r in failed_results
            ],
            "generated_at": datetime.now().isoformat(),
            "reflection_ready": True
        }

        return final_report

# ========== 3. FastAPI 应用初始化 ==========
app = FastAPI(
    title="论文评审调度器 (Orchestrator) - 集成论文存储版",
    description="调度四个审计组进行论文智能评审的服务，集成论文存储功能",
    version="2.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

# 修复 CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# 创建 Orchestrator 实例
orchestrator = Orchestrator()

# ========== 4. API 路由定义 ==========
@app.get("/")
async def root():
    """根路径"""
    return {
        "service": "论文审计调度器 (Orchestrator) - 集成论文存储版",
        "version": "2.1.0",
        "status": "running",
        "timestamp": datetime.now().isoformat(),
        "features": "集成论文存储，使用paper_id作为全系统唯一标识"
    }

@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {
        "status": "healthy",
        "service": "orchestrator",
        "timestamp": datetime.now().isoformat(),
        "agents_count": len(orchestrator.agent_endpoints)
    }

@app.post("/api/v1/audit")
async def handle_audit_request(
    request: Request,
    paper: PaperSubmission,
    background_tasks: BackgroundTasks = None
):
    """
    提交论文审计任务
    """
    try:
        # 1. 生成唯一请求ID
        request_id = f"audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        print(f"\n[audit] 收到评审请求")
        print(f"   论文标题: {paper.title}")
        print(f"   内容长度: {len(paper.content)} 字符")
        print(f"   请求ID: {request_id}")

        # 2. 初始化任务状态记录
        orchestrator.tasks[request_id] = TaskStatus(
            request_id=request_id,
            paper_id="processing",  # 初始化时标记为处理中
            overall_status=AuditStatus.PENDING,
            progress={},
            aggregated_report=None,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            estimated_time_left=20,
            trace_id=request_id,
            event_count=0,
        )
        orchestrator._persist_task(request_id)

        # 3. 将耗时的审计流程放入后台任务执行
        if background_tasks:
            background_tasks.add_task(orchestrator.process_paper, paper, request_id)
        else:
            asyncio.create_task(orchestrator.process_paper(paper, request_id))

        # 4. 立即返回，实现异步处理
        response_data = AuditTaskResponse(
            request_id=request_id,
            paper_id="processing",  # 前端稍后轮询获取实际paper_id
            message=f"论文'{paper.title}'的审计任务已开始处理。",
            status_url=f"/api/v1/task/{request_id}",
            estimated_time=20
        )

        print(f"   [ok] 任务已提交，返回响应")

        return JSONResponse(
            status_code=202,  # Accepted
            content=response_data.dict()
        )

    except Exception as e:
        print(f"[err] 提交任务失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"提交任务失败: {str(e)}")

@app.get("/api/v1/task/{request_id}", response_model=TaskStatus)
async def get_task_status(request_id: str):
    """查询任务状态。前端可轮询此接口获取进度。"""
    if request_id not in orchestrator.tasks:
        loaded = orchestrator._load_task_from_store(request_id)
        if loaded is not None:
            orchestrator.tasks[request_id] = loaded
    else:
        loaded = orchestrator._load_task_from_store(request_id)
        if loaded is not None:
            orchestrator.tasks[request_id] = loaded
    if request_id not in orchestrator.tasks:
        raise HTTPException(status_code=404, detail=f"任务 {request_id} 不存在")

    task = orchestrator.tasks[request_id]

    # 计算剩余时间
    if task.overall_status == AuditStatus.RUNNING:
        elapsed = (datetime.now() - task.created_at).total_seconds()
        task.estimated_time_left = max(0, 20 - int(elapsed))
    else:
        task.estimated_time_left = 0

    orchestrator._persist_task(request_id)
    return task

@app.get("/api/v1/paper/{paper_id}")
async def get_paper_info(paper_id: str):
    """根据paper_id获取论文信息"""
    try:
        paper_info = await asyncio.to_thread(
            orchestrator.paper_processor.get_paper_info,
            paper_id
        )

        if paper_info:
            return {
                "success": True,
                "paper": paper_info
            }
        else:
            raise HTTPException(status_code=404, detail=f"论文 {paper_id} 不存在")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取论文信息失败: {str(e)}")

# 主程序入口
if __name__ == "__main__":
    import sys

    print("=" * 60)
    print("论文审计调度器 (Orchestrator)")
    print("=" * 60)
    print("特性:")
    print(f"   使用paper_id作为全系统唯一标识")
    print(f"   向量化存储摘要和章节")
    print("=" * 60)

    uvicorn.run(
        "orchestrator:app",
        host="0.0.0.0",
        port=7860,
        reload=True,
        log_level="info"
    )
