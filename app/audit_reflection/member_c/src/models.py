from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum

class AgentType(str, Enum):
    FORMAT = "format"
    LOGIC = "logic"
    CODE = "code"
    EXPERIMENT = "experiment"
    LITERATURE = "literature"

class AgentResult(BaseModel):
    agent_name: AgentType
    result_json: Dict[str, Any]
    score: float = Field(ge=0, le=100)
    audit_level: str  # "critical", "major", "minor"
    evidence_quotes: List[str] = []
    confidence: float = 1.0

class ContradictionIssue(BaseModel):
    agents: List[str]
    issue: str
    confidence: float

class ValidationReport(BaseModel):
    quote: str
    exists: bool
    location: Optional[str] = None
    confidence: float

class HallucinationDetail(BaseModel):
    quote: str
    exists: bool
    location: Optional[str] = None
    confidence: float

class ProcessedResult(AgentResult):
    hallucination_details: Optional[List[HallucinationDetail]] = None
    confidence: float = 1.0  # 更新后的置信度

class PrioritizedIssue(BaseModel):
    description: str
    priority: str  # critical / major / minor
    agents: List[str]
    evidence: Optional[str] = None

class MentorDialogue(BaseModel):
    role: str
    field: str
    conversation: List[Dict[str, str]]  # [{"role": "mentor", "content": "..."}, ...]
    quality_score: Optional[float] = None

class ReflectionResult(BaseModel):
    paper_id: str
    final_score: float
    verdict: str  # 例如 "需要修改后重审"
    critical_issues: List[PrioritizedIssue] = []
    major_issues: List[PrioritizedIssue] = []
    minor_issues: List[PrioritizedIssue] = []
    needs_human_review: bool = False
    human_review_reason: Optional[str] = None
    mentor_dialogue: Optional[MentorDialogue] = None
    plugin_metadata: Dict[str, Any] = Field(default_factory=dict)

class ReflectionTask(BaseModel):
    request_id: str
    metadata: Dict[str, Any]
    # 可能包含 paper_id 等
