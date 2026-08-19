from sentence_transformers import util
from typing import List, Dict
import torch
import sys
from pathlib import Path
from src.config import settings

APP_ROOT = Path(__file__).resolve().parents[3]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from local_model_config import MODEL_IDS, load_sentence_transformer

class EvidenceValidator:
    def __init__(self):
        self.model = None
        if settings.evidence.enable_semantic:
            self.model = load_sentence_transformer(MODEL_IDS["reflection_embedding"])

    async def validate(self, quote: str, paper_sections: List[Dict]) -> ValidationReport:
        # 精确匹配
        if settings.evidence.exact_match:
            for sec in paper_sections:
                if quote.lower() in sec['content'].lower():
                    return ValidationReport(
                        quote=quote,
                        exists=True,
                        location=sec['section_name'],
                        confidence=1.0
                    )

        # 语义匹配
        if settings.evidence.enable_semantic and self.model:
            # 编码引文和所有段落
            quote_emb = self.model.encode(quote, convert_to_tensor=True)
            section_contents = [sec['content'] for sec in paper_sections]
            section_embs = self.model.encode(section_contents, convert_to_tensor=True)
            cos_scores = util.cos_sim(quote_emb, section_embs)[0]
            max_score, max_idx = torch.max(cos_scores, dim=0)
            if max_score >= settings.evidence.semantic_threshold:
                return ValidationReport(
                    quote=quote,
                    exists=True,
                    location=paper_sections[max_idx]['section_name'],
                    confidence=float(max_score)
                )

        return ValidationReport(quote=quote, exists=False, confidence=0.0)
