from pydantic import BaseModel
from typing import List, Dict

class AgentInfo(BaseModel):
    name: str
    version: str

class Proposition(BaseModel):
    prop_id: str
    content: str
    type: str
    position: str

class SemanticRelation(BaseModel):
    from_prop: str
    to_prop: str
    relation_type: str
    confidence: float

class SemanticGraph(BaseModel):
    nodes: List[Proposition]
    edges: List[SemanticRelation]

class UsageInfo(BaseModel):
    tokens: int
    latency_ms: int

class SemanticModelingResponse(BaseModel):
    request_id: str
    agent_info: AgentInfo
    result: SemanticGraph
    usage: UsageInfo
