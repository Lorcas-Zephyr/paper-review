"""
冲突裁决后校准 final_level：避免将纯格式/术语/书写类问题标为 Critical；
并剔除实为「合规/表扬」却被误列为问题的条目。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# 仍应保持 Critical 的语义（数据可信度、统计硬伤、学术不端等）
_RETAIN_CRITICAL = re.compile(
    r"(伪造|篡改|造假|捏造|学术不端|数据造假|选择性报告|结论不成立|逻辑谬误|"
    r"未报告\s*p|P\s*值.*未|缺乏\s*显著性|缺乏统计检验|样本量不足.*(?:结论|显著)|"
    r"实验(?:数据|结果).*(?:矛盾|不符|虚假)|数值.*(?:矛盾|与.*不符)|"
    r"(?:正文|图表).*(?:严重|实质).*(?:矛盾|不符))",
    re.I,
)

# 典型应降级为 Warning：关键词/摘要/大小写/术语与缩写体系统一等（含「首次全称后括注缩写」类前后文风差异）
_BENIGN_CRITICAL = re.compile(
    r"(关键词|英文关键词|英文摘要|中文摘要|摘要中|首字母|大小写|小写|大写|"
    r"专有名词|书写规范|排版|标点|拼写|"
    r"术语.*(?:不一致|不统一|对应|混用)|(?:英文|中文).*术语|"
    r"标题中|标题与正文|正文中.{0,100}频繁|频繁使用|组合不一致|"
    r"全称.{0,50}缩写|缩写.{0,30}全称|括注.*(?:缩写|英文)|"
    r"Mixture[-\s]*of[-\s]*Experts|\bMoE\b|混合专家|"
    r"Parameter[-\s]*Efficient|PEFT|参数高效微调|"
    r"Integrated\s*Learning|Ensemble\s*Learning|集成学习|"
    r"Internet\s+of\s+things|Internet\s+of\s+Things|\bIoT\b|"
    r"缩写.*(?:不一致|不统一)|引用格式|参考文献格式|"
    r"术语缩写|缩写定义|定义格式|"
    r"定义格式不一致|格式不一致.*(?:缩写|定义)|"
    r"(?:后文|前文|多处).{0,12}(?:缩写|定义|使用)|"
    r"(?:全称|英文全称).{0,40}(?:缩写|括注)|"
    r"数值一致|正文.*(?:图表|表).{0,60}一致|表[\d.\-]+.*(?:一致|相同)|"
    r"误差棒|不确定性范围|视觉准确性|数据呈现完整性|"
    r"正向确认|裁决为内部意见整合|不应标为Critical|过于严厉)",
    re.I,
)


def _acknowledged_compliance(text: str) -> bool:
    """
    裁决/评阅文字中已承认「符合常见规范」等，却仍标 Critical 的情况，降为 Warning。
    排除「不符合/未符合常见规范」。
    """
    t = text or ""
    if "不符合常见规范" in t or "未符合常见规范" in t:
        return False
    if "符合常见规范" in t:
        return True
    if "符合规范" in t and ("但可优化" in t or "可优化清晰度" in t or "可优化" in t):
        return True
    return False


def calibrate_resolved_issue(issue: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(issue)
    if out.get("final_level") != "Critical":
        return out
    text = " ".join(
        [
            str(out.get("resolved_comment") or ""),
            str(out.get("root_cause") or ""),
            str(out.get("resolved_suggestion") or ""),
        ]
    )
    if not text.strip():
        return out
    if _RETAIN_CRITICAL.search(text):
        return out
    if out.get("final_level") == "Critical" and _acknowledged_compliance(text):
        out["final_level"] = "Warning"
        logger.info(
            "severity_calibration: Critical -> Warning (acknowledged compliance): %s",
            text.strip()[:120].replace("\n", " "),
        )
        return out
    if _BENIGN_CRITICAL.search(text):
        out["final_level"] = "Warning"
        logger.info(
            "severity_calibration: Critical -> Warning (format/terminology): %s",
            text.strip()[:120].replace("\n", " "),
        )
    return out


def calibrate_resolved_issues(issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not issues:
        return []
    return [calibrate_resolved_issue(dict(x)) for x in issues]


def _issue_combined_text(issue: Dict[str, Any]) -> str:
    return " ".join(
        [
            str(issue.get("resolved_comment") or ""),
            str(issue.get("root_cause") or ""),
            str(issue.get("resolved_suggestion") or ""),
        ]
    ).strip()


def is_compliance_praise_only(text: str) -> bool:
    """
    判断是否为「说明已符合规范」的褒义描述，不应作为 Critical/Warning 问题展示。
    例如：「缩略语首次出现时给出了全称定义，符合规范。」
    """
    t = (text or "").strip()
    if len(t) < 8:
        return False
    # 先排除明显在指出缺陷的句子
    if re.search(
        r"(未|没有|无)(?:能|能)?(?:给出|定义|注明|列出|使用)|"
        r"(不符合|未符合|未能|缺乏|缺少|错误|不一致|不当|"
        r"存在问题|存在风险|存在缺陷|不应|不建议|禁止|"
        r"问题在于|不足之处)",
        t,
    ):
        return False
    # 合规/表扬类表述
    return bool(
        re.search(
            r"(符合规范|符合要求|符合标准|较为规范|表述规范|定义清晰|"
            r"给出了.{0,20}全称|全称定义|"
            r"首次出现时.{0,16}(?:给出|已给|提供了)|"
            r"已正确(?:给出|使用)|无不当|建议保持(?:现状)?)",
            t,
        )
    )


def _prioritized_combined_text(iss: Any) -> str:
    return " ".join(
        [
            str(getattr(iss, "description", None) or ""),
            str(getattr(iss, "evidence", None) or ""),
        ]
    ).strip()


def _clone_prioritized_downgrade(iss: Any) -> Any:
    return type(iss)(
        description=getattr(iss, "description", "") or "",
        priority="warning",
        agents=list(getattr(iss, "agents", None) or []),
        evidence=getattr(iss, "evidence", None),
    )


def recalibrate_prioritized_issue_buckets(
    critical: List[Any],
    major: List[Any],
    minor: List[Any],
) -> tuple[List[Any], List[Any], List[Any]]:
    """
    对已从冲突裁决、audit_records、sorted_results 汇入的 PrioritizedIssue 再校准。
    剔除纯表扬；Critical 按 calibrate_resolved_issue 逻辑降为 major（priority=warning）。
    """
    if not critical:
        return critical, major, minor
    new_critical: List[Any] = []
    extra_major: List[Any] = []
    for iss in critical:
        t = _prioritized_combined_text(iss)
        if not t:
            new_critical.append(iss)
            continue
        if is_compliance_praise_only(t):
            logger.info(
                "severity_calibration: dropped praise-only PrioritizedIssue: %s",
                t[:120].replace("\n", " "),
            )
            continue
        fake = {
            "final_level": "Critical",
            "resolved_comment": getattr(iss, "description", "") or "",
            "root_cause": getattr(iss, "evidence", None) or "",
        }
        adj = calibrate_resolved_issue(fake)
        if adj.get("final_level") != "Critical":
            extra_major.append(_clone_prioritized_downgrade(iss))
            logger.info(
                "severity_calibration: PrioritizedIssue critical -> major: %s",
                t[:100].replace("\n", " "),
            )
        else:
            new_critical.append(iss)
    return new_critical, list(major) + extra_major, minor


def strip_compliance_praise_findings(issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not issues:
        return []
    out: List[Dict[str, Any]] = []
    for x in issues:
        t = _issue_combined_text(x)
        if is_compliance_praise_only(t):
            logger.info(
                "severity_calibration: dropped praise/compliance-only finding: %s",
                t[:120].replace("\n", " "),
            )
            continue
        out.append(x)
    return out
