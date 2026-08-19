# models.py
# 协议与Mock组 - 统一数据模型定义
from typing import List, Dict, Any, Optional
from enum import Enum
from datetime import datetime
from pydantic import BaseModel, Field
import uuid

# ========== 通用枚举 ==========
class AuditStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"

class AuditLevelGroup6(str, Enum):
    CRITICAL = "Critical"
    WARNING = "Warning"
    INFO = "Info"
    PASS = "Pass"

class AuditLevelFormat(str, Enum):
    CRITICAL = "Critical"
    WARNING = "Warning"
    INFO = "Info"

# ========== 基础组件模型 ==========
class AgentInfo(BaseModel):
    name: str
    version: str

class Usage(BaseModel):
    tokens: int
    latency_ms: int

# ========== 通用协议模型 (用于Mock及文献等Agent) ==========
class ProtocolRequest(BaseModel):
    request_id: str
    metadata: Dict[str, str]
    payload: Dict[str, str]
    config: Dict[str, float]

class ProtocolResponse(BaseModel):
    request_id: str
    agent_info: AgentInfo
    result: Dict[str, Any]
    usage: Usage

# ========== 代码审计组模型 ==========
class CodeReviewMetadata(BaseModel):
    paper_id: str
    paper_title: str
    chunk_id: str

class CodeReviewPayload(BaseModel):
    content: str
    context_before: str = ""
    context_after: str = ""

class CodeReviewConfig(BaseModel):
    temperature: float = 0.1
    max_tokens: int = 500

class CodeReviewRequest(BaseModel):
    request_id: str
    metadata: CodeReviewMetadata
    payload: CodeReviewPayload
    config: CodeReviewConfig

class CodeReviewResult(BaseModel):
    score: int
    audit_level: str
    comment: str
    suggestion: str
    tags: List[str]
    location: str

class CodeReviewResponse(BaseModel):
    request_id: str
    agent_info: AgentInfo
    result: CodeReviewResult
    usage: Usage

# ========== 实验数据审计组（第六组）模型 ==========
class Group6AuditRequest(BaseModel):
    request_id: str
    paper_id: str
    model_preference: str = "deepseek-v4-flash"
    audit_scope: List[str] = ["experiment", "result"]

class AuditResultItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    point: str
    score: int
    level: AuditLevelGroup6
    description: str
    evidence_quote: str
    location: Dict[str, Any] = {}
    suggestion: str

class GenericAuditResult(BaseModel):
    """通用审计结果结构，用于统一代码审计和实验数据审计的返回格式"""
    score: int
    audit_level: str
    comment: str
    suggestion: str
    tags: List[str]
    details: Optional[Dict[str, Any]] = Field(default_factory=dict, description="审计详情，如实验审计的 point, evidence_quote 等")

class Group6AuditResponse(BaseModel):
    request_id: str
    agent_info: AgentInfo
    result: GenericAuditResult
    usage: Usage

# ========== 格式审计组模型 ==========
class FormatIssueItem(BaseModel):
    issue_type: str
    severity: str
    page_num: Optional[int] = None
    bbox: Optional[List[float]] = None
    evidence: str
    message: str
    location: Dict[str, Any] = {}
    anchor_id: Optional[str] = None
    highlight: Optional[List[float]] = None
    suggestion: Optional[str] = None

class FormatResult(BaseModel):
    score: int
    audit_level: AuditLevelFormat
    comment: str
    suggestion: str
    tags: List[str]
    issues: List[FormatIssueItem]

class FormatAuditResponse(BaseModel):
    request_id: str
    agent_info: AgentInfo
    result: FormatResult
    usage: Usage

# ========== 逻辑审计组模型 ==========
class LogicAuditDetailItem(BaseModel):
    chunk_id: str
    evidence_quote: str
    issue_type: str
    comment: str
    suggestion: str

class LogicAuditResult(BaseModel):
    score: int
    audit_level: str
    comment: str
    suggestion: str
    tags: List[str]
    details: List[LogicAuditDetailItem] = []

class LogicAuditResponse(BaseModel):
    request_id: str
    agent_info: AgentInfo
    result: LogicAuditResult
    usage: Usage

class PaperAuditNode(BaseModel):
    id: str
    content: str
    type: str
    position: str
    section: str

class PaperAuditEdge(BaseModel):
    source: str
    target: str
    relation: str
    confidence: float

class PaperAuditRequest(BaseModel):
    paper_id: str

# ========== 文献审计组模型 ==========
class CitationAuditResult(BaseModel):
    score: int
    audit_level: str
    comment: str
    suggestion: Optional[str] = None
    tags: List[str]

class CitationAuditResponse(BaseModel):
    request_id: str
    agent_info: AgentInfo
    result: CitationAuditResult
    usage: Dict[str, Any]  # 文献组可能使用字典格式
