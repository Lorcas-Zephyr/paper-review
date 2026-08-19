from typing import Dict, Any, List


def issues_to_frontend_payload(issues: List[Dict[str, Any]]) -> Dict[str, Any]:
    highlights = []
    for issue in issues:
        highlights.append(
            {
                "anchor_id": issue.get("anchor_id"),
                "page_num": issue.get("page_num"),
                "bbox": issue.get("bbox"),
                "highlight": issue.get("highlight", issue.get("bbox")),
                "severity": issue.get("severity"),
                "issue_type": issue.get("issue_type"),
                "message": issue.get("message"),
                "evidence": issue.get("evidence"),
            }
        )
    return {"highlights": highlights}
