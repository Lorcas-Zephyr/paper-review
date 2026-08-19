"""成员B模块：证据验证与重复过滤。"""

from .config import DedupConfig, EvidenceConfig
from .dedup import Deduplicator
from .evidence import EvidenceValidator
from .models import (
    DedupItem,
    DedupRequest,
    DedupResult,
    EvidenceItem,
    EvidenceValidationRequest,
    EvidenceValidationResult,
    Section,
)

__all__ = [
    "DedupConfig",
    "EvidenceConfig",
    "Deduplicator",
    "EvidenceValidator",
    "DedupItem",
    "DedupRequest",
    "DedupResult",
    "EvidenceItem",
    "EvidenceValidationRequest",
    "EvidenceValidationResult",
    "Section",
]
