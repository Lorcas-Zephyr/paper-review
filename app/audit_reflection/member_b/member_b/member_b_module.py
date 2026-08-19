from typing import Dict, List

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
)


def run_evidence_validation(payload: EvidenceValidationRequest) -> List[EvidenceValidationResult]:
    config = EvidenceConfig(**payload.config) if payload.config else EvidenceConfig()
    validator = EvidenceValidator(config=config)
    results: List[EvidenceValidationResult] = []

    for item in payload.items:
        result = validator.validate_item(item, payload.sections)
        # 补齐 item_id，便于下游追溯
        result.item_id = item.item_id
        results.append(result)

    return results


def run_dedup(payload: DedupRequest) -> DedupResult:
    config = DedupConfig(**payload.config) if payload.config else DedupConfig()
    deduper = Deduplicator(config=config)
    return deduper.dedup(payload.paper_id, payload.items)
