import yaml
import os
from typing import Dict, Any

RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "rules.yaml")

def load_rules() -> Dict[str, Any]:
    if not os.path.exists(RULES_PATH):
        return {}
    with open(RULES_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

from typing import List, Any
import re
from .layout_schema import LayoutIssue


def check_citation_reference_match(citations: List[Any], references: List[Any]) -> List[LayoutIssue]:
    def _is_year_like(num: str) -> bool:
        if not num or not num.isdigit() or len(num) != 4:
            return False
        try:
            v = int(num)
        except Exception:
            return False
        return 1800 <= v <= 2100

    ref_nums = set()
    for r in references:
        m = re.match(r"^\s*(?:\[\s*(\d+)\s*\]|［\s*(\d+)\s*］|\(\s*(\d+)\s*\)|（\s*(\d+)\s*）|(\d+)\.|(\d+)\s)", r.content)
        if m:
            num = next((g for g in m.groups() if g is not None), None)
            if num and str(num).isdigit():
                v = int(num)
                if v == 0:
                    continue
                if v >= 1000:
                    continue
                if v > 300:
                    continue
                if _is_year_like(str(v)):
                    continue
                ref_nums.add(str(v))
    max_ref_num = 0
    try:
        if ref_nums:
            max_ref_num = max(int(n) for n in ref_nums if str(n).isdigit())
    except Exception:
        max_ref_num = 0
    if max_ref_num == 0 and references:
        try:
            max_ref_num = max(1, len(references))
        except Exception:
            max_ref_num = 0
    issues: List[LayoutIssue] = []
    for c in citations:
        content = str(getattr(c, "content", "") or "")
        if not content:
            continue
        if re.search(r"[（(].*\d{4}[a-z]?[)）]", content) and re.search(r"[A-Za-z]", content):
            continue

        nums: List[str] = []
        for inner in re.findall(r"[\[［]([^\]］]{1,50})[\]］]", content):
            parts = [p for p in re.split(r"[,\s;，；]+", inner.strip()) if p]
            for p in parts:
                m_range = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", p)
                if m_range:
                    start = int(m_range.group(1))
                    end = int(m_range.group(2))
                    if 0 < start <= end and (end - start) <= 50:
                        for n in range(start, end + 1):
                            s = str(n)
                            if s != "0" and not _is_year_like(s):
                                nums.append(s)
                    continue
                if re.match(r"^\d+$", p):
                    if p != "0" and not _is_year_like(p):
                        nums.append(p)
        if not nums:
            continue
        try:
            if any(int(n) >= 1000 for n in nums):
                continue
            if any(int(n) >= 150 for n in nums):
                continue
            if max_ref_num > 0:
                nums = [n for n in nums if int(n) <= max_ref_num + 10]
        except Exception:
            continue
        missing = sorted({n for n in nums if n not in ref_nums}, key=lambda x: int(x))
        if missing:
            issues.append(
                LayoutIssue(
                    issue_type="Citation_Visual_Fault",
                    severity="Warning",
                    page_num=c.page_num,
                    bbox=c.bbox,
                    evidence=content,
                    message=f"引用标注在参考文献区未找到对应条目: {', '.join(missing)}",
                    location={"section": "unknown", "line_start": 0}
                )
            )
    return issues
