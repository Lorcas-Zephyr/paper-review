import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException

# 使用统一的数据库和API模块
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.db.database import db_manager
from src.api.deepseek_client import deepseek_client
from src.common.models import ConflictResolutionRequest, ConflictResolutionResponse, ConflictType
from src.common.config_c import settings
from src.common.thesis_grade_verdict import build_conflict_final_verdict_text

logger = logging.getLogger(__name__)

app = FastAPI(title="Reflection Judge - Conflict Resolver")

DEFAULT_SCORE_DIFF_THRESHOLD = 20
LEVEL_PRIORITY = {"Info": 0, "Warning": 1, "Critical": 2}

# 与 orchestrator `agent_endpoints[].description` 一致；用于 group_id 解析及 Group_N 文案替换
GROUP_ID_DISPLAY_NAMES: Dict[int, str] = {
    2: "格式审计组",
    3: "逻辑审计组",
    4: "代码审计组",
    5: "实验数据审计组",
    6: "文献审计组",
}

AGENT_CODE_DISPLAY_NAMES: Dict[str, str] = {
    "FMT": "格式审计组",
    "REF": "文献审计组",
    "EXP": "实验数据审计组",
    "LOG": "逻辑审计组",
}


def _coerce_group_id_to_int(group_id: Any) -> Optional[int]:
    if group_id is None or group_id == "":
        return None
    try:
        return int(str(group_id).strip())
    except (TypeError, ValueError):
        return None


def humanize_group_label_text(text: str) -> str:
    """将正文中的 Group_2、group_5 等替换为中文审计组名称（兼容 LLM 照抄旧标签）。"""
    if not text or not isinstance(text, str):
        return text

    def _repl(m):
        n = int(m.group(1))
        return GROUP_ID_DISPLAY_NAMES.get(n, m.group(0))

    return re.sub(r"Group_(\d+)", _repl, text, flags=re.IGNORECASE)


def humanize_resolved_issues_group_labels(issues: List[Dict[str, Any]]) -> None:
    """就地替换裁决结果中的 Group_N 为中文组名。"""
    keys = ("agent1_name", "agent2_name", "resolved_comment", "root_cause", "resolved_suggestion")
    for issue in issues or []:
        if not isinstance(issue, dict):
            continue
        for k in keys:
            v = issue.get(k)
            if isinstance(v, str):
                issue[k] = humanize_group_label_text(v)


