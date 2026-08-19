from pydantic import BaseModel, Field


class CodeReviewRequest(BaseModel):

    code: str = Field(..., min_length=1)

    language: str = Field(default="python")

    filename: str = Field(default="unknown")
