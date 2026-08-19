from pydantic import BaseModel, Field
from typing import List


class Issue(BaseModel):
    line: int = Field(..., description="line number")
    severity: str = Field(..., description="low medium high critical")
    category: str = Field(..., description="security performance quality")
    message: str
    suggestion: str


class ReviewResult(BaseModel):
    summary: str
    issues: List[Issue]
    score: int = Field(..., ge=0, le=100)


class ReviewRequest(BaseModel):
    filename: str
    code: str
