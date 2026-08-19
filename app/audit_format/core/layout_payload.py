from typing import Any, Dict, List
from pydantic import BaseModel, Field


class LayoutPayload(BaseModel):
    elements: List[Dict[str, Any]] = Field(default_factory=list)
    layout_issues: List[Dict[str, Any]] = Field(default_factory=list)
    anchors: List[Dict[str, Any]] = Field(default_factory=list)
    parse_errors: List[Dict[str, Any]] = Field(default_factory=list)
    parse_report: Dict[str, Any] = Field(default_factory=dict)
    frontend: Dict[str, Any] = Field(default_factory=dict)


def build_layout_payload(elements, layout_issues, anchors, parse_errors=None, parse_report=None, frontend=None) -> LayoutPayload:
    return LayoutPayload(
        elements=[e.model_dump() for e in elements],
        layout_issues=layout_issues,
        anchors=anchors,
        parse_errors=parse_errors or [],
        parse_report=parse_report or {},
        frontend=frontend or {},
    )
