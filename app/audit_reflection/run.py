"""
反思评估组主运行程序
支持两种运行方案：
1. 从PostgreSQL数据库读取agent_audit_result表
2. 从prompts文件夹读取JSON文件

Week3更新：
- 输入表从agent_audits改为agent_audit_result
- 输出表从agent_audits改为reflect_agent_verdict
- 从数据库读取评审规则（main_rules + rule_judge）
- 自动检查4个审计组是否齐全
- 自动检查是否已评估/需要重新评估
- 支持--paper-id强制评估（忽略审计组不全）
"""
import asyncio
import json
import logging
import sys
import warnings
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
import argparse

# 抑制SSL相关的警告
warnings.filterwarnings('ignore', category=ResourceWarning)
warnings.filterwarnings('ignore', message='.*SSL.*')

# 重定向stderr以抑制SSL错误（在导入其他模块之前）
class SuppressSSLErrors:
    """抑制SSL相关的stderr输出"""
    def __init__(self):
        self.original_stderr = sys.stderr
        self.null = open(os.devnull, 'w')

    def __enter__(self):
        sys.stderr = self
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stderr = self.original_stderr
        self.null.close()

    def write(self, text):
        # 只过滤SSL相关的错误
        if 'SSL' in text or 'ssl' in text.lower() or '_SSLProtocolTransport' in text:
            return
        self.original_stderr.write(text)

    def flush(self):
        self.original_stderr.flush()

# 将 audit_reflection 包根目录加入 Python 路径（供 src.* 导入）
sys.path.insert(0, str(Path(__file__).parent))

from src.db import db_manager
from src.common.models import ReflectionResult
from src.common.utils_b import dedupe_prioritized_issues
from src.common.severity_calibration import recalibrate_prioritized_issue_buckets
from src.common.thesis_grade_verdict import build_reflection_verdict
from src.common.report_generator import report_generator
from src.conflict_resolution import ConflictResolver
from src.deduplication import Deduplicator
from src.evidence_validation import EvidenceValidator
from src.dialogue_generation import DialogueEngine
from src.priority_sorting import ReviewDecisionEngine
from src.api.deepseek_client import deepseek_client

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 抑制asyncio的SSL错误日志
logging.getLogger('asyncio').setLevel(logging.CRITICAL)

# ---------- 反思综合分校准（与四组 Agent 口径对齐，避免 inline 无 audit_records 时过度压分）----------
_REF_CONFLICT_CRIT = 2.0
_REF_CONFLICT_WARN = 0.75
_REF_CONFLICT_CAP = 12.0
_REF_EVIDENCE_FACTOR = 9.0
_REF_EVIDENCE_CAP = 10.0
_REF_BLEND_NO_RECORDS = 0.58
_REF_BLEND_WITH_RECORDS = 0.22
_REF_HINT_SOFT_FLOOR = 10.0


def _extract_agent_hint_score(audit_results: Optional[List[Dict[str, Any]]]) -> Optional[float]:
    """
    从各组 audit_results 中 item.score（0–100）估计与调度器展示相近的综合倾向。
    orchestrator 走 /api/evaluate/inline 时常无 audit_records，必须用此与四组高分对齐。
    """
    if not audit_results:
        return None
    group_means: List[float] = []
    for grp in audit_results:
        if not isinstance(grp, dict):
            continue
        items = grp.get("audit_results")
        if not isinstance(items, list) or not items:
            continue
        vals: List[float] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            raw = it.get("score")
            if raw is None:
                continue
            try:
                v = float(raw)
            except (TypeError, ValueError):
                continue
            if 0.0 <= v <= 100.0:
                vals.append(v)
        if vals:
            group_means.append(sum(vals) / len(vals))
    if not group_means:
        return None
    return round(sum(group_means) / len(group_means), 2)


def custom_exception_handler(loop, context):
    """自定义异常处理器，抑制SSL transport错误"""
    exception = context.get('exception')
    message = context.get('message', '')

    # 忽略SSL相关的错误
    if exception:
        if 'SSL' in str(exception) or 'ssl' in str(exception).lower():
            return
    if 'SSL' in message or 'ssl' in message.lower():
        return

    # 其他错误正常记录
    if exception:
        logger.error(f"Asyncio exception: {exception}", exc_info=exception)
    else:
        logger.error(f"Asyncio error: {message}")


