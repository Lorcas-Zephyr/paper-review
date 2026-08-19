"""
统一的数据模型定义
整合了反思评估组四位成员的数据模型
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, validator


# ==================== 枚举类型定义 ====================
class AuditLevel(str, Enum):
    """审计级别"""
    INFO = "Info"
    WARNING = "Warning"
    CRITICAL = "Critical"


class ConflictType(str, Enum):
    """冲突类型"""
    DIRECT_CONTRADICTION = "direct_contradiction"
    CONTEXT_DEPENDENT = "context_dependent"
    MEASUREMENT_DIFFERENCE = "measurement_difference"
    SCOPE_MISMATCH = "scope_mismatch"


class AgentType(str, Enum):
    """Agent类型（对应agent_code）"""
    FMT = "FMT"       # 格式审计
    REF = "REF"       # 文献审计
    EXP = "EXP"       # 实验数据
    LOG = "LOG"       # 逻辑审计
    # 兼容旧值
    FORMAT = "format"
    LOGIC = "logic"
    CODE = "code"
    EXPERIMENT = "experiment"
    LITERATURE = "literature"


# ==================== 成员B：重复过滤和幻觉过滤模型 ====================
@dataclass
class Section:
    """论文切片结构：section -> paragraphs"""
    section_id: str
    title: str
    paragraphs: List[str]


@dataclass
class EvidenceItem:
    """上游审计条目"""
    item_id: str
    agent: str
    claim_text: str
    quote: Optional[str] = None
    evidence_hint: Optional[str] = None
    upstream_confidence: Optional[float] = None


@dataclass
class EvidenceSpan:
    """证据定位信息"""
    section_id: str
    paragraph_index: int
    char_start: int
    char_end: int


@dataclass
class EvidenceValidationResult:
    """证据验证结果"""
    item_id: str
    exists: bool
    method: str
    confidence: float
    evidence_spans: List[EvidenceSpan]
    matched_text: str
    notes: str


@dataclass
class DedupItem:
    """去重输入条目"""
    item_id: str
    agent: str
    text: str


@dataclass
class DedupCluster:
    """聚类结果"""
    cluster_id: int
    representative_item: DedupItem
    members: List[DedupItem]
    cluster_stats: Dict[str, object]


@dataclass
class DedupResult:
    """去重结果"""
    paper_id: str
    clusters: List[DedupCluster]
    noise: List[DedupItem]


# ==================== 成员A：冲突裁决模型 ====================
class AgentResultData(BaseModel):
    """Agent返回的result字段"""
    score: int = 70
    audit_level: str = "Info"
    comment: str = ""
    suggestion: str = ""
    tags: List[str] = Field(default_factory=list)
    point: str = ""
    description: str = ""
    evidence_quote: str = ""
    location: Dict[str, Any] = Field(default_factory=dict)


class AgentResult(BaseModel):
    """单个Agent的完整返回结果"""
    request_id: str = ""
    agent_info: Dict[str, str]
    result: AgentResultData
    usage: Dict[str, int] = Field(default_factory=lambda: {"tokens": 0, "latency_ms": 0})


class ConflictResolutionRequest(BaseModel):
    """冲突裁决请求"""
    request_id: str = Field(
        default_factory=lambda: f"req_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:8]}"
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)
    payload: Dict[str, Any]
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
    """冲突裁决响应"""
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


# ==================== 成员D：优先级排序和复核标记模型 ====================
class AuditResult(BaseModel):
    """审计组输出结果模型"""
    audit_agent: str = Field(..., description="审计组标识")
    result_id: str = Field(..., description="审计结果唯一ID")
    audit_point: str = Field(..., description="论文审核点")
    problem_level: str = Field(..., description="问题等级：Critical/Major/Minor/None")
    confidence: float = Field(..., ge=0.0, le=1.0, description="置信度（0-1）")
    evidence: str = Field(..., description="问题证据")
    impact_scope: str = Field(default="无明确范围", description="影响范围")
    audit_time: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    @validator("problem_level")
    def validate_problem_level(cls, v):
        valid_levels = ["Critical", "Major", "Minor", "None"]
        if v not in valid_levels:
            raise ValueError(f"问题等级必须是{valid_levels}中的一种")
        return v


class ReviewMarkResult(BaseModel):
    """人工复核标记结果"""
    review_mark_id: str = Field(
        default_factory=lambda: f"RM-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
    )
    audit_point: str = Field(...)
    related_agents: List[str] = Field(...)
    mark_type: str = Field(..., description="Conf_Low/Agent_Conflict/Evid_Missing")
    trigger_reason: str = Field(...)
    confidence_scores: Dict[str, float] = Field(...)
    mark_priority: str = Field(...)
    related_result_ids: List[str] = Field(...)
    generate_time: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


class SortedAuditResult(BaseModel):
    """排序后的审计结果"""
    result_id: str = Field(...)
    audit_agent: str = Field(...)
    audit_point: str = Field(...)
    problem_level: str = Field(...)
    sort_score: float = Field(..., description="排序得分")
    mark_status: str = Field(..., description="是否标记复核：是/否")
    priority_rank: int = Field(...)


# ==================== 成员C：导师对话生成模型 ====================
class PrioritizedIssue(BaseModel):
    """优先级问题"""
    description: str
    priority: str
    agents: List[str]
    evidence: Optional[str] = None


class MentorDialogue(BaseModel):
    """导师对话"""
    role: str
    field: str
    conversation: List[Dict[str, str]]
    quality_score: Optional[float] = None


class ReflectionResult(BaseModel):
    """反思评估最终结果"""
    paper_id: str
    final_score: float
    verdict: str
    critical_issues: List[PrioritizedIssue] = []
    major_issues: List[PrioritizedIssue] = []
    minor_issues: List[PrioritizedIssue] = []
    needs_human_review: bool = False
    human_review_reason: Optional[str] = None
    mentor_dialogue: Optional[MentorDialogue] = None
    plugin_metadata: Dict[str, Any] = Field(default_factory=dict)
