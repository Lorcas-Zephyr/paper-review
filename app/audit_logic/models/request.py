from pydantic import BaseModel
from typing import Optional

class PaperMetadata(BaseModel):
    paper_id: str
    paper_title: str
    chunk_id: str

class PaperPayload(BaseModel):
    content: str
    context_before: str
    context_after: str
    abstract: Optional[str] = None

class SemanticModelingRequest(BaseModel):
    request_id: str
    metadata: PaperMetadata
    payload: PaperPayload
    config: Optional[dict] = None
