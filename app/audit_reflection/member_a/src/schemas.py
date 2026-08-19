import uuid
from datetime import datetime
from enum import Enum
from typing import Dict, Any, List

from pydantic import BaseModel, Field, field_validator


class AuditLevel(str, Enum):
    INFO = "Info"
    WARNING = "Warning"
    CRITICAL = "Critical"


class ConflictType(str, Enum):
    DIRECT_CONTRADICTION = "direct_contradiction"
    CONTEXT_DEPENDENT = "context_dependent"
    MEASUREMENT_DIFFERENCE = "measurement_difference"
    SCOPE_MISMATCH = "scope_mismatch"


class AgentResultData(BaseModel):
    """Agent返回的result字段（对应文档中Agent审计结果返回协议的result部分）"""
    score: int = 70
    audit_level: str = "Info"
    comment: str = ""
    suggestion: str = ""
    tags: List[str] = Field(default_factory=list)


class AgentResult(BaseModel):
    """单个Agent的完整返回结果（对应文档中Agent审计结果返回协议）"""
    request_id: str = ""
    agent_info: Dict[str, str]   # {"name": "Methodology_Agent", "version": "v1.2"}
    result: AgentResultData
    usage: Dict[str, int] = Field(default_factory=lambda: {"tokens": 0, "latency_ms": 0})


class ConflictEvidence(BaseModel):
    agent1_name: str
    agent2_name: str
    agent1_comment: str
    agent2_comment: str
    conflict_type: ConflictType
    root_cause: str = ""
    evidence_strength: float = Field(0.0, ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    resolved_comment: str
    resolved_suggestion: str
    final_level: AuditLevel
    needs_human_review: bool = False


class ConflictResolutionRequest(BaseModel):
    """冲突裁决请求（对应文档中Orchestrator -> Agent的上传协议）"""
    request_id: str = Field(
        default_factory=lambda: f"req_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:8]}"
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)
    payload: Dict[str, Any]   # 包含agent_results列表
    config: Dict[str, Any] = Field(default_factory=lambda: {
        "temperature": 0.3,
        "max_tokens": 1000,
        "conflict_threshold": 0.7
    })


class ResolvedIssue(BaseModel):
    """单个冲突的裁决结果"""
    agent1_name: str
    agent2_name: str
    conflict_type: str
    root_cause: str = ""
    evidence_strength: float = 0.0
    confidence: float = 0.0
    resolved_comment: str = ""
    resolved_suggestion: str = ""
    final_level: str = "Info"
    needs_human_review: bool = False
    score: int = 70


class ConflictResolutionResponse(BaseModel):
    """冲突裁决响应（对应文档中反思评估组的输出规范）"""
    request_id: str
    agent_info: Dict[str, str] = {
        "name": "ReflectionJudge_ConflictResolver",
        "version": "v1.0"
    }
    result: Dict[str, Any]
    usage: Dict[str, int]

    @field_validator('result')
    @classmethod
    def validate_result(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        required_keys = ['conflicts_resolved', 'resolved_issues', 'confidence_score']
        for key in required_keys:
            if key not in v:
                raise ValueError(f"Missing required key in result: {key}")
        return v