class ReflectionOrchestrator:
    """反思评估编排器"""

    def __init__(self, mode: str = "database", enable_dialogue: bool = True,
                 always_use_llm: bool = False, enable_hallucination_filter: bool = True):
        self.conflict_resolver = None
        self.deduplicator = None
        self.evidence_validator = None
        self.dialogue_engine = None
        self.review_engine = None
        self.mode = mode  # "database" or "file"
        self.enable_dialogue = enable_dialogue  # 是否启用导师对话生成
        self.always_use_llm = always_use_llm  # 是否始终使用LLM裁决
        self.enable_hallucination_filter = enable_hallucination_filter  # 是否启用幻觉过滤（证据验证）

    def initialize_modules(self):
        """初始化各模块（延迟初始化以避免导入错误）"""
        try:
            self.conflict_resolver = ConflictResolver(
                mode=self.mode, always_use_llm=self.always_use_llm,
                enable_hallucination_filter=self.enable_hallucination_filter
            )
            self.deduplicator = Deduplicator()
            self.evidence_validator = EvidenceValidator()
            self.dialogue_engine = DialogueEngine()
            self.review_engine = ReviewDecisionEngine()
            logger.info("所有模块初始化成功")
            if self.always_use_llm:
                logger.info("⚠️ 已启用纯LLM-as-a-Judge模式（always_use_llm=True），所有评审都将调用DeepSeek API")
        except Exception as e:
            logger.error(f"模块初始化失败: {e}")
            raise

    async def process_paper(
        self,
        paper_id: str,
        audit_results: List[Dict[str, Any]],
        paper_content: Optional[str] = None,
        rules_dict: Optional[Dict[str, Any]] = None,
        incomplete_note: str = "",
        paper_name: Optional[str] = None,
        audit_records: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        处理单篇论文的评审

        Args:
            paper_id: 论文ID
            audit_results: 审计组的结果列表（按agent_code分组的result_json）
            paper_content: 论文内容（用于证据验证）
            rules_dict: 评审规则字典（从数据库读取）
            incomplete_note: 审计组不全的提示信息
            paper_name: 论文题目
            audit_records: 原始审计记录（用于计算initial_score和构建suggestions）

        Returns:
            反思评估结果
        """
        logger.info(f"开始处理论文: {paper_id}")
        paper_title = paper_name or f"Paper_{paper_id}"

        try:
            # 验证输入：确保有4个审计组的结果（FMT/REF/EXP/LOG）
            if len(audit_results) < 4:
                logger.warning(f"论文{paper_id}的审计结果数量不足4个，实际为{len(audit_results)}个")

            # ========== 计算 initial_score ==========
            # initial_score = 各审计agent不同rule的score_obtained之和
            initial_score = 0.0
            total_full_score = 0.0
            if audit_records:
                for rec in audit_records:
                    score_val = rec.get("score_obtained")
                    if score_val is not None:
                        try:
                            initial_score += float(score_val)
                        except (TypeError, ValueError):
                            pass
                    # 累加满分用于归一化
                    rule_id = rec.get("rule_id", "")
                    if rules_dict and rule_id in rules_dict:
                        fs = rules_dict[rule_id].get("full_score")
                        if fs is not None:
                            try:
                                total_full_score += float(fs)
                            except (TypeError, ValueError):
                                pass
            logger.info(f"initial_score(原始得分和)={initial_score}, total_full_score={total_full_score}")

            # 步骤1：优先级排序和复核标记
            logger.info("步骤1: 优先级排序和复核标记")
            sorted_results, review_marks = self.review_engine.process_audit_results(audit_results)

            # 步骤2：冲突裁决
            logger.info("步骤2: 冲突裁决")
            from src.common.models import ConflictResolutionRequest

            conflict_request = ConflictResolutionRequest(
                metadata={"paper_id": paper_id, "paper_title": paper_title},
                payload={"agent_results": audit_results}
            )
            conflict_response = await self.conflict_resolver.resolve_conflicts(conflict_request)

            from src.common.severity_calibration import (
                calibrate_resolved_issues,
                strip_compliance_praise_findings,
            )

            _raw_resolved = conflict_response.result.get("resolved_issues") or []
            resolved_issues = strip_compliance_praise_findings(
                calibrate_resolved_issues(_raw_resolved)
            )
            if hasattr(conflict_response, "result") and isinstance(conflict_response.result, dict):
                conflict_response.result["resolved_issues"] = resolved_issues

            # ========== 计算 conflict_penalty ==========
            conflict_penalty = 0.0
            for issue in resolved_issues:
                level = issue.get("final_level", "Info")
                if level == "Critical":
                    conflict_penalty += _REF_CONFLICT_CRIT
                elif level == "Warning":
                    conflict_penalty += _REF_CONFLICT_WARN
                # Info不扣分
            conflict_penalty = min(conflict_penalty, _REF_CONFLICT_CAP)
            logger.info(f"conflict_penalty={conflict_penalty} (capped)")

            # ========== 计算 final_score ==========
            # 归一化到100分制
            if total_full_score > 0:
                normalized_score = (initial_score / total_full_score) * 100.0
            else:
                # 无满分信息时，使用conflict_resolver的加权平均分
                final_verdict_data = conflict_response.result.get("final_verdict", {})
                normalized_score = final_verdict_data.get("average_score", 70.0)

            # 扣除冲突惩罚
            final_score = max(0.0, normalized_score - conflict_penalty)

            # 证据验证调整（仅在启用幻觉过滤时）
            evidence_validation = conflict_response.result.get("evidence_validation", {})
            if self.enable_hallucination_filter and evidence_validation:
                validation_score = evidence_validation.get("validation_score", 1.0)
                if validation_score < 0.7:
                    ev_penalty = min(
                        (0.7 - float(validation_score)) * _REF_EVIDENCE_FACTOR,
                        _REF_EVIDENCE_CAP,
                    )
                    final_score = max(0.0, final_score - ev_penalty)
                    logger.info(f"证据验证扣分: -{ev_penalty:.1f} (validation_score={validation_score:.2f})")
                elif validation_score > 0.9:
                    ev_bonus = (validation_score - 0.9) * 10  # 最多加1分
                    final_score = min(100.0, final_score + ev_bonus)
                    logger.info(f"证据验证加分: +{ev_bonus:.1f} (validation_score={validation_score:.2f})")
            elif not self.enable_hallucination_filter:
                logger.info("幻觉过滤已禁用，不纳入证据验证调整")

            final_score = round(final_score, 1)

            agent_hint = _extract_agent_hint_score(audit_results)
            blend_w = _REF_BLEND_NO_RECORDS if total_full_score <= 0 else _REF_BLEND_WITH_RECORDS
            if agent_hint is not None:
                pre_b = final_score
                final_score = round((1.0 - blend_w) * final_score + blend_w * agent_hint, 1)
                logger.info(
                    "agent_hint=%.2f blend_w=%.2f pre_blend=%.1f -> blended=%.1f",
                    agent_hint,
                    blend_w,
                    pre_b,
                    final_score,
                )
                if agent_hint >= 75.0:
                    soft_floor = agent_hint - _REF_HINT_SOFT_FLOOR
                    if final_score < soft_floor:
                        final_score = round(min(100.0, soft_floor), 1)
                        logger.info("hint soft_floor applied: final_score=%.1f", final_score)

            logger.info(f"final_score={final_score} (normalized={normalized_score:.1f}, penalty={conflict_penalty})")

            # 步骤3: 提取优先级问题（来自冲突裁决）
            logger.info("步骤3: 提取优先级问题")
            critical_issues = []
            major_issues = []
            minor_issues = []

            for issue in resolved_issues:
                from src.common.models import PrioritizedIssue
                priority_issue = PrioritizedIssue(
                    description=issue.get("resolved_comment", ""),
                    priority=issue.get("final_level", "Info").lower(),
                    agents=[issue.get("agent1_name", ""), issue.get("agent2_name", "")],
                    evidence=issue.get("root_cause", "")
                )

                if issue.get("final_level") == "Critical":
                    critical_issues.append(priority_issue)
                elif issue.get("final_level") == "Warning":
                    major_issues.append(priority_issue)
                else:
                    minor_issues.append(priority_issue)

            # 步骤3.5: 从原始审计记录中补充审计建议到issues列表
            if audit_records:
                for rec in audit_records:
                    suggestion = rec.get("audit_suggestion", "")
                    if not suggestion or not suggestion.strip():
                        continue

                    # 避免将占位符建议作为严重问题
                    if suggestion.strip() in ["无建议", "无需修改", "无修改建议"]:
                        continue

                    is_compliant = rec.get("is_compliant")
                    agent_code = rec.get("agent_code", "")
                    rule_id = rec.get("rule_id", "")
                    score_obtained = rec.get("score_obtained", 0)

                    # 确定优先级
                    rule_info = rules_dict.get(rule_id, {}) if rules_dict else {}
                    severity = rule_info.get("severity", "")
                    full_score = rule_info.get("full_score", 0)
                    rule_name = rule_info.get("rule_name_cn", rule_id)

                    # 不合规的条目才作为issue
                    if is_compliant:
                        continue

                    from src.common.models import PrioritizedIssue
                    issue_item = PrioritizedIssue(
                        description=f"[{rule_name}] {suggestion}",
                        priority="critical" if severity == "CRITICAL" else ("warning" if severity == "MAJOR" else "info"),
                        agents=[agent_code],
                        evidence=f"得分: {score_obtained}/{full_score}" if full_score else f"得分: {score_obtained}"
                    )

                    if severity == "CRITICAL":
                        critical_issues.append(issue_item)
                    elif severity == "MAJOR":
                        major_issues.append(issue_item)
                    else:
                        minor_issues.append(issue_item)

            # 从 sorted_results 添加 issues，补全因为没有 audit_records 而缺失的条目
            for s_res in sorted_results:
                if s_res.problem_level in ["Critical", "Major", "Minor", "Warning"]:
                    priority = "critical" if s_res.problem_level == "Critical" else ("warning" if s_res.problem_level in ["Major", "Warning"] else "info")

                    # 检查是否已存在
                    desc_prefix = f"[{s_res.audit_point}]"
                    exists = False
                    for existing_list in [critical_issues, major_issues, minor_issues]:
                        for iss in existing_list:
                            if iss.description.startswith(desc_prefix):
                                exists = True
                                break
                        if exists:
                            break

                    if not exists:
                        # 查找原始描述（须与当前 sorted 条目的审计组一致，且 result_id 对齐）
                        original_desc = ""
                        agent = (s_res.audit_agent or "").strip()
                        for ar in audit_results:
                            if not isinstance(ar, dict) or "audit_results" not in ar:
                                continue
                            gname = (ar.get("group_name") or "").strip()
                            if agent and gname and gname != agent:
                                continue
                            for item in ar.get("audit_results", []):
                                rid = (item.get("result_id") or item.get("id") or "").strip()
                                if rid == (s_res.result_id or "").strip():
                                    original_desc = item.get("description", "") or item.get("suggestion", "")
                                    break
                            if original_desc:
                                break

                        if original_desc and original_desc not in ["无描述", "无建议"]:
                            from src.common.models import PrioritizedIssue
                            issue_item = PrioritizedIssue(
                                description=f"[{s_res.audit_point}] {original_desc}",
                                priority=priority,
                                agents=[s_res.audit_agent],
                                evidence=f"级别: {s_res.problem_level}"
                            )
                            if priority == "critical":
                                critical_issues.append(issue_item)
                            elif priority == "warning":
                                major_issues.append(issue_item)
                            else:
                                minor_issues.append(issue_item)

            # 冲突裁决已校准的条目外，audit_records / sorted_results 汇入的 Critical 需统一再校准
            critical_issues, major_issues, minor_issues = recalibrate_prioritized_issue_buckets(
                critical_issues, major_issues, minor_issues
            )

            # 步骤4: 生成导师对话（可选）
            mentor_dialogue = None
            if self.enable_dialogue:
                logger.info("步骤4: 生成导师对话")
                all_issues = critical_issues + major_issues + minor_issues
                if all_issues:
                    try:
                        mentor_dialogue = await self.dialogue_engine.generate_dialogue("软件工程", all_issues)
                    except Exception as e:
                        logger.warning(f"导师对话生成失败: {e}")
            else:
                logger.info("步骤4: 跳过导师对话生成（未启用）")

            # 步骤5: 确定是否需要人工复核
            needs_human_review = len(review_marks) > 0
            human_review_reason = None
            if needs_human_review:
                reasons = [mark.trigger_reason for mark in review_marks[:3]]
                human_review_reason = "; ".join(reasons)

            # 补齐：如果因为幻觉剔除导致没有留下任何 issues，但原本存在需要关注的问题
            if not critical_issues and not major_issues and not minor_issues:
                # 如果没有从 audit_records 拿到数据，但是 review_marks 里有东西
                if len(review_marks) > 0:
                    human_review_reason = f"系统检测到潜在问题但可能由于缺乏有效证据而被全部剔除。原因示例: {human_review_reason}"

                # 检查 evidence_validation 的剔除情况
                evidence_enforcement = conflict_response.result.get("evidence_enforcement", {})
                if evidence_enforcement and evidence_enforcement.get("removed_count", 0) > 0:
                    removed_count = evidence_enforcement.get("removed_count")
                    needs_human_review = True
                    reason_suffix = f"有 {removed_count} 个Warning/Critical级别的问题因为缺乏证据引用被系统剔除（防幻觉）。建议人工查阅原文验证是否存在这些问题。"
                    if human_review_reason:
                        human_review_reason += "; " + reason_suffix
                    else:
                        human_review_reason = reason_suffix

            # 步骤6: 构建最终结果（综合质量等级按百分制四档：优秀≥90 / 良好80–89 / 一般70–79 / 较差<70）
            has_critical = any(i.priority == "critical" for i in critical_issues)
            verdict = build_reflection_verdict(
                final_score,
                has_critical=has_critical,
                major_issue_count=len(major_issues),
                incomplete_note=incomplete_note or "",
            )

            # 按「去掉 [审计点] 前缀后的正文」去重，避免多 Agent 同一条问题重复展示
            critical_issues = dedupe_prioritized_issues(critical_issues)
            major_issues = dedupe_prioritized_issues(major_issues)
            minor_issues = dedupe_prioritized_issues(minor_issues)

            # 使用 ReflectionResult 模型
            result = ReflectionResult(
                paper_id=paper_id,
                final_score=final_score,
                verdict=verdict,
                critical_issues=critical_issues,
                major_issues=major_issues,
                minor_issues=minor_issues,
                needs_human_review=needs_human_review,
                human_review_reason=human_review_reason,
                mentor_dialogue=mentor_dialogue,
                plugin_metadata={
                    "paper_title": paper_title,
                    "conflict_resolution": conflict_response.result,
                    "initial_score": initial_score,
                    "total_full_score": total_full_score,
                    "agent_hint_score": agent_hint,
                    "reflection_blend_weight": blend_w if agent_hint is not None else None,
                    "conflict_penalty": conflict_penalty,
                    "evidence_validation": evidence_validation,
                    "review_marks_count": len(review_marks),
                    "sorted_results_count": len(sorted_results),
                    "usage_tokens": conflict_response.usage.get("tokens", 0),
                    "latency_ms": conflict_response.usage.get("latency_ms", 0)
                }
            )

            # 步骤7: 生成Markdown报告
            logger.info("步骤7: 生成Markdown报告")
            markdown_report_path = None
            try:
                # 将 ReflectionResult 转换为字典用于报告生成
                result_dict = result.model_dump()
                result_dict["paper_title"] = paper_title

                report_path = report_generator.generate_report(
                    paper_id=paper_id,
                    paper_title=result_dict["paper_title"],
                    result=result_dict
                )
                markdown_report_path = report_path
                logger.info(f"Markdown报告已保存: {report_path}")
            except Exception as e:
                logger.error(f"生成Markdown报告失败: {e}")

            # 将报告路径添加到 plugin_metadata
            if markdown_report_path:
                result.plugin_metadata["markdown_report_path"] = markdown_report_path

            logger.info(f"论文{paper_id}处理完成: initial_score={initial_score}, conflict_penalty={conflict_penalty}, final_score={final_score}, 结论={verdict}")
            return result

        except Exception as e:
            logger.error(f"处理论文{paper_id}时发生错误: {e}", exc_info=True)
            return ReflectionResult(
                paper_id=paper_id,
                final_score=0.0,
                verdict="处理失败",
                plugin_metadata={"error": str(e)}
            )

    async def run_from_database(self, paper_id: Optional[str] = None):
        """
        方案1: 从数据库读取并处理

        逻辑：
        - 若指定paper_id：强制评估（即使4个审计组不全，但会提醒）
        - 若未指定paper_id：遍历所有paper_id，仅处理4个审计组齐全且未评估/需重新评估的论文

        Args:
            paper_id: 指定论文ID，如果为None则处理所有论文
        """
        logger.info("=== 方案1: 从数据库读取（agent_audit_result表） ===")

        try:
            # 连接数据库
            await db_manager.connect()

            # 从数据库读取评审规则
            rules = await db_manager.fetch_rules()
            logger.info(f"已加载{len(rules)}条评审规则")

            # 构建规则字典（按rule_id索引）
            rules_dict = {r["rule_id"]: r for r in rules}

            # 获取待处理的论文ID列表
            if paper_id:
                paper_ids = [paper_id]
            else:
                paper_ids = await db_manager.get_paper_ids()

            logger.info(f"找到{len(paper_ids)}篇论文")

            processed_count = 0
            skipped_count = 0

            # 处理每篇论文
            for pid in paper_ids:
                logger.info(f"\n{'='*60}")
                logger.info(f"检查论文: {pid}")
                logger.info(f"{'='*60}")

                # 检查4个审计组是否齐全
                has_all, existing_codes = await db_manager.check_paper_has_all_agents(pid)
                missing_codes = list({"FMT", "REF", "EXP", "LOG"} - set(existing_codes))

                force_mode = (paper_id is not None)  # 指定paper_id时为强制模式

                if not has_all:
                    if force_mode:
                        # 强制模式：提醒但继续
                        print(f"\n[警告] 论文{pid}的审计组不全，缺少: {', '.join(missing_codes)}")
                        print(f"  已有审计组: {', '.join(existing_codes)}")
                        print(f"  强制模式下继续评估，结果可能不完整。")
                        logger.warning(f"论文{pid}审计组不全（缺少{missing_codes}），强制模式下继续评估")
                    else:
                        # 自动模式：跳过
                        logger.info(f"论文{pid}审计组不全（缺少{missing_codes}），跳过")
                        skipped_count += 1
                        continue

                # 自动模式下额外检查：每个agent是否已应用所有规则
                if not force_mode:
                    rules_complete, rules_detail = await db_manager.check_paper_has_all_rules(pid)
                    if not rules_complete:
                        missing_info = []
                        for ac, detail in rules_detail.items():
                            if detail["missing_count"] > 0:
                                missing_info.append(f"{ac}缺少{detail['missing_count']}条规则")
                        logger.info(f"论文{pid}规则未全部应用（{', '.join(missing_info)}），跳过")
                        skipped_count += 1
                        continue

                # 检查是否需要评估
                if not force_mode:
                    needs_eval = await db_manager.check_needs_reevaluation(pid)
                    if not needs_eval:
                        logger.info(f"论文{pid}已评估且无更新，跳过")
                        skipped_count += 1
                        continue

                # 读取该论文的所有审计结果（按rule_id去重，保留最新时间戳）
                audit_records = await db_manager.fetch_audit_results(pid, dedup_by_rule=True)
                logger.info(f"读取到{len(audit_records)}条审计结果（已去重）")

                # 从result_json中提取审计结果，按agent_code分组
                audit_results_by_agent = {}
                paper_name = None
                for record in audit_records:
                    agent_code = record.get("agent_code", "")
                    if not paper_name:
                        paper_name = record.get("paper_name")

                    result_json = record.get("result_json", {})
                    if isinstance(result_json, dict) and "audit_results" in result_json:
                        # result_json包含完整的审计结果
                        if agent_code not in audit_results_by_agent:
                            audit_results_by_agent[agent_code] = result_json
                        else:
                            # 合并同一agent_code的多条记录
                            existing = audit_results_by_agent[agent_code]
                            existing["audit_results"].extend(result_json.get("audit_results", []))
                    else:
                        # result_json不含audit_results，从行级字段构建
                        if agent_code not in audit_results_by_agent:
                            audit_results_by_agent[agent_code] = {
                                "agent_code": agent_code,
                                "audit_results": []
                            }
                        # 从行级字段构建单条审计结果
                        rule_info = rules_dict.get(record.get("rule_id", ""), {})
                        audit_item = {
                            "result_id": record.get("result_id", ""),
                            "paper_id": pid,
                            "point": rule_info.get("rule_name_cn", record.get("rule_id", "")),
                            "rule_id": record.get("rule_id", ""),
                            "score": record.get("score_obtained", 0),
                            "level": "Critical" if rule_info.get("severity") == "CRITICAL" else "Warning",
                            "description": record.get("audit_suggestion", ""),
                            "evidence_quote": "",
                            "location": {},
                            "suggestion": record.get("audit_suggestion", ""),
                        }
                        # 如果result_json有额外字段，合并
                        if isinstance(result_json, dict):
                            audit_item.update({
                                k: v for k, v in result_json.items()
                                if k in ("evidence_quote", "location", "description", "suggestion", "point", "level", "score")
                            })

                        # 重要修复：如果在 result_json 中没有 evidence_quote，但行级字段有，也要赋给它
                        if not audit_item.get("evidence_quote") and isinstance(result_json, dict) and "evidence_quotes" in result_json:
                            quotes = result_json.get("evidence_quotes", [])
                            if quotes and len(quotes) > 0:
                                audit_item["evidence_quote"] = quotes[0]

                        audit_results_by_agent[agent_code]["audit_results"].append(audit_item)

                # 将分组结果转为列表
                audit_results = list(audit_results_by_agent.values())
                logger.info(f"按agent_code分组后有{len(audit_results)}个审计组的结果")

                # 获取论文内容
                paper_content = await db_manager.get_paper_content(pid)

                # 构建不全审计组的提示信息
                incomplete_note = ""
                if not has_all:
                    incomplete_note = f"[注意] 审计组不全（缺少{', '.join(missing_codes)}），评估结果可能不完整。"

                # 处理论文
                result = await self.process_paper(
                    pid, audit_results, paper_content,
                    rules_dict=rules_dict,
                    incomplete_note=incomplete_note,
                    paper_name=paper_name,
                    audit_records=audit_records
                )

                # 保存结果到数据库（reflect_agent_verdict表）
                if "error" not in result.plugin_metadata:
                    # 从plugin_metadata中获取已计算好的分数
                    initial_score = result.plugin_metadata.get("initial_score", 0.0)
                    conflict_penalty = result.plugin_metadata.get("conflict_penalty", 0.0)
                    conflict_data = result.plugin_metadata.get("conflict_resolution", {})

                    # 构建去重后建议（filtered_suggestions）：所有issues
                    all_issues = []
                    for issue in result.critical_issues + result.major_issues + result.minor_issues:
                        issue_dict = issue.model_dump() if hasattr(issue, 'model_dump') else issue
                        all_issues.append(issue_dict)

                    # 去重：按description去重
                    seen_descs = set()
                    deduped_issues = []
                    for iss in all_issues:
                        desc = iss.get("description", "")
                        if desc and desc not in seen_descs:
                            seen_descs.add(desc)
                            deduped_issues.append(iss)

                    # 构建优先级排序后建议（prioritized_suggestions）：
                    # 仅保留critical和warning级别，按优先级排序
                    priority_order = {"critical": 0, "warning": 1, "error": 1, "info": 2}
                    high_priority_issues = [
                        iss for iss in deduped_issues
                        if iss.get("priority", "info") in ("critical", "warning", "error")
                    ]
                    prioritized = sorted(
                        high_priority_issues,
                        key=lambda x: priority_order.get(x.get("priority", "info"), 99)
                    )

                    final_verdict_text = result.verdict

                    await db_manager.save_verdict(
                        paper_id=pid,
                        paper_name=paper_name or f"Paper_{pid}",
                        initial_score=initial_score,
                        conflict_resolution=json.dumps(conflict_data, ensure_ascii=False, default=str) if conflict_data else None,
                        conflict_penalty=conflict_penalty,
                        final_score=result.final_score,
                        filtered_suggestions=deduped_issues,
                        prioritized_suggestions=prioritized,
                        final_verdict=final_verdict_text,
                    )

                processed_count += 1

                # 打印结果摘要
                print(f"\n论文ID: {pid}")
                if paper_name:
                    print(f"论文题目: {paper_name}")
                print(f"初始得分(score_obtained之和): {result.plugin_metadata.get('initial_score', 0)}")
                print(f"冲突扣分: {result.plugin_metadata.get('conflict_penalty', 0)}")
                print(f"最终得分(100分制): {result.final_score}")
                print(f"评审结论: {result.verdict}")
                if incomplete_note:
                    print(f"  {incomplete_note}")
                print(f"是否需要人工复核: {result.needs_human_review}")
                if result.human_review_reason:
                    print(f"复核原因: {result.human_review_reason}")
                markdown_path = result.plugin_metadata.get("markdown_report_path")
                if markdown_path:
                    print(f"Markdown报告: {markdown_path}")

            print(f"\n处理完成: 已评估{processed_count}篇, 跳过{skipped_count}篇")

        except Exception as e:
            logger.error(f"从数据库读取处理失败: {e}", exc_info=True)
        finally:
            # 关闭数据库连接
            await db_manager.disconnect()

    async def run_from_files(self, prompts_dir: str = "prompts"):
        """
        方案2: 从文件读取并处理

        Args:
            prompts_dir: JSON文件所在目录
        """
        logger.info("=== 方案2: 从文件读取 ===")

        try:
            prompts_path = Path(prompts_dir)
            if not prompts_path.exists():
                logger.error(f"目录不存在: {prompts_path}")
                return

            # 查找所有JSON文件
            json_files = list(prompts_path.glob("*.json"))
            if not json_files:
                logger.warning(f"在{prompts_path}目录下未找到JSON文件")
                return

            logger.info(f"找到{len(json_files)}个JSON文件")

            # 按paper_id分组
            paper_audits = {}
            for json_file in json_files:
                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)

                    # 提取paper_id（从文件名或JSON内容）
                    paper_id = data.get("paper_id", json_file.stem)

                    if paper_id not in paper_audits:
                        paper_audits[paper_id] = []

                    paper_audits[paper_id].append(data)
                    logger.info(f"读取文件: {json_file.name} -> paper_id: {paper_id}")

                except Exception as e:
                    logger.error(f"读取文件{json_file}失败: {e}")

            # 处理每篇论文
            for paper_id, audits in paper_audits.items():
                logger.info(f"\n{'='*60}")
                logger.info(f"处理论文: {paper_id}")
                logger.info(f"{'='*60}")

                result = await self.process_paper(paper_id, audits)

                # 保存结果到results文件夹
                results_dir = Path("results")
                results_dir.mkdir(exist_ok=True)
                output_file = results_dir / f"result_{paper_id}.json"
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(result.model_dump(), f, ensure_ascii=False, indent=2)
                logger.info(f"结果已保存到: {output_file}")

                # 打印结果摘要
                print(f"\n论文ID: {paper_id}")
                print(f"最终得分: {result.final_score}")
                print(f"评审结论: {result.verdict}")
                print(f"是否需要人工复核: {result.needs_human_review}")
                markdown_path = result.plugin_metadata.get("markdown_report_path")
                if markdown_path:
                    print(f"Markdown报告: {markdown_path}")

        finally:
            # 不需要在这里关闭deepseek_client，会在main函数的finally中统一关闭
            pass


async def main():
    """主函数"""
    # 设置自定义异常处理器以抑制SSL错误
    loop = asyncio.get_event_loop()
    loop.set_exception_handler(custom_exception_handler)

    parser = argparse.ArgumentParser(description="反思评估组主程序")
    parser.add_argument(
        "--mode",
        choices=["database", "file", "interactive"],
        default="interactive",
        help="运行模式: database(从数据库读取), file(从文件读取), interactive(交互式选择)"
    )
    parser.add_argument(
        "--paper-id",
        type=str,
        help="指定论文ID（仅在database模式下有效，指定后强制评估，即使4个审计组不全）"
    )
    parser.add_argument(
        "--prompts-dir",
        type=str,
        default="prompts",
        help="JSON文件目录（仅在file模式下有效）"
    )
    parser.add_argument(
        "--no-dialogue",
        action="store_true",
        help="禁用导师评语（导师对话）生成，可节省一次 DeepSeek 调用"
    )
    parser.add_argument(
        "--always-use-llm",
        action="store_true",
        help="启用纯LLM-as-a-Judge模式（始终调用DeepSeek API进行裁决，即使无冲突）"
    )
    parser.add_argument(
        "--no-hallucination-filter",
        action="store_true",
        help="禁用幻觉过滤（证据验证），计算final_score时不纳入证据验证调整"
    )

    args = parser.parse_args()

    try:
        # 根据模式运行
        if args.mode == "interactive":
            print("\n" + "="*60)
            print("反思评估组 - 主运行程序")
            print("="*60)
            print("\n请选择运行方案:")
            print("1. 从PostgreSQL数据库读取 (agent_audit_result表)")
            print("2. 从prompts文件夹读取JSON文件")
            print("0. 退出")

            choice = input("\n请输入选项 (0-2): ").strip()

            if choice == "1":
                # 创建database模式的编排器
                orchestrator = ReflectionOrchestrator(mode="database", enable_dialogue=not args.no_dialogue, always_use_llm=args.always_use_llm, enable_hallucination_filter=not args.no_hallucination_filter)
                orchestrator.initialize_modules()
                paper_id = input("请输入论文ID (留空处理所有论文): ").strip() or None
                await orchestrator.run_from_database(paper_id)
            elif choice == "2":
                # 创建file模式的编排器
                orchestrator = ReflectionOrchestrator(mode="file", enable_dialogue=not args.no_dialogue, always_use_llm=args.always_use_llm, enable_hallucination_filter=not args.no_hallucination_filter)
                orchestrator.initialize_modules()
                prompts_dir = input(f"请输入JSON文件目录 (默认: prompts): ").strip() or "prompts"
                await orchestrator.run_from_files(prompts_dir)
            elif choice == "0":
                print("退出程序")
            else:
                print("无效选项")

        elif args.mode == "database":
            # 创建database模式的编排器
            orchestrator = ReflectionOrchestrator(mode="database", enable_dialogue=not args.no_dialogue, always_use_llm=args.always_use_llm, enable_hallucination_filter=not args.no_hallucination_filter)
            orchestrator.initialize_modules()
            await orchestrator.run_from_database(args.paper_id)

        elif args.mode == "file":
            # 创建file模式的编排器
            orchestrator = ReflectionOrchestrator(mode="file", enable_dialogue=not args.no_dialogue, always_use_llm=args.always_use_llm, enable_hallucination_filter=not args.no_hallucination_filter)
            orchestrator.initialize_modules()
            await orchestrator.run_from_files(args.prompts_dir)

    finally:
        # 确保在main函数结束前关闭所有HTTP连接
        await deepseek_client.close()
        # 给一点时间让所有异步任务完成清理
        await asyncio.sleep(0.1)


if __name__ == "__main__":
    # 启用SSL错误抑制
    ssl_suppressor = SuppressSSLErrors()

    try:
        with ssl_suppressor:
            asyncio.run(main())
    except KeyboardInterrupt:
        print("\n程序被用户中断")
    except Exception as e:
        logger.error(f"程序异常退出: {e}", exc_info=True)
