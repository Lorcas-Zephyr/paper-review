from pydantic import BaseModel
from typing import List


class Issue(BaseModel):

    line: int
    severity: str
    message: str
    suggestion: str


class CodeReviewResponse(BaseModel):

    summary: str
    issues: List[Issue]
    score: int
