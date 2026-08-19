from typing import Optional
from pydantic import BaseModel


class ParseError(BaseModel):
    error_type: str
    message: str
    page_num: Optional[int] = None


class ParseReport(BaseModel):
    encrypted: bool = False
    scanned_pages: int = 0
    multi_column_pages: int = 0
    visual_elements_count: int = 0
