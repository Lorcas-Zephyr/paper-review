from typing import List, Optional, Tuple

from .config import EvidenceConfig
from .models import EvidenceItem, EvidenceSpan, EvidenceValidationResult, Section
from .utils import (
    argmax_topk,
    extract_numbers,
    extract_terms,
    find_substring_span,
    normalize_text,
    similarity_ratio,
)


class EvidenceValidator:
    def __init__(self, config: Optional[EvidenceConfig] = None):
        self.config = config or EvidenceConfig()
        self._embedder = None

    def _ensure_embedder(self):
        if self._embedder is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
            return
        except Exception:
            self._embedder = None

    def _embed(self, texts: List[str]) -> List[List[float]]:
        # 向量化：优先 Sentence-Transformers，失败则降级为 TF-IDF 或哈希。
        self._ensure_embedder()
        if self._embedder is not None:
            vectors = self._embedder.encode(texts, normalize_embeddings=True)
            return [v.tolist() for v in vectors]
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore

            vectorizer = TfidfVectorizer(max_features=2048)
            mat = vectorizer.fit_transform(texts)
            return mat.toarray().tolist()
        except Exception:
            # 简单哈希回退，保证流程可跑通
            vectors = []
            for text in texts:
                vec = [0.0] * 256
                for token in normalize_text(text).split():
                    idx = hash(token) % 256
                    vec[idx] += 1.0
                vectors.append(vec)
            return vectors

    def validate_item(self, item: EvidenceItem, sections: List[Section]) -> EvidenceValidationResult:
        quote = item.quote or ""
        claim = item.claim_text or ""
        target = quote if quote.strip() else claim
        normalized_target = normalize_text(target)

        exact = self._exact_match(target, normalized_target, sections)
        if exact is not None:
            exact.item_id = item.item_id
            return exact

        semantic = self._semantic_match(target, normalized_target, sections)
        if semantic is not None:
            semantic.item_id = item.item_id
            return semantic

        return EvidenceValidationResult(
            item_id=item.item_id,
            exists=False,
            method="none",
            confidence=0.0,
            evidence_spans=[],
            matched_text="",
            notes="未找到证据",
        )

    def _exact_match(
        self,
        target: str,
        normalized_target: str,
        sections: List[Section],
    ) -> Optional[EvidenceValidationResult]:
        for s in sections:
            for i, para in enumerate(s.paragraphs):
                normalized_para = normalize_text(para)
                if normalized_target and normalized_target in normalized_para:
                    start, end = find_substring_span(para, target)
                    return EvidenceValidationResult(
                        item_id="",
                        exists=True,
                        method="exact",
                        confidence=0.97,
                        evidence_spans=[EvidenceSpan(s.section_id, i, start, end)],
                        matched_text=para[start:end] if end > start else para[:200],
                        notes="精确匹配（规范化后包含）",
                    )

                if normalized_target:
                    ratio = similarity_ratio(normalized_target, normalized_para)
                    if 1.0 - ratio <= self.config.edit_distance_threshold:
                        start, end = (0, min(len(para), len(target)))
                        return EvidenceValidationResult(
                            item_id="",
                            exists=True,
                            method="exact",
                            confidence=0.92,
                            evidence_spans=[EvidenceSpan(s.section_id, i, start, end)],
                            matched_text=para[:200],
                            notes="模糊匹配（编辑距离阈值）",
                        )

        return None

    def _semantic_match(
        self,
        target: str,
        normalized_target: str,
        sections: List[Section],
    ) -> Optional[EvidenceValidationResult]:
        paragraphs: List[Tuple[str, str, int]] = []
        for s in sections:
            for i, para in enumerate(s.paragraphs):
                paragraphs.append((s.section_id, para, i))

        if not paragraphs or not normalized_target:
            return None

        texts = [p for _, p, _ in paragraphs]
        vectors = self._embed(texts + [target])
        para_vecs = vectors[:-1]
        target_vec = vectors[-1]

        from .utils import cosine_sim

        sims = [cosine_sim(target_vec, v) for v in para_vecs]
        top_indices = argmax_topk(sims, self.config.top_k)

        best_idx = top_indices[0]
        best_sim = sims[best_idx]
        if best_sim < self.config.semantic_threshold:
            return None

        section_id, para, para_index = paragraphs[best_idx]
        rule_factor, notes = self._rule_factor(target, para)
        confidence = min(best_sim * rule_factor, self.config.semantic_confidence_cap)

        start, end = find_substring_span(para, target)
        return EvidenceValidationResult(
            item_id="",
            exists=True,
            method="semantic",
            confidence=confidence,
            evidence_spans=[EvidenceSpan(section_id, para_index, start, end)],
            matched_text=para[:300],
            notes=notes,
        )

    def _rule_factor(self, target: str, paragraph: str) -> Tuple[float, str]:
        factor = 1.0
        notes = []
        if self.config.enable_numeric_check:
            target_nums = extract_numbers(target)
            para_nums = extract_numbers(paragraph)
            if target_nums and not set(target_nums).intersection(para_nums):
                factor *= 0.6
                notes.append("数字不一致")

        if self.config.enable_term_check:
            target_terms = extract_terms(target)
            para_terms = extract_terms(paragraph)
            if target_terms and not set(target_terms).intersection(para_terms):
                factor *= 0.7
                notes.append("关键术语不一致")

        if not notes:
            notes.append("语义命中")

        return factor, "; ".join(notes)
