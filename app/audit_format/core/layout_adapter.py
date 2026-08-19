from typing import Dict, Any, List
import hashlib
from .layout_schema import LayoutIssue


def with_anchor(issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    output = []
    for issue in issues:
        page_num = issue.get("page_num", 0)
        bbox = issue.get("bbox", [0, 0, 0, 0])
        issue_type = issue.get("issue_type", "issue")
        raw = f"{issue_type}-{page_num}-{bbox}"
        anchor_id = hashlib.md5(raw.encode("utf-8")).hexdigest()
        issue["anchor_id"] = anchor_id
        issue["highlight"] = bbox
        output.append(issue)
    return output


def normalize_issues(raw_issues: List[Dict[str, Any]]) -> List[LayoutIssue]:
    return [LayoutIssue(**i) for i in raw_issues]
