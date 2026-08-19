from typing import Dict, Any
from .layout_frontend_adapter import issues_to_frontend_payload


def merge_layout_to_response(layout_result: Dict[str, Any]) -> Dict[str, Any]:
    issues = layout_result.get("layout_issues", [])
    frontend_payload = issues_to_frontend_payload(issues)
    return {
        "layout_issues": issues,
        "frontend": frontend_payload,
    }
