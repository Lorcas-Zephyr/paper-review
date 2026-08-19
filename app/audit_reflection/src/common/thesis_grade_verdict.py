"""
学位论文综合质量等级（百分制四档）与反思结论文本。
与《软件工程硕士研究生学位论文质量评价指标》第 12 节口径一致。
"""
from __future__ import annotations


def thesis_grade_paragraph(score: float) -> str:
    """按综合得分给出等级标题 + 该档文字说明（唯一事实来源，供反思与冲突裁决共用）。"""
    try:
        s = float(score)
    except (TypeError, ValueError):
        s = 0.0
    s = max(0.0, min(100.0, s))
    if s >= 90:
        return (
            "【综合质量等级：优秀（≥90分）】"
            "选题属前沿；文献综述全面且具有批判性；具有明显的新方法或新见解；"
            "实验可靠且对比充分；文字表达严谨、无低级错误。"
        )
    if s >= 80:
        return (
            "【综合质量等级：良好（80–89分）】"
            "选题难度适中；基本了解国内外动态；有一定新意；实验方案合理；仅有极少量文字瑕疵。"
        )
    if s >= 70:
        return (
            "【综合质量等级：一般（70–79分）】"
            "基本达到硕士水平，但创新性不强，实验对比不够充分，文字排版存在较多不规范之处。"
        )
    return (
        "【综合质量等级：较差/不通过（<70分）】"
        "选题难度不够；拼凑痕迹明显；关键技术无横向对比；文字错误率极高。"
    )


def thesis_grade_suffix_for_prioritized_issues(
    *,
    has_critical: bool,
    major_issue_count: int,
) -> str:
    """在等级说明之后追加与审计结果强度一致的处置建议（短句）。"""
    if has_critical:
        return " 多组审计存在 Critical 级别问题，须优先修订后再议录用。"
    if major_issue_count >= 3:
        return " 存在多项重要审计意见，建议分项修改后提交。"
    if major_issue_count > 0:
        return " 存在若干重要审计意见，建议按意见小修后录用。"
    return ""


def build_reflection_verdict(
    final_score: float,
    *,
    has_critical: bool,
    major_issue_count: int,
    incomplete_note: str = "",
) -> str:
    """反思评估最终对用户展示的 verdict 字符串。"""
    body = thesis_grade_paragraph(final_score) + thesis_grade_suffix_for_prioritized_issues(
        has_critical=has_critical,
        major_issue_count=major_issue_count,
    )
    note = (incomplete_note or "").strip()
    if note:
        return f"{note} {body}"
    return body


def build_conflict_final_verdict_text(
    adjusted_score: float,
    level_counts: dict,
) -> str:
    """
    冲突裁决模块内 final_verdict.verdict，与主流程同一套等级口径；
    level_counts: Info / Warning / Critical 计数（来自已裁决 issues）。
    """
    base = thesis_grade_paragraph(adjusted_score)
    c = int(level_counts.get("Critical", 0) or 0)
    w = int(level_counts.get("Warning", 0) or 0)
    if c > 0:
        return base + " 冲突裁决汇总中存在 Critical 级结论，请结合明细优先处理。"
    if w >= 3:
        return base + " 冲突裁决汇总中 Warning 较多，建议逐项回应。"
    if w > 0:
        return base + " 冲突裁决汇总中存在少量 Warning，建议修订后录用。"
    return base
