from pydantic import BaseModel
from typing import List, Dict, Optional

class Payload(BaseModel):
    content: str
    context_before: Optional[str] = ""
    context_after: Optional[str] = ""
    abstract: Optional[str] = ""

class AuditRequest(BaseModel):
    request_id: str
    metadata: Dict[str, str]
    payload: Payload
    config: Optional[Dict] = {}

class Detail(BaseModel):
    chunk_id: str
    evidence_quote: str
    issue_type: str
    comment: str
    suggestion: str

class Result(BaseModel):
    score: int
    audit_level: str
    comment: str
    suggestion: str
    tags: List[str]
    details: List[Detail]

class AgentInfo(BaseModel):
    name: str
    version: str

class Usage(BaseModel):
    tokens: int
    latency_ms: int

class AuditResponse(BaseModel):
    request_id: str
    agent_info: AgentInfo
    result: Result
    usage: Usage
