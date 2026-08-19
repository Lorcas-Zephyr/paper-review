from pydantic import BaseModel, Field, field_validator, UUID4, ConfigDict
from typing import Optional, List, Dict, Any
from enum import Enum
import re
from config import ALLOWED_TAGS, AGENT_NAME, AGENT_VERSION

class AuditLevel(str, Enum):
    INFO = "Info"
    WARNING = "Warning"
    CRITICAL = "Critical"

class RequestMetadata(BaseModel):
    paper_id: UUID4 = Field(..., description="论文全局唯一标识 (UUID)")
    paper_title: str = Field(..., description="论文标题", min_length=1)
    chunk_id: str = Field(..., description="切片ID", min_length=1)

class RequestPayload(BaseModel):
    content: Optional[str] = Field(None, description="论文切片内容 (如果为空，则根据paper_id从数据库拉取)")
    context_before: Optional[str] = Field(None, description="前一段落摘要")
    context_after: Optional[str] = Field(None, description="后一段落开头")

class RequestConfig(BaseModel):
    temperature: float = Field(0.1, description="模型温度", ge=0.0, le=1.0)
    max_tokens: int = Field(500, description="最大生成Token数", gt=0)
    enable_rules: bool = Field(
        True,
        description="为 False 时关闭 YAML 细则，仅保留布局分析 + 限块 LLM 等基本格式审阅",
    )

class AuditRequest(BaseModel):
    """
    审计请求模型
    对应开发规范：API交互规范 - 论文切片上传协议
    """
    request_id: str = Field(..., description="请求ID")
    metadata: RequestMetadata
    payload: RequestPayload
    config: RequestConfig

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "request_id": "req_20231027_001",
                "metadata": {
                    "paper_id": "123e4567-e89b-12d3-a456-426614174000",
                    "paper_title": "论文标题",
                    "chunk_id": "chunk_seq_005",
                },
                "payload": {
                    "content": "论文切片内容...",
                    "context_before": "前文...",
                    "context_after": "后文...",
                },
                "config": {"temperature": 0.1, "max_tokens": 500, "enable_rules": True},
            }
        }
    )

class AgentInfo(BaseModel):
    name: str = Field(AGENT_NAME, description="Agent名称")
    version: str = Field(AGENT_VERSION, description="Agent版本")

class IssueDetail(BaseModel):
    """
    Detailed issue report, matching the frontend requirement for anchor and highlighting.
    """
    issue_type: str = Field(..., description="问题类型")
    severity: str = Field(..., description="严重程度")
    page_num: int = Field(0, description="页码", ge=0)
    bbox: List[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0], description="边界框 [x0, y0, x1, y1]")
    evidence: Optional[str] = Field(None, description="原文证据/特征字符")
    message: Optional[str] = Field(None, description="错误描述")
    location: Optional[Dict[str, Any]] = Field(None, description="详细定位信息 (page, bbox)")
    anchor_id: Optional[str] = Field(None, description="锚点ID")
    highlight: Optional[List[float]] = Field(None, description="高亮区域")

    @field_validator("page_num", mode="before")
    def normalize_page_num(cls, v):
        if v is None:
            return 0
        if isinstance(v, int):
            return v if v >= 0 else 0
        if isinstance(v, str):
            s = v.strip()
            if re.fullmatch(r"-\d+", s):
                return 0
            if s.isdigit():
                return int(s)
            nums = re.findall(r"-?\d+", s)
            if nums:
                try:
                    n = int(nums[0])
                    return n if n >= 0 else 0
                except Exception:
                    return 0
            return 0
        return 0

    @field_validator("bbox", mode="before")
    def normalize_bbox(cls, v):
        if v is None:
            return [0.0, 0.0, 0.0, 0.0]
        if not isinstance(v, list):
            return [0.0, 0.0, 0.0, 0.0]
        try:
            floats = [float(x) for x in v]
        except Exception:
            return [0.0, 0.0, 0.0, 0.0]
        if len(floats) < 4:
            floats = floats + [0.0] * (4 - len(floats))
        if len(floats) > 4:
            floats = floats[:4]
        return floats

class AuditResult(BaseModel):
    score: int = Field(..., ge=0, le=100, description="评分")
    audit_level: AuditLevel = Field(..., description="风险等级")
    comment: str = Field(..., description="审计评语")
    suggestion: str = Field(..., description="修改建议")
    tags: List[str] = Field(..., description="问题标签")

    @field_validator('tags')
    def validate_tags(cls, v):
        for tag in v:
            if tag not in ALLOWED_TAGS:
                raise ValueError(f"invalid tag: {tag}")
        return v

class ResourceUsage(BaseModel):
    tokens: int = Field(..., description="Token消耗", ge=0)
    latency_ms: int = Field(..., description="耗时(ms)", ge=0)

class AuditResponse(BaseModel):
    """
    审计响应模型
    对应开发规范：API交互规范 - 审计结果返回协议
    """
    request_id: str = Field(..., description="请求ID")
    agent_info: AgentInfo
    result: AuditResult
    usage: ResourceUsage

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "request_id": "req_20231027_001",
                "agent_info": {"name": "Standardization_Auditor_Agent", "version": "v1.1"},
                "result": {
                    "score": 85,
                    "audit_level": "Warning",
                    "comment": "发现 3 个格式问题。",
                    "suggestion": "建议修正图表标号及错别字。",
                    "tags": ["Citation_Inconsistency", "Label_Missing"],
                },
                "usage": {"tokens": 120, "latency_ms": 1500},
            }
        }
    )
