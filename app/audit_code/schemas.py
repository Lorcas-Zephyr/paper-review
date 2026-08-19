from pydantic import BaseModel, Field
from typing import List,Optional

# ==========================================
# 1. 论文切片上传协议 (Orchestrator -> Agent)
# ==========================================
class Metadata(BaseModel):
    paper_id: str = Field(..., description="论文唯一标识")
    paper_title: str = Field(..., description="论文标题")
    chunk_id: str = Field(..., description="切片 ID")

class Payload(BaseModel):
    content: Optional[str] = Field(None, description="当前需要审计的 Markdown 核心文本片段")
    context_before: Optional[str] = Field(None, description="前一段内容")
    context_after: Optional[str] = Field(None, description="后一段内容")

class AgentConfig(BaseModel):
    temperature: float = Field(0.1, description="模型温度")
    max_tokens: int = Field(500, description="最大生成 Token 数")

class ReviewRequest(BaseModel):
    request_id: str = Field(..., description="请求追踪 ID")
    metadata: Metadata
    payload: Payload
    config: dict = Field(default_factory=dict)

# ==========================================
# 2. Agent 审计结果返回协议 (Agent -> Orchestrator)
# ==========================================

class AgentInfo(BaseModel):
    name: str = Field(default="Code_Review_Agent", description="负责的 Agent 名称")
    version: str = Field(default="v1.0", description="模型/逻辑版本")

class AuditResult(BaseModel):
    score: int = Field(description="该切片的质量评分 (0-100)")
    audit_level: str = Field(description="风险等级：Info, Warning, Critical")
    comment: str = Field(description="专家视角的评语内容")
    suggestion: str = Field(description="具体的修改建议")
    tags: List[str] = Field(description="问题标签，如['代码规范', '内存泄漏']")
    location: str = Field(description="用于前端点击证据、原文跳转的高亮定位")

class Usage(BaseModel):
    tokens: int = Field(description="消耗 Token 数")
    latency_ms: int = Field(description="执行耗时")

class ReviewResponse(BaseModel):
    request_id: str = Field(..., description="必须与请求中的 request_id 一致")
    agent_info: AgentInfo = Field(default_factory = AgentInfo, description="处理该请求的 Agent 信息")
    result: AuditResult
    usage: Usage
