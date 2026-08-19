from dataclasses import dataclass


@dataclass
class EvidenceConfig:
    """证据验证配置。"""
    top_k: int = 5
    semantic_threshold: float = 0.86
    enable_numeric_check: bool = True
    enable_term_check: bool = True
    edit_distance_threshold: float = 0.02
    semantic_confidence_cap: float = 0.90


@dataclass
class DedupConfig:
    """去重聚类配置。"""
    eps: float = 0.16
    min_samples: int = 2
