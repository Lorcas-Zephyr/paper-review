from fastapi import APIRouter
from core.layout_analysis import LayoutAnalyzer
from core.layout_payload import build_layout_payload
from core.layout_frontend_adapter import issues_to_frontend_payload


router = APIRouter()
layout_analyzer = LayoutAnalyzer()


@router.post("/layout/analyze")
async def analyze_layout(payload: dict):
    content = payload.get("content")
    result = await layout_analyzer.analyze(content)
    elements = result.get("elements", [])
    issues = result.get("layout_result", {}).get("layout_issues", [])
    anchors = issues
    parse_errors = result.get("parse_errors", [])
    parse_report = result.get("parse_report", {})
    frontend = issues_to_frontend_payload(issues)
    return build_layout_payload(
        elements,
        issues,
        anchors,
        parse_errors=parse_errors,
        parse_report=parse_report,
        frontend=frontend,
    ).model_dump()
