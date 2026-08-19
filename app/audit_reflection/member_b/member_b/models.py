from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Section:
    """论文切片结构：section -> paragraphs。"""
    section_id: str
    title: str
    paragraphs: List[str]


@dataclass
class EvidenceItem:
    """上游审计条目。"""
    item_id: str
    agent: str
    claim_text: str
    quote: Optional[str] = None
    evidence_hint: Optional[str] = None
    upstream_confidence: Optional[float] = None


@dataclass
class EvidenceValidationRequest:
    """证据验证请求。"""
    paper_id: str
    paper_field: Optional[str]
    sections: List[Section]
    items: List[EvidenceItem]
    config: Dict[str, object] = field(default_factory=dict)


@dataclass
class EvidenceSpan:
    """证据定位信息。"""
    section_id: str
    paragraph_index: int
    char_start: int
    char_end: int


@dataclass
class EvidenceValidationResult:
    """证据验证结果。"""
    item_id: str
    exists: bool
    method: str
    confidence: float
    evidence_spans: List[EvidenceSpan]
    matched_text: str
    notes: str


@dataclass
class DedupItem:
    """去重输入条目。"""
    item_id: str
    agent: str
    text: str


@dataclass
class DedupRequest:
    """去重请求。"""
    paper_id: str
    items: List[DedupItem]
    config: Dict[str, object] = field(default_factory=dict)


@dataclass
class DedupCluster:
    """聚类结果。"""
    cluster_id: int
    representative_item: DedupItem
    members: List[DedupItem]
    cluster_stats: Dict[str, object]


@dataclass
class DedupResult:
    """去重结果。"""
    paper_id: str
    clusters: List[DedupCluster]
    noise: List[DedupItem]