class ConflictResolver:
    def __init__(self, mode: str = "database", always_use_llm: Optional[bool] = None,
                 enable_hallucination_filter: bool = True):
        # 使用统一的DeepSeek客户端和数据库管理器
        self.llm_client = deepseek_client
        self.db_client = db_manager
        # 从配置或参数获取always_use_llm设置
        self.always_use_llm = always_use_llm if always_use_llm is not None else settings.conflict_resolution.always_use_llm
        self.mode = mode  # "database" or "file"
        self.enable_hallucination_filter = enable_hallucination_filter  # 是否启用幻觉过滤
        self.conflict_patterns = self._load_conflict_patterns()
        # 审计组权重配置（根据重要性调整）
        # 使用agent_code作为key，同时保留中文名兼容
        self.agent_weights = {
            "LOG": 1.2,
            "EXP": 1.1,
            "REF": 1.0,
            "FMT": 0.8,
            # 与调度器 description 一致
            "逻辑审计组": 1.2,
            "实验数据审计组": 1.1,
            "格式审计组": 0.8,
            "文献审计组": 1.0,
            # 兼容旧中文名
            "代码审计组": 1.1,
            "实验数据组": 1.1,
            "文献真实性组": 1.0,
        }
        # 证据验证权重（验证分数对最终评分的影响）
        self.evidence_validation_weight = 0.1

    def _load_conflict_patterns(self) -> Dict[str, Dict[str, Any]]:
        """Load keyword-based conflict patterns used by the pre-filter rule engine."""
        return {
            "efficiency": {
                "keywords": [
                    "高效", "性能好", "快速", "优化",
                    "低效", "性能差", "缓慢", "耗时"
                ],
                "pattern": r"(高效|性能好|快速|优化|低效|性能差|缓慢|耗时)",
            },
            "quality": {
                "keywords": [
                    "质量高", "精确", "准确", "质量低", "不精确", "错误"
                ],
                "pattern": r"(质量高|精确|准确|质量低|不精确|错误)",
            },
            "completeness": {
                "keywords": ["完整", "全面", "缺失", "不足", "缺少"],
                "pattern": r"(完整|全面|缺失|不足|缺少)",
            },
        }

    @staticmethod
    def _coerce_score(value: Any, default: int = 70) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    def _normalize_agent_results(self, raw_results: Any) -> List[Dict[str, Any]]:
        """Normalize agent results from list/object/JSON-string into the canonical list schema.

        支持两种格式：
        1. 旧格式：包含agent_results列表，每个元素有agent_info和result字段
        2. 新格式（work_week2.txt）：包含group_id和audit_results列表，每个元素有point、score、level等字段
        """
        parsed = raw_results

        if isinstance(parsed, str):
            text = parsed.strip()
            if not text:
                return []
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"agent_results JSON字符串解析失败: {exc.msg}") from exc

        # 检查是否为新格式（包含group_id/agent_code和audit_results）
        if isinstance(parsed, dict) and "audit_results" in parsed:
            if "group_id" in parsed or "agent_code" in parsed:
                return self._convert_new_format_to_old(parsed)

        if isinstance(parsed, dict):
            if "agent_results" in parsed:
                return self._normalize_agent_results(parsed["agent_results"])
            parsed = [parsed]

        if not isinstance(parsed, list):
            raise ValueError("agent_results必须是列表、JSON字符串或包含agent_results的对象")

        normalized: List[Dict[str, Any]] = []
        for idx, item in enumerate(parsed):
            if isinstance(item, str):
                try:
                    item = json.loads(item)
                except json.JSONDecodeError:
                    logger.warning("Skip unparsable agent result string at index=%d", idx)
                    continue

            if not isinstance(item, dict):
                logger.warning("Skip non-object agent result at index=%d", idx)
                continue

            # Compat for list input where each element is new-format payload.
            if ("group_id" in item or "agent_code" in item) and "audit_results" in item:
                normalized.extend(self._convert_new_format_to_old(item))
                continue

            result_data = item.get("result")
            if not isinstance(result_data, dict):
                # Compatibility: some groups may put result fields directly in result_json.
                result_data = item.get("result_json", {})
            if not isinstance(result_data, dict):
                result_data = {}

            agent_info = item.get("agent_info")
            if not isinstance(agent_info, dict):
                agent_info = {
                    "name": item.get("agent_name") or f"agent_{idx + 1}",
                    "version": item.get("agent_version", "unknown"),
                }

            normalized.append(
                {
                    "request_id": item.get("request_id", ""),
                    "agent_info": {
                        "name": agent_info.get("name", f"agent_{idx + 1}"),
                        "version": agent_info.get("version", "unknown"),
                    },
                    "result": {
                        "score": self._coerce_score(result_data.get("score", item.get("score", 70))),
                        "audit_level": result_data.get("audit_level", item.get("audit_level", "Info")),
                        "comment": result_data.get("comment", ""),
                        "suggestion": result_data.get("suggestion", ""),
                        "tags": result_data.get("tags", []),
                        # 新格式字段映射
                        "point": result_data.get("point", item.get("point", "")),
                        "description": result_data.get("description", item.get("description", "")),
                        "evidence_quote": result_data.get("evidence_quote", item.get("evidence_quote", "")),
                        "location": result_data.get("location", item.get("location", {})),
                    },
                    "usage": item.get("usage", {"tokens": 0, "latency_ms": 0}),
                }
            )

        return normalized

    @staticmethod
    def _is_negative_finding(result_data: Dict[str, Any]) -> bool:
        """Return True when the finding is Warning/Critical and must include evidence."""
        level = str(result_data.get("audit_level", "")).strip().lower()
        return level in {"warning", "critical"}

    def enforce_evidence_linking(
            self,
            agent_results: List[Dict[str, Any]],
            evidence_validation: Dict[str, Any],
            paper_context_available: bool,
    ) -> Dict[str, Any]:
        """Apply strict evidence-linking for negative findings.

        Rule 1: Warning/Critical without evidence_quote -> remove.
        Rule 2: Warning/Critical with invalid evidence_quote -> remove when paper context is available.
        """
        invalid_quote_set = set()
        for invalid in evidence_validation.get("invalid_results", []) or []:
            agent_name = invalid.get("agent_name", "")
            clean_quote = invalid.get("clean_quote", "")
            invalid_quote_set.add((agent_name, clean_quote))

        filtered_results: List[Dict[str, Any]] = []
        removed_results: List[Dict[str, Any]] = []

        for item in agent_results:
            result_data = item.get("result", {}) if isinstance(item.get("result"), dict) else {}
            if not self._is_negative_finding(result_data):
                filtered_results.append(item)
                continue

            agent_name = item.get("agent_info", {}).get("name", "unknown_agent")
            evidence_quote = str(result_data.get("evidence_quote", "") or "").strip()

            # 特殊情况处理：如果证据是常见的占位符或完全缺失，且级别不是Info
            if not evidence_quote or evidence_quote in ["N/A", "无", "未提供", "全文评审"]:
                removed_results.append(
                    {
                        "agent_name": agent_name,
                        "reason": "negative finding missing evidence_quote",
                        "comment": result_data.get("comment", ""),
                    }
                )
                continue

            clean_quote = self._clean_evidence_quote(evidence_quote) if evidence_quote else ""

            if not clean_quote:
                removed_results.append(
                    {
                        "agent_name": agent_name,
                        "reason": "negative finding missing evidence_quote",
                        "comment": result_data.get("comment", ""),
                    }
                )
                continue

            if paper_context_available and (agent_name, clean_quote) in invalid_quote_set:
                removed_results.append(
                    {
                        "agent_name": agent_name,
                        "reason": "evidence_quote not found in paper content",
                        "comment": result_data.get("comment", ""),
                        "evidence_quote": clean_quote,
                    }
                )
                continue

            filtered_results.append(item)

        return {
            "filtered_agent_results": filtered_results,
            "removed_results": removed_results,
            "removed_count": len(removed_results),
            "original_count": len(agent_results),
            "remaining_count": len(filtered_results),
            "paper_context_available": paper_context_available,
        }

    def _convert_new_format_to_old(self, new_format_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """将新格式转换为旧格式

        支持两种新格式：
        1. week2格式：group_id + audit_results
        2. week3格式（agent_audit_result.result_json）：agent_code + audit_results
        """
        normalized = []

        # week3格式：使用agent_code
        agent_code = new_format_data.get("agent_code", "")
        group_id = new_format_data.get("group_id", 0)
        audit_results = new_format_data.get("audit_results", [])

        explicit_name = (new_format_data.get("group_name") or "").strip()
        agent_code = (agent_code or "").strip() if isinstance(agent_code, str) else str(agent_code or "").strip()
        gid_int = _coerce_group_id_to_int(group_id)

        if explicit_name:
            group_name = explicit_name
        elif agent_code:
            group_name = AGENT_CODE_DISPLAY_NAMES.get(agent_code, agent_code)
        elif gid_int is not None:
            group_name = GROUP_ID_DISPLAY_NAMES.get(gid_int, f"Group_{group_id}")
        else:
            group_name = f"Group_{group_id}"

        for idx, audit_item in enumerate(audit_results):
            if not isinstance(audit_item, dict):
                continue

            # 映射字段
            score = self._coerce_score(audit_item.get("score", 70))
            level = audit_item.get("level", "Info")
            comment = audit_item.get("description", "")
            point = audit_item.get("point", "")
            rule_id = audit_item.get("rule_id", "")

            # 合并comment和point
            full_comment = f"{point}: {comment}" if point else comment

            normalized.append({
                "request_id": audit_item.get("result_id", f"req_{agent_code or group_id}_{idx}"),
                "agent_info": {
                    "name": group_name,
                    "version": "v1.0"
                },
                "result": {
                    "score": score,
                    "audit_level": level,
                    "comment": full_comment,
                    "suggestion": audit_item.get("suggestion", ""),
                    "tags": [],
                    "point": point,
                    "description": comment,
                    "evidence_quote": audit_item.get("evidence_quote", ""),
                    "location": audit_item.get("location", {}),
                    "rule_id": rule_id,
                },
                "usage": {
                    "tokens": 0,
                    "latency_ms": 0
                }
            })

        logger.info(f"转换新格式数据: agent_code={agent_code or group_id}, 转换了{len(normalized)}条结果")
        return normalized

    def validate_evidence_quotes(self, agent_results: List[Dict[str, Any]], paper_content: str) -> Dict[str, Any]:
        """验证Agent结果中的evidence_quote是否在论文内容中存在

        返回格式:
        {
            "valid_count": 10,
            "invalid_count": 2,
            "invalid_results": [
                {
                    "agent_name": "格式审计组",
                    "evidence_quote": "原文第4.2节提到...",
                    "reason": "未在论文内容中找到匹配文本"
                }
            ],
            "validation_score": 0.83  # 有效证据比例
        }
        """
        if not paper_content:
            logger.warning("论文内容为空，跳过证据验证")
            return {
                "valid_count": 0,
                "invalid_count": 0,
                "invalid_results": [],
                "validation_score": 0.0,
                "message": "论文内容为空，无法验证证据"
            }

        paper_content_lower = paper_content.lower()
        invalid_results = []
        valid_count = 0
        total_quotes = 0

        for agent_result in agent_results:
            agent_info = agent_result.get("agent_info", {})
            agent_name = agent_info.get("name", "未知Agent")
            result_data = agent_result.get("result", {})
            evidence_quote = result_data.get("evidence_quote", "")

            if not evidence_quote or evidence_quote.strip() == "":
                continue

            total_quotes += 1

            # 特殊情况处理：如果证据是常见的占位符或完全缺失
            if not evidence_quote or evidence_quote in ["N/A", "无", "未提供", "全文评审"]:
                invalid_results.append({
                    "agent_name": agent_name,
                    "evidence_quote": evidence_quote,
                    "clean_quote": evidence_quote,
                    "reason": "未提供有效的证据引用"
                })
                continue

            # 清理证据引用：移除可能的前缀如"原文第4.2节提到："
            clean_quote = self._clean_evidence_quote(evidence_quote)
            if not clean_quote:
                invalid_results.append({
                    "agent_name": agent_name,
                    "evidence_quote": evidence_quote,
                    "clean_quote": clean_quote,
                    "reason": "提取有效文本失败"
                })
                continue

            # 检查是否在论文内容中存在（简单字符串匹配，可扩展为模糊匹配）
            if self._quote_exists_in_content(clean_quote, paper_content_lower):
                valid_count += 1
            else:
                invalid_results.append({
                    "agent_name": agent_name,
                    "evidence_quote": evidence_quote,
                    "clean_quote": clean_quote[:100] + "..." if len(clean_quote) > 100 else clean_quote,
                    "reason": "未在论文内容中找到匹配文本"
                })

        invalid_count = len(invalid_results)
        validation_score = valid_count / max(1, total_quotes)

        logger.info(f"证据验证完成: 有效{valid_count}/总数{total_quotes}, 分数{validation_score:.2f}")

        return {
            "valid_count": valid_count,
            "invalid_count": invalid_count,
            "invalid_results": invalid_results,
            "validation_score": validation_score,
            "total_quotes": total_quotes
        }

    def _clean_evidence_quote(self, quote: str) -> str:
        """清理证据引用，提取核心文本内容"""
        # 移除常见前缀
        prefixes = ["原文", "论文", "文中", "第", "节提到", "提到", ":", "：", "“", "”", "'", '"']
        clean = quote.strip()

        # 如果包含引号，提取引号内内容
        import re
        quote_pattern = r'[“"]([^"”]+)["”]'
        matches = re.findall(quote_pattern, clean)
        if matches:
            # 取最长的引号内容
            longest = max(matches, key=len)
            return longest.strip()

        # 否则移除前缀并返回
        for prefix in prefixes:
            if clean.startswith(prefix):
                clean = clean[len(prefix):].strip()

        return clean

    def _quote_exists_in_content(self, quote: str, content_lower: str) -> bool:
        """检查引用是否在内容中存在（支持模糊匹配）"""
        if not quote:
            return False

        quote_lower = quote.lower()

        # 1. 直接包含检查
        if quote_lower in content_lower:
            return True

        # 2. 模糊匹配：如果引用较长，检查部分匹配
        if len(quote_lower) > 20:
            # 尝试匹配较长的子串
            for i in range(0, len(quote_lower) - 10, 5):
                substring = quote_lower[i:i+20]
                if substring in content_lower:
                    return True

        # 3. 移除标点符号后检查
        import re
        quote_no_punct = re.sub(r'[^\w\s]', '', quote_lower)
        content_no_punct = re.sub(r'[^\w\s]', '', content_lower)

        if len(quote_no_punct) > 10 and quote_no_punct in content_no_punct:
            return True

        return False

    @staticmethod
    def _normalize_resolution_data(resolution_data: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(resolution_data or {})
        normalized.setdefault("conflicts_resolved", bool(normalized.get("resolved_issues")))
        normalized.setdefault("resolved_issues", [])
        normalized.setdefault("confidence_score", 0.5)
        return normalized

    @staticmethod
    def _attach_result_json(result_data: Dict[str, Any]) -> Dict[str, Any]:
        core_keys = [
            "conflicts_resolved",
            "resolved_issues",
            "confidence_score",
            "tags",
            "final_verdict",
            "paper_id",
            "evidence_validation",
            "evidence_enforcement",
            "markdown_report_path",
        ]
        result_data["result_json"] = {key: result_data.get(key) for key in core_keys if key in result_data}
        return result_data

    def detect_conflicts(
        self,
        agent_results: List[Dict[str, Any]],
        conflict_threshold: Optional[float] = None,
        score_diff_threshold: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        if not agent_results or len(agent_results) < 2:
            logger.info("Agent result count < 2, skip conflict detection")
            return []

        comments: List[Dict[str, Any]] = []
        for result in agent_results:
            agent_info = result.get("agent_info")
            if not agent_info or "name" not in agent_info:
                logger.warning("Skip malformed agent result: request_id=%s", result.get("request_id", "unknown"))
                continue

            result_data = result.get("result", {})
            if isinstance(result_data, dict) and "comment" in result_data:
                comments.append(
                    {
                        "agent": agent_info["name"],
                        "comment": result_data.get("comment", ""),
                        "level": result_data.get("audit_level", "Info"),
                        "score": self._coerce_score(result_data.get("score", 70)),
                        "tags": result_data.get("tags", []),
                        "suggestion": result_data.get("suggestion", ""),
                    }
                )

        conflicts: List[Dict[str, Any]] = []
        score_threshold = DEFAULT_SCORE_DIFF_THRESHOLD if score_diff_threshold is None else score_diff_threshold

        for i in range(len(comments)):
            for j in range(i + 1, len(comments)):
                c1, c2 = comments[i], comments[j]
                conflict_info = self._analyze_comment_conflict(c1, c2)

                score_diff = abs(c1["score"] - c2["score"])
                if score_diff >= score_threshold and not conflict_info:
                    conflict_info = {
                        "type": ConflictType.MEASUREMENT_DIFFERENCE,
                        "confidence": min(0.6 + (score_diff - score_threshold) * 0.01, 0.9),
                    }

                if conflict_info:
                    conflicts.append(
                        {
                            "agent1": c1["agent"],
                            "agent2": c2["agent"],
                            "comment1": c1["comment"],
                            "comment2": c2["comment"],
                            "level1": c1["level"],
                            "level2": c2["level"],
                            "score1": c1["score"],
                            "score2": c2["score"],
                            "conflict_type": conflict_info["type"],
                            "confidence": conflict_info["confidence"],
                        }
                    )

        threshold = float(os.getenv("CONFLICT_THRESHOLD", "0.7")) if conflict_threshold is None else float(conflict_threshold)
        conflicts.sort(key=lambda x: x["confidence"], reverse=True)
        filtered = [c for c in conflicts if c["confidence"] >= threshold]
        logger.info("Conflict detection done: %d found, %d kept", len(conflicts), len(filtered))
        return filtered

    def _analyze_comment_conflict(self, comment1: Dict[str, Any], comment2: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        text1 = comment1.get("comment", "").lower()
        text2 = comment2.get("comment", "").lower()

        for pattern_info in self.conflict_patterns.values():
            pattern = pattern_info["pattern"]
            matches1 = re.findall(pattern, text1)
            matches2 = re.findall(pattern, text2)
            if matches1 and matches2:
                for m1 in matches1:
                    for m2 in matches2:
                        if self._are_opposites(m1, m2):
                            level_diff = abs(
                                LEVEL_PRIORITY.get(comment1.get("level", "Info"), 0)
                                - LEVEL_PRIORITY.get(comment2.get("level", "Info"), 0)
                            )
                            return {
                                "type": ConflictType.DIRECT_CONTRADICTION,
                                "confidence": min(0.7 + level_diff * 0.1, 0.95),
                            }

        if self._has_contextual_dependency(text1, text2):
            return {"type": ConflictType.CONTEXT_DEPENDENT, "confidence": 0.65}

        return None

    def _are_opposites(self, word1: str, word2: str) -> bool:
        opposites = [
            ("高效", "低效"),
            ("性能好", "性能差"),
            ("快速", "缓慢"),
            ("完整", "缺失"),
            ("准确", "错误"),
            ("优化", "恶化"),
            ("质量高", "质量低"),
            ("精确", "不精确"),
        ]
        return (word1, word2) in opposites or (word2, word1) in opposites

    def _has_contextual_dependency(self, text1: str, text2: str) -> bool:
        contextual_triggers = [
            ("不同", "相同"),
            ("部分", "整体"),
            ("短期", "长期"),
            ("理论", "实践"),
            ("假设", "验证"),
        ]
        for t1, t2 in contextual_triggers:
            if (t1 in text1 and t2 in text2) or (t2 in text1 and t1 in text2):
                return True
        return False

    async def resolve_conflicts_with_llm(
        self,
        request: ConflictResolutionRequest,
        conflict_pairs: List[Dict[str, Any]],
        paper_context: str
    ) -> tuple:
        """使用DeepSeek API进行冲突裁决"""
        import time
        start_time = time.time()

        # 构建冲突描述
        conflict_descriptions = []
        for pair in conflict_pairs:
            conflict_descriptions.append(
                f"- {pair['agent1']} (评分:{pair.get('score1', 'N/A')}, 级别:{pair.get('level1', 'N/A')}) "
                f"vs {pair['agent2']} (评分:{pair.get('score2', 'N/A')}, 级别:{pair.get('level2', 'N/A')}): "
                f"'{pair['comment1']}' vs '{pair['comment2']}'"
            )

        # 构建系统提示词
        system_prompt = """你是一位资深的学术评审专家，负责裁决不同审计组之间的意见冲突。
你需要：
1. 分析冲突的根本原因
2. 综合考虑证据强度、专业领域、置信度
3. 给出公正的裁决意见
4. 返回JSON格式的结果

final_level 约定：Critical 仅用于可能影响结论可信度的问题（如实验/数据硬伤、统计显著性缺失、数据矛盾、学术不端嫌疑等）。
关键词与正文术语不完全一致、英文摘要大小写/专有名词书写、引用或排版类问题应标为 Warning，不要用 Critical。
学术论文中「首次出现写全称并在括号内给出英文或缩写（如：可编程逻辑控制器（PLC））」是规范写法，本身不是错误；若仅指出后文与前文对同一缩写的括注格式不完全一致，应标 Warning，不要标 Critical。"""

        # 构建用户提示词
        paper_title = request.metadata.get("paper_title", "Unknown Paper")
        paper_excerpt = paper_context[:1000] + "..." if len(paper_context) > 1000 else paper_context
        conflicts_text = "\n".join(conflict_descriptions)

        user_prompt = f"""论文标题: {paper_title}

论文摘要:
{paper_excerpt}

检测到以下冲突:
{conflicts_text}

请分析这些冲突并给出裁决意见。上文中的审计组名称请原样沿用（如「格式审计组」「实验数据审计组」等），不要使用 Group_数字 形式。

返回JSON格式，包含：
{{
  "resolved_issues": [
    {{
      "agent1_name": "审计组1",
      "agent2_name": "审计组2",
      "conflict_type": "冲突类型",
      "root_cause": "根本原因",
      "evidence_strength": 0.8,
      "confidence": 0.85,
      "resolved_comment": "裁决意见",
      "resolved_suggestion": "改进建议",
      "final_level": "Warning",
      "needs_human_review": false
    }}
  ]
}}"""

        try:
            # 调用DeepSeek API
            response = await self.llm_client.chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=request.config.get("temperature", 0.3),
                max_tokens=request.config.get("max_tokens", 2000)
            )

            # 解析响应
            content = response["choices"][0]["message"]["content"]

            # 尝试提取JSON
            import re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                json_str = json_match.group()
            else:
                json_str = content

            # 检查JSON是否被截断（响应达到max_tokens限制）
            finish_reason = response.get("choices", [{}])[0].get("finish_reason", "")
            if finish_reason == "length":
                logger.warning("DeepSeek响应被截断（达到max_tokens限制），尝试补全JSON结构")
                # 尝试补全JSON结构
                json_str = json_str.rstrip()
                # 统计未闭合的括号
                open_braces = json_str.count('{') - json_str.count('}')
                open_brackets = json_str.count('[') - json_str.count(']')

                # 补全缺失的闭合符号
                if open_brackets > 0:
                    json_str += '\n' + '  ]' * open_brackets
                if open_braces > 0:
                    json_str += '\n' + '}' * open_braces

                logger.info(f"已补全JSON结构: 添加了{open_brackets}个']'和{open_braces}个'}}'")

            # 保存原始JSON用于调试
            original_json_str = json_str

            # 多层次JSON修复策略
            def repair_json_basic(json_text):
                """基本JSON修复"""
                # 移除尾随逗号
                fixed = re.sub(r',(\s*[}\]])', r'\1', json_text)
                # 移除连续逗号
                fixed = re.sub(r',\s*,', ',', fixed)
                return fixed

            def repair_json_aggressive(json_text):
                """激进JSON修复 - 逐行分析并修复"""
                lines = json_text.split('\n')
                fixed_lines = []

                for i, line in enumerate(lines):
                    stripped = line.strip()

                    # 跳过空行
                    if not stripped:
                        fixed_lines.append(line)
                        continue

                    # 检查下一行（如果存在）
                    next_line_stripped = ""
                    if i + 1 < len(lines):
                        next_line_stripped = lines[i + 1].strip()

                    # 当前行是值行（以"、数字、true、false、null、}、]结尾）
                    # 且下一行是属性名，则需要添加逗号
                    needs_comma = False

                    if stripped and not stripped.endswith(','):
                        # 检查当前行是否以值结尾
                        ends_with_value = (
                            stripped.endswith('"') or
                            stripped.endswith('}') or
                            stripped.endswith(']') or
                            (len(stripped) > 0 and stripped[-1].isdigit()) or
                            stripped.endswith('true') or
                            stripped.endswith('false') or
                            stripped.endswith('null')
                        )

                        # 检查下一行是否是新属性（以"开头）
                        next_is_property = next_line_stripped.startswith('"')

                        # 如果当前行以值结尾，下一行是新属性，则需要逗号
                        if ends_with_value and next_is_property:
                            needs_comma = True

                    # 添加逗号
                    if needs_comma:
                        fixed_lines.append(line.rstrip() + ',')
                    else:
                        fixed_lines.append(line)

                fixed = '\n'.join(fixed_lines)

                # 移除尾随逗号（在}或]之前）
                fixed = re.sub(r',(\s*[}\]])', r'\1', fixed)

                # 移除连续逗号
                fixed = re.sub(r',\s*,', ',', fixed)

                # 移除注释（如果有）
                fixed = re.sub(r'//.*?\n', '\n', fixed)
                fixed = re.sub(r'/\*.*?\*/', '', fixed, flags=re.DOTALL)

                return fixed

            # 尝试解析JSON，使用多次修复尝试
            resolution_data = None
            last_error = None

            for attempt in range(4):
                try:
                    if attempt == 0:
                        # 第一次尝试：原始JSON
                        resolution_data = json.loads(json_str)
                        break
                    elif attempt == 1:
                        # 第二次尝试：基本修复
                        json_str = repair_json_basic(original_json_str)
                        resolution_data = json.loads(json_str)
                        logger.info("JSON修复成功（基本修复）")
                        break
                    elif attempt == 2:
                        # 第三次尝试：激进修复
                        json_str = repair_json_aggressive(original_json_str)
                        resolution_data = json.loads(json_str)
                        logger.info("JSON修复成功（激进修复）")
                        break
                    else:
                        # 第四次尝试：使用json5或其他宽松解析器的替代方案
                        # 尝试手动修复特定位置的问题
                        json_str = repair_json_aggressive(original_json_str)
                        # 额外处理：移除所有可能的格式问题
                        json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
                        json_str = json_str.replace(',}', '}').replace(',]', ']')
                        resolution_data = json.loads(json_str)
                        logger.info("JSON修复成功（最终尝试）")
                        break
                except json.JSONDecodeError as je:
                    last_error = je
                    if attempt == 0:
                        logger.warning(f"JSON解析失败（尝试{attempt+1}/4）: {je}")
                        logger.debug(f"原始JSON前500字符: {original_json_str[:500]}")
                        # 保存完整JSON到文件用于调试
                        try:
                            import tempfile
                            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
                                f.write(original_json_str)
                                logger.info(f"完整JSON已保存到: {f.name}")
                        except:
                            pass
                    elif attempt < 3:
                        logger.warning(f"JSON解析失败（尝试{attempt+1}/4）: {je}")
                        # 显示错误位置附近的内容
                        error_pos = je.pos if hasattr(je, 'pos') else 0
                        context_start = max(0, error_pos - 50)
                        context_end = min(len(json_str), error_pos + 50)
                        logger.debug(f"错误位置附近: ...{json_str[context_start:context_end]}...")
                    else:
                        # 最后一次尝试失败
                        logger.error(f"JSON解析失败（所有尝试均失败）: {je}")
                        logger.error(f"错误位置: line {je.lineno}, column {je.colno}")
                        # 显示错误位置附近的内容
                        lines = json_str.split('\n')
                        if je.lineno <= len(lines):
                            # 显示错误行及其前后各2行
                            start_line = max(0, je.lineno - 3)
                            end_line = min(len(lines), je.lineno + 2)
                            logger.error(f"错误位置上下文（第{start_line+1}-{end_line}行）:")
                            for line_idx in range(start_line, end_line):
                                prefix = ">>> " if line_idx == je.lineno - 1 else "    "
                                logger.error(f"{prefix}{line_idx+1}: {lines[line_idx]}")
                            if je.lineno - 1 < len(lines):
                                error_line = lines[je.lineno - 1]
                                logger.error(f"错误位置: {' ' * (je.colno + 3)}^")
                        raise

            usage = {
                "tokens": response.get("usage", {}).get("total_tokens", 0),
                "latency_ms": int((time.time() - start_time) * 1000)
            }

            logger.info(f"LLM冲突裁决完成, tokens={usage['tokens']}, latency={usage['latency_ms']}ms")
            return resolution_data, usage

        except Exception as e:
            logger.error(f"LLM调用失败: {e}, 使用降级方案")
            # 降级方案：简单合并冲突
            resolved_issues = []
            for pair in conflict_pairs:
                resolved_issues.append({
                    "agent1_name": pair['agent1'],
                    "agent2_name": pair['agent2'],
                    "conflict_type": pair.get('conflict_type', 'unknown'),
                    "root_cause": "LLM调用失败，使用降级方案",
                    "evidence_strength": 0.5,
                    "confidence": pair.get('confidence', 0.5),
                    "resolved_comment": f"检测到{pair['agent1']}和{pair['agent2']}之间的冲突",
                    "resolved_suggestion": "建议人工复核",
                    "final_level": "Warning",
                    "needs_human_review": True
                })

            return {
                "resolved_issues": resolved_issues
            }, {
                "tokens": 0,
                "latency_ms": int((time.time() - start_time) * 1000)
            }

    def deduplicate_issues(self, resolved_issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not resolved_issues:
            return []

        seen: Dict[Any, Dict[str, Any]] = {}
        deduped: List[Dict[str, Any]] = []

        for issue in resolved_issues:
            key = (
                issue.get("conflict_type", ""),
                issue.get("final_level", ""),
                frozenset({issue.get("agent1_name", ""), issue.get("agent2_name", "")}),
            )
            if key in seen:
                existing = seen[key]
                if issue.get("confidence", 0) > existing.get("confidence", 0):
                    seen[key] = issue
                    deduped = [issue if d is existing else d for d in deduped]
            else:
                seen[key] = issue
                deduped.append(issue)

        return deduped

    def sort_by_priority(self, resolved_issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        def priority_key(issue: Dict[str, Any]) -> Any:
            level_score = LEVEL_PRIORITY.get(issue.get("final_level", "Info"), 0)
            confidence = issue.get("confidence", 0)
            needs_review = 1 if issue.get("needs_human_review", False) else 0
            return (-level_score, -confidence, -needs_review)

        return sorted(resolved_issues, key=priority_key)

    def compute_final_verdict(self, resolved_issues: List[Dict[str, Any]], agent_results: List[Dict[str, Any]],
                             evidence_validation: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """计算最终裁决结果

        Args:
            resolved_issues: 已解决的冲突列表
            agent_results: Agent评审结果列表
            evidence_validation: 证据验证结果（可选）

        Returns:
            包含平均分、级别分布、最终结论等的字典
        """
        # 计算加权平均分
        weighted_scores = []
        for result in agent_results:
            result_data = result.get("result", {})
            if isinstance(result_data, dict):
                score = self._coerce_score(result_data.get("score", 70))
                agent_info = result.get("agent_info", {})
                agent_name = agent_info.get("name", "未知")

                # 获取权重，默认为1.0
                weight = self.agent_weights.get(agent_name, 1.0)
                weighted_scores.append(score * weight)

        if weighted_scores:
            avg_score = sum(weighted_scores) / sum(self.agent_weights.get(r.get("agent_info", {}).get("name", "未知"), 1.0)
                                                  for r in agent_results if r.get("result"))
        else:
            avg_score = 70  # 默认分数

        # 根据证据验证分数调整平均分
        adjusted_score = avg_score
        if evidence_validation and isinstance(evidence_validation, dict):
            validation_score = evidence_validation.get("validation_score", 1.0)
            # 验证分数低于0.7时扣分，高于0.9时加分
            if validation_score < 0.7:
                penalty = (0.7 - validation_score) * 10  # 最多扣3分
                adjusted_score = max(0, avg_score - penalty)
            elif validation_score > 0.9:
                bonus = (validation_score - 0.9) * 5  # 最多加0.5分
                adjusted_score = min(100, avg_score + bonus)

            # 记录调整信息
            score_adjustment = adjusted_score - avg_score
            if abs(score_adjustment) > 0.1:
                logger.info(f"基于证据验证调整分数: {avg_score:.1f} -> {adjusted_score:.1f} (调整{score_adjustment:+.1f})")

        level_counts = {"Info": 0, "Warning": 0, "Critical": 0}
        for issue in resolved_issues:
            level = issue.get("final_level", "Info")
            level_counts[level] = level_counts.get(level, 0) + 1

        # 确定最终结论（与反思主流程同一套百分制四档等级说明）
        verdict = build_conflict_final_verdict_text(adjusted_score, level_counts)

        return {
            "average_score": round(adjusted_score, 1),
            "original_average_score": round(avg_score, 1),
            "level_distribution": level_counts,
            "verdict": verdict,
            "total_conflicts": len(resolved_issues),
            "needs_human_review_count": sum(1 for issue in resolved_issues if issue.get("needs_human_review", False)),
            "score_adjusted_by_evidence": abs(adjusted_score - avg_score) > 0.1,
            "evidence_validation_score": evidence_validation.get("validation_score", 1.0) if evidence_validation else None,
        }

    def generate_markdown_report(self, resolution_data: Dict[str, Any], paper_title: str = "未知论文") -> str:
        final_verdict = resolution_data.get("final_verdict", {})
        resolved_issues = resolution_data.get("resolved_issues", [])
        confidence = resolution_data.get("confidence_score", 0)
        evidence_validation = resolution_data.get("evidence_validation", {})

        lines = [
            "# 论文评审冲突裁决报告",
            "",
            f"**论文标题**: {paper_title}",
            f"**综合得分**: {final_verdict.get('average_score', 'N/A')}",
            f"**裁决置信度**: {confidence:.2f}",
            f"**最终结论**: {final_verdict.get('verdict', '待定')}",
            "",
            "---",
            "",
            "## 一、总体评价",
            "",
        ]

        level_dist = final_verdict.get("level_distribution", {})
        lines.append(
            f"本次评审共检测到 **{len(resolved_issues)}** 个冲突，"
            f"其中 Critical {level_dist.get('Critical', 0)} 个、"
            f"Warning {level_dist.get('Warning', 0)} 个、"
            f"Info {level_dist.get('Info', 0)} 个。"
        )

        review_count = final_verdict.get("needs_human_review_count", 0)
        if review_count > 0:
            lines.append(f"有 **{review_count}** 个问题建议人工复核。")

        lines.append("")

        # 添加证据验证结果
        if evidence_validation:
            valid_count = evidence_validation.get("valid_count", 0)
            invalid_count = evidence_validation.get("invalid_count", 0)
            total_quotes = evidence_validation.get("total_quotes", 0)
            validation_score = evidence_validation.get("validation_score", 0.0)

            lines.append("## 二、证据真实性验证")
            lines.append("")
            lines.append(f"系统对评审意见中的证据引用进行了真实性验证：")
            lines.append(f"- **验证结果**: {valid_count} 个有效引用 / {total_quotes} 个总引用")
            lines.append(f"- **验证分数**: {validation_score:.1%}")

            if invalid_count > 0:
                lines.append(f"- **无效引用**: {invalid_count} 个引用未在论文原文中找到")
                invalid_results = evidence_validation.get("invalid_results", [])
                if invalid_results:
                    lines.append("")
                    lines.append("**无效引用详情**:")
                    for invalid in invalid_results[:5]:  # 最多显示5个
                        agent_name = invalid.get("agent_name", "未知Agent")
                        quote_preview = invalid.get("clean_quote", invalid.get("evidence_quote", ""))
                        if len(quote_preview) > 50:
                            quote_preview = quote_preview[:50] + "..."
                        lines.append(f"  - **{agent_name}**: \"{quote_preview}\"")
                    if invalid_count > 5:
                        lines.append(f"  - ... 还有 {invalid_count - 5} 个无效引用")
            else:
                lines.append(f"- **所有证据引用均通过验证**")

            lines.append("")

        if resolved_issues:
            lines.append("## 三、冲突裁决详情")
            lines.append("")
            for idx, issue in enumerate(resolved_issues, 1):
                level = issue.get("final_level", "Info")
                level_icon = {"Critical": "[!]", "Warning": "[?]", "Info": "[i]"}.get(level, "[i]")
                lines.append(f"### {idx}. {level_icon} {issue.get('conflict_type', '未知类型')} ({level})")
                lines.append("")
                lines.append(f"- **冲突双方**: {issue.get('agent1_name', '?')} vs {issue.get('agent2_name', '?')}")
                if issue.get("root_cause"):
                    lines.append(f"- **根本原因**: {issue['root_cause']}")
                lines.append(f"- **裁决意见**: {issue.get('resolved_comment', '')}")
                lines.append(f"- **改进建议**: {issue.get('resolved_suggestion', '')}")
                if issue.get("needs_human_review"):
                    lines.append("- **需要人工复核**")
                lines.append("")

        lines.extend(
            [
                "## 四、最终建议",
                "",
                f"{final_verdict.get('verdict', '待定')}",
                "",
                "---",
                "*由 ReflectionJudge_ConflictResolver v1.0 自动生成*",
            ]
        )

        return "\n".join(lines)

    def save_markdown_report(self, markdown_content: str, output_dir: str = "reports",
                           filename: str = None, paper_title: str = "未知论文") -> str:
        """将Markdown报告保存到文件系统

        Args:
            markdown_content: Markdown报告内容
            output_dir: 输出目录（默认为reports）
            filename: 文件名（如果为None则自动生成）
            paper_title: 论文标题（用于生成文件名）

        Returns:
            保存的文件路径
        """
        import os
        from datetime import datetime

        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)

        # 生成文件名
        if filename is None:
            # 清理论文标题，移除特殊字符
            safe_title = "".join(c for c in paper_title if c.isalnum() or c in (' ', '-', '_')).rstrip()
            safe_title = safe_title[:50]  # 限制长度
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"report_{safe_title}_{timestamp}.md"

        filepath = os.path.join(output_dir, filename)

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(markdown_content)
            logger.info(f"Markdown报告已保存: {filepath}")
            return filepath
        except Exception as e:
            logger.error(f"保存Markdown报告失败: {str(e)}")
            return ""

    async def resolve_conflicts(self, request: ConflictResolutionRequest) -> ConflictResolutionResponse:
        try:
            if not request.payload:
                raise ValueError("payload不能为空")

            agent_results = request.payload.get("agent_results", [])
            paper_id = request.metadata.get("paper_id")
            paper_title = request.metadata.get("paper_title", "未知论文")
            paper_context = ""

            # 仅在database模式下从数据库读取数据
            if self.mode == "database" and not agent_results and paper_id:
                await self.db_client.connect()
                agent_results = await self.db_client.get_agent_results(paper_id)
                paper_context = await self.db_client.get_paper_content(paper_id)

            agent_results = self._normalize_agent_results(agent_results)

            # 获取论文内容用于证据验证（仅在database模式）
            if self.mode == "database" and not paper_context and paper_id:
                try:
                    await self.db_client.connect()
                    paper_context = await self.db_client.get_paper_content(paper_id)
                except Exception as e:
                    logger.warning(f"获取论文内容失败: {e}")

            # 幻觉过滤：验证evidence_quote（仅在启用时执行）
            if self.enable_hallucination_filter:
                evidence_validation = self.validate_evidence_quotes(agent_results, paper_context)

                # 对Warning/Critical级别的无证据结果进行过滤
                enforcement = self.enforce_evidence_linking(
                    agent_results,
                    evidence_validation,
                    paper_context_available=bool(paper_context),
                )
                agent_results = enforcement["filtered_agent_results"]
            else:
                logger.info("幻觉过滤已禁用，跳过证据验证和证据链接强制")
                evidence_validation = {"valid_count": 0, "invalid_count": 0, "invalid_results": [], "validation_score": 1.0, "message": "幻觉过滤已禁用"}
                enforcement = {"filtered_agent_results": agent_results, "removed_results": [], "removed_count": 0, "original_count": len(agent_results), "remaining_count": len(agent_results), "paper_context_available": bool(paper_context)}

            if not agent_results:
                result_data = {
                    "conflicts_resolved": False,
                    "resolved_issues": [],
                    "confidence_score": 1.0,
                    "tags": ["Executive_Summary"],
                    "evidence_validation": evidence_validation,
                    "evidence_enforcement": enforcement,
                    "final_verdict": {
                        "average_score": 0,
                        "level_distribution": {},
                        "verdict": "无Agent结果可供裁决",
                        "total_conflicts": 0,
                        "needs_human_review_count": 0,
                    },
                    "message": "无Agent结果可供裁决",
                }
                return ConflictResolutionResponse(
                    request_id=request.request_id,
                    result=self._attach_result_json(result_data),
                    usage={"tokens": 0, "latency_ms": 0},
                )

            conflicts = self.detect_conflicts(
                agent_results,
                conflict_threshold=request.config.get("conflict_threshold"),
                score_diff_threshold=request.config.get("score_diff_threshold"),
            )

            # 如果启用了always_use_llm模式，即使没有冲突也调用LLM
            if not conflicts and not self.always_use_llm:
                logger.info("无冲突且未启用always_use_llm模式，使用快速路径")
                result_data = {
                    "conflicts_resolved": False,
                    "resolved_issues": [],
                    "confidence_score": 0.95,
                    "tags": ["Executive_Summary", "Score_Calibration"],
                    "evidence_validation": evidence_validation,
                    "evidence_enforcement": enforcement,
                    "final_verdict": self.compute_final_verdict([], agent_results, evidence_validation),
                    "paper_id": paper_id,
                }
                result_data["markdown_report"] = self.generate_markdown_report(result_data, paper_title)
                # 保存Markdown报告到文件
                if result_data["markdown_report"]:
                    saved_path = self.save_markdown_report(
                        result_data["markdown_report"],
                        paper_title=paper_title
                    )
                    if saved_path:
                        result_data["markdown_report_path"] = saved_path

                return ConflictResolutionResponse(
                    request_id=request.request_id,
                    result=self._attach_result_json(result_data),
                    usage={"tokens": 0, "latency_ms": 50},
                )

            # 调用LLM进行裁决（有冲突或启用了always_use_llm模式）
            if self.always_use_llm and not conflicts:
                logger.info("启用always_use_llm模式，即使无冲突也调用LLM进行综合评估")

            resolution_data, usage = await self.resolve_conflicts_with_llm(request, conflicts, paper_context)
            resolution_data = self._normalize_resolution_data(resolution_data)

            humanize_resolved_issues_group_labels(resolution_data.get("resolved_issues", []))

            resolved_issues = self.deduplicate_issues(resolution_data.get("resolved_issues", []))
            resolution_data["resolved_issues"] = self.sort_by_priority(resolved_issues)
            resolution_data["final_verdict"] = self.compute_final_verdict(resolution_data["resolved_issues"], agent_results, evidence_validation)
            resolution_data["tags"] = ["Executive_Summary", "Critical_Fix_List", "Score_Calibration"]
            resolution_data["evidence_validation"] = evidence_validation
            resolution_data["evidence_enforcement"] = enforcement
            resolution_data["markdown_report"] = self.generate_markdown_report(resolution_data, paper_title)
            # 保存Markdown报告到文件
            if resolution_data["markdown_report"]:
                saved_path = self.save_markdown_report(
                    resolution_data["markdown_report"],
                    paper_title=paper_title
                )
                if saved_path:
                    resolution_data["markdown_report_path"] = saved_path

            if paper_id:
                resolution_data["paper_id"] = paper_id
                # 注意：不在这里保存到数据库，由run.py统一处理
                # 仅在database模式下保存到数据库
                # if self.mode == "database":
                #     await self.db_client.save_reflection_result(request.request_id, resolution_data, usage)

            return ConflictResolutionResponse(
                request_id=request.request_id,
                result=self._attach_result_json(resolution_data),
                usage=usage,
            )

        except ValueError as exc:
            logger.error("冲突裁决失败: %s", str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("冲突裁决失败: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=f"内部服务错误: {exc}") from exc
        finally:
            # 注意：当由run.py调用时，数据库连接由run.py统一管理，不在此处断开
            # 仅在独立运行（如FastAPI端点）且自行建立了连接时才断开
            pass


resolver = ConflictResolver()


@app.post("/api/resolve_conflicts", response_model=ConflictResolutionResponse)
async def resolve_conflicts_endpoint(request: ConflictResolutionRequest):
    return await resolver.resolve_conflicts(request)


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "conflict_resolver"}
