from typing import List, Optional
from pydantic import BaseModel


class LayoutIssue(BaseModel):
    issue_type: str
    severity: str
    page_num: int
    bbox: List[float]
    evidence: Optional[str] = None
    message: Optional[str] = None
    location: Optional[dict] = None  # {"section": "4.2", "line_start": 45}
    anchor_id: Optional[str] = None
    highlight: Optional[List[float]] = None


class Anchor(BaseModel):
    anchor_id: str
    page_num: int
    bbox: List[float]
    highlight: List[float]
