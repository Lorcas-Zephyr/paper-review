import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException

from .database import DatabaseClient
from .llm_client import LLMClient
from .schemas import ConflictResolutionRequest, ConflictResolutionResponse, ConflictType

logger = logging.getLogger(__name__)

app = FastAPI(title="Reflection Judge - Conflict Resolver")

DEFAULT_SCORE_DIFF_THRESHOLD = 20
LEVEL_PRIORITY = {"Info": 0, "Warning": 1, "Critical": 2}


class ConflictResolver:
    def __init__(self):
        self.llm_client = LLMClient()
        self.db_client = DatabaseClient()
        self.conflict_patterns = self._load_conflict_patterns()
        # 审计组权重配置（根据重要性调整）
        self.agent_weights = {
            "逻辑审计组": 1.2,
            "代码审计组": 1.1,
            "实验数据组": 1.1,
            "文献真实性组": 1.0,
            "格式审计组": 0.8
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

        # 检查是否为新格式（包含group_id和audit_results）
        if isinstance(parsed, dict) and "group_id" in parsed and "audit_results" in parsed:
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
            if "group_id" in item and "audit_results" in item:
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
        """将新格式（group_id + audit_results）转换为旧格式"""
        normalized = []
        group_id = new_format_data.get("group_id", 0)
        audit_results = new_format_data.get("audit_results", [])

        # 组名映射
        group_name_map = {
            2: "格式审计组",
            3: "逻辑审计组",
            4: "代码审计组",
            5: "实验数据组",
            6: "文献真实性组"
        }

        group_name = group_name_map.get(group_id, f"Group_{group_id}")

        for idx, audit_item in enumerate(audit_results):
            if not isinstance(audit_item, dict):
                continue

            # 映射字段
            score = self._coerce_score(audit_item.get("score", 70))
            level = audit_item.get("level", "Info")
            comment = audit_item.get("description", "")
            point = audit_item.get("point", "")

            # 合并comment和point
            full_comment = f"{point}: {comment}" if point else comment

            normalized.append({
                "request_id": f"req_{group_id}_{idx}",
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
                    "location": audit_item.get("location", {})
                },
                "usage": {
                    "tokens": 0,
                    "latency_ms": 0
                }
            })

        logger.info(f"转换新格式数据: group_id={group_id}, 转换了{len(normalized)}条结果")
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

            # 清理证据引用：移除可能的前缀如"原文第4.2节提到："
            clean_quote = self._clean_evidence_quote(evidence_quote)
            if not clean_quote:
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

        # 确定最终结论
        if level_counts["Critical"] > 0:
            verdict = "存在关键问题，建议大修后重新提交（Major Revision）"
        elif level_counts["Warning"] >= 3:
            verdict = "存在多处需要关注的问题，建议修改后录用（Minor Revision with conditions）"
        elif level_counts["Warning"] > 0:
            verdict = "存在少量问题，建议小修后录用（Minor Revision）"
        elif adjusted_score >= 85:
            verdict = "论文质量良好，建议直接录用（Accept）"
        else:
            verdict = "论文基本合格，建议小修后录用（Minor Revision）"

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

            if not agent_results and paper_id:
                await self.db_client.connect()
                agent_results = await self.db_client.get_agent_results(paper_id)
                paper_context = await self.db_client.get_paper_content(paper_id)

            agent_results = self._normalize_agent_results(agent_results)

            # 获取论文内容用于证据验证
            if not paper_context and paper_id:
                try:
                    await self.db_client.connect()
                    paper_context = await self.db_client.get_paper_content(paper_id)
                except Exception as e:
                    logger.warning(f"获取论文内容失败: {e}")

            # 幻觉过滤：验证evidence_quote
            evidence_validation = self.validate_evidence_quotes(agent_results, paper_context)

            # ?????Warning/Critical ???????????????
            enforcement = self.enforce_evidence_linking(
                agent_results,
                evidence_validation,
                paper_context_available=bool(paper_context),
            )
            agent_results = enforcement["filtered_agent_results"]

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

            if not conflicts:
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

            resolution_data, usage = await self.llm_client.resolve_conflicts_with_llm(request, conflicts, paper_context)
            resolution_data = self._normalize_resolution_data(resolution_data)

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
                await self.db_client.save_resolution_result(request.request_id, resolution_data, usage)

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
            await self.db_client.disconnect()


resolver = ConflictResolver()


@app.post("/api/resolve_conflicts", response_model=ConflictResolutionResponse)
async def resolve_conflicts_endpoint(request: ConflictResolutionRequest):
    return await resolver.resolve_conflicts(request)


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "conflict_resolver"}
