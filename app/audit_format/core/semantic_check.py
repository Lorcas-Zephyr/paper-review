from typing import List, Dict, Any, Optional, Tuple
import asyncio
import base64
import math
import re
import json
import sys
from pathlib import Path
from config import LLM_TIMEOUT_SEC
from .layout_zones import is_reference_title
from .llm_client import LLMClient
from sqlalchemy import select, func, or_
from .database import db_manager, ExpertComment
from utils.logger import setup_logger

logger = setup_logger(__name__)

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from local_model_config import load_sentence_transformer

_sbert_model = None


def _get_sbert_model():
    global _sbert_model
    if _sbert_model is None:
        try:
            from config import SBERT_MODEL_NAME, SBERT_DEVICE

            device = SBERT_DEVICE or None
            _sbert_model = load_sentence_transformer(SBERT_MODEL_NAME, device=device)
        except Exception as e:
            logger.warning(f"SBERT model unavailable, will use fallback embedding: {e}")
            _sbert_model = None
    return _sbert_model


def _embed_text_fallback(text: str) -> List[float]:
    import hashlib
    import numpy as np

    vec = np.zeros(768, dtype=np.float32)
    tokens = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9_]+", (text or "").lower())
    if not tokens:
        return vec.astype(float).tolist()

    for token in tokens:
        h = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "little", signed=False) % 768
        sign = 1.0 if (h[4] & 1) == 0 else -1.0
        vec[idx] += sign

    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec /= norm
    return vec.astype(float).tolist()


def _embed_text_sbert(text: str) -> List[float]:
    model = _get_sbert_model()
    if model is None:
        return _embed_text_fallback(text)

    vec = model.encode(text, normalize_embeddings=True)
    vec_list = vec.tolist() if hasattr(vec, "tolist") else list(vec)
    if len(vec_list) != 768:
        logger.warning(f"SBERT embedding dim mismatch: expected 768, got {len(vec_list)}. Using fallback embedding.")
        return _embed_text_fallback(text)
    return vec_list


def _embed_text_expert_comment(text: str) -> List[float]:
    return _embed_text_sbert(text)


def _element_get(element: Any, key: str) -> Any:
    if isinstance(element, dict):
        return element.get(key)
    return getattr(element, key, None)


def _is_likely_reference(element: Any) -> bool:
    """
    Check if an element is likely a reference item, either by region tag or heuristic content analysis.
    """
    region = _element_get(element, "region")
    if region == "reference":
        return True

    content = str(_element_get(element, "content") or "")
    if not content:
        return False

    # Heuristic: Starts with [N] or N. and contains reference keywords
    # Exclude typical section headers like "1. Introduction" (usually short, no vol/pp)

    # Special case: Isolated reference markers (e.g. "[14]") often found when layout analysis splits marker from text
    if re.match(r"^\s*\[\d+\]\s*$", content):
        return True

    if re.match(r"^\s*(\[\d+\]|\d+\.)", content):
        if len(content) > 20:
            # Common reference keywords (English & Chinese)
            # vol, pp, no, doi, http, isbn, issn, journal, conference, proceedings, trans, rev, arxiv
            # 卷, 期, 页, 学报, 会议, 论文集, 出版社
            # Standard GB/T 7714 type indicators: [J], [C], [D], [M], [EB/OL], etc.
            # Common abbreviations: et al., eds.
            # Added "Reference", "References" for title lines if they slip in
            pattern = r"(vol\.|pp\.|no\.|doi|http|isbn|issn|journal|conference|proceedings|trans\.|rev\.|arxiv|reference|references|学报|会议|论文集|出版社|pages|et al\.?|eds\.?|\[[A-Z]{1,2}(/[A-Z]+)?\])"
            if re.search(pattern, content, re.IGNORECASE):
                return True

            # Fallback: If no keywords, but looks like a standard reference (starts with [N] + space, long enough)
            # This catches references with just authors/titles and no "vol/pp" in the first block.
            # Require space after number to avoid [14]、 (enumeration without space)
            if len(content) > 40 and re.match(r"^\s*(\[\d+\]|\d+\.)\s", content):
                return True
    return False


def _extract_text_from_layout(layout_data: Dict[str, Any]) -> str:
    elements = layout_data.get("elements", []) if isinstance(layout_data, dict) else []
    texts = []
    for e in elements:
        text = _element_get(e, "content")
        if text:
            texts.append(str(text))
    return "\n".join(texts)


def _is_pdf_base64(content: str) -> bool:
    try:
        decoded = base64.b64decode(content, validate=True)
    except Exception:
        return False
    return b"%PDF" in decoded[:1024]


def _resolve_text_content(content: Any, layout_data: Dict[str, Any]) -> str:
    if isinstance(content, str):
        if _is_pdf_base64(content):
            layout_text = _extract_text_from_layout(layout_data)
            return layout_text
        return content
    return _extract_text_from_layout(layout_data)


def _term_found(content: str, term: str) -> bool:
    if not term:
        return False
    if re.search(r"[A-Za-z]", term):
        pattern = r"\b" + re.escape(term) + r"\b"
        return re.search(pattern, content, flags=re.IGNORECASE) is not None
    return term in content


def _normalize_term_key(term: str) -> str:
    if re.search(r"[A-Za-z]", term):
        return term.lower()
    return term


def _extract_reference_numbers(reference_texts: List[str]) -> List[str]:
    nums = []
    for text in reference_texts:
        # Handle multi-line elements (references might be merged in one block)
        for line in text.split('\n'):
            if not line.strip():
                continue
            m = re.match(r"^\s*\[(\d+)\]", line)
            if m:
                nums.append(m.group(1))
                continue
            m = re.match(r"^\s*(\d+)[.)]", line)
            if m:
                nums.append(m.group(1))
    return nums


def _extract_numeric_citations(content: str) -> List[str]:
    results: List[str] = []
    for m in re.finditer(r"(?<![\w\]])\[(\d+(?:\s*,\s*\d+)*)\]", content or ""):
        nums = [n for n in re.findall(r"\d+", m.group(1) or "") if n and n != "0"]
        if not nums:
            continue
        try:
            int_nums = [int(n) for n in nums]
        except Exception:
            continue
        if any(n >= 1000 for n in int_nums):
            continue
        results.append(m.group(1))
    return results


def _extract_author_year_citations(content: str) -> List[str]:
    results = []
    pattern = re.compile(
        r"\(([^()]*\b[A-Za-z][A-Za-z'\-]+(?:\s+et\s+al\.)?[^()]*?,\s*\d{4}[a-z]?[^()]*)\)"
    )
    for m in pattern.finditer(content or ""):
        value = (m.group(1) or "").strip()
        if value:
            results.append(value)
    return results


def _parse_author_year(citation: str) -> Optional[Dict[str, str]]:
    year_match = re.search(r"\d{4}[a-z]?", citation)
    author_match = re.search(r"\b([A-Za-z][A-Za-z'\-]+)\b", citation)
    if not year_match or not author_match:
        return None
    return {"author": author_match.group(1).lower(), "year": year_match.group(0)}


class TextPageMapper:
    """
    Helper class to map text indices back to page numbers.
    """
    def __init__(self, layout_data: Dict[str, Any]):
        self.elements = layout_data.get("elements", []) if isinstance(layout_data, dict) else []
        self.mapping = []  # List of (start_idx, end_idx, page_num)
        self.full_text = ""
        self._build()

    def _build(self):
        parts = []
        current_pos = 0
        for e in self.elements:
            text = _element_get(e, "content")
            pg = _element_get(e, "page_num")
            if text:
                text_str = str(text)
                length = len(text_str)
                # Map this segment to page
                self.mapping.append((current_pos, current_pos + length, pg))
                parts.append(text_str)
                current_pos += length + 1  # +1 for newline
        self.full_text = "\n".join(parts)

    def get_page_num(self, index: int) -> str:
        # Binary search could be better but linear is fine for now
        for start, end, pg in self.mapping:
            if start <= index < end:
                return str(pg) if pg is not None else "?"
        return "?"

    def get_page_range(self, start_idx: int, end_idx: int) -> str:
        pages = set()
        for start, end, pg in self.mapping:
            # Check overlap
            if max(start, start_idx) < min(end, end_idx):
                if pg is not None:
                    pages.add(str(pg))

        if not pages:
            return "?"

        # Try to sort numerically
        try:
            sorted_pages = sorted(list(pages), key=lambda x: int(x) if str(x).isdigit() else 9999)
        except:
            sorted_pages = sorted(list(pages))

        if not sorted_pages:
            return "?"

        if len(sorted_pages) == 1:
            return str(sorted_pages[0])
        else:
            return f"{sorted_pages[0]}-{sorted_pages[-1]}"


import difflib

class TypoChecker:
    """
    语义判定 - 错别字红线判定
    对应《分工明细》二、语义判定 - 阶段2
    """
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.max_typos_total = config.get("max_typos_total_warning", 10)
        self.critical_keywords = config.get("critical_keywords", [])

    def check(self, content: str, issues: List[Dict], mapper: Optional['TextPageMapper'] = None):
        """
        1. 集成中文分词/错别字检测工具
        2. 上下文语义纠错 (排除专业名词歧义)
        3. 红线触发逻辑: 全文>10个 -> Warning; 关键术语错字 -> Critical
        """
        if not content:
            return

        typo_count = 0
        found_typos = []

        # 1. Critical Keywords Check (Fuzzy Matching)
        # Find all word occurrences with their positions
        word_matches = list(re.finditer(r"\b\w+\b", content))
        words = [m.group() for m in word_matches]

        # 对每个关键术语，寻找文本中相似但不完全相同的词
        for keyword in self.critical_keywords:
            # 忽略大小写比较，如果 text 中有 deep learning 而 keyword 是 Deep Learning
            # 这其实由 TerminologyChecker 处理。
            # TypoChecker 处理的是拼写错误，如 "TensorFlow" 写成 "TensorFlwo"

            is_english = bool(re.search(r"[A-Za-z]", keyword))
            if is_english:
                candidates = [w for w in words if abs(len(w) - len(keyword)) <= 2]
            else:
                candidates = [w for w in words if len(w) == len(keyword)]
            # 提高匹配阈值，避免将 "神经网络" 误判为 "卷积神经网络" 的错别字
            matches = difflib.get_close_matches(keyword, candidates, n=3, cutoff=0.85)

            for match in matches:
                if match != keyword:
                    if not is_english:
                        if keyword.startswith(match) or keyword.endswith(match) or match in keyword or keyword in match:
                            continue
                    # 排除掉仅仅是大小写不同的情况 (交给 TerminologyChecker)
                    if match.lower() == keyword.lower():
                        continue

                    # 排除常见复数形式 (简单的 heuristic)
                    if match == keyword + "s" or match == keyword + "es":
                        continue

                    # Find specific occurrences
                    instances = [m for m in word_matches if m.group() == match]
                    for instance in instances:
                        pg = "?"
                        if mapper:
                            pg = mapper.get_page_num(instance.start())

                        issues.append({
                            "issue_type": "Critical_Keyword_Typo",
                            "severity": "Critical",
                            "evidence": match,
                            "page_num": pg,
                            "message": f"关键术语拼写错误: '{match}' 应为 '{keyword}'"
                        })
                        typo_count += 1
                        found_typos.append(match)

        # 2. 通用错别字检测 (模拟/占位)
        # 在没有 NLP 库的情况下，暂时只统计上述发现的 Critical Typos
        # 如果未来集成了 pycorrector，这里可以扩展

        # 3. 阈值检查
        if typo_count > self.max_typos_total:
            issues.append({
                "issue_type": "Typo_Limit_Exceeded",
                "severity": "Warning",
                "evidence": f"Found {typo_count} typos (including: {', '.join(found_typos[:3])}...)",
                "message": f"全文错别字数量超过红线 ({self.max_typos_total}个)"
            })

class TerminologyChecker:
    """
    语义判定 - 术语一致性校验
    对应《分工明细》二、语义判定 - 阶段3
    """
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.terms = config.get("terms", {})
        self.forbidden_variants = config.get("forbidden_variants", {})
        self.warn_on_mixed_allowed_forms = bool(config.get("warn_on_mixed_allowed_forms", False))

    def _norm_en(self, s: str) -> str:
        t = (s or "").strip().lower()
        t = re.sub(r"[_\\-]+", " ", t)
        t = re.sub(r"\\s+", " ", t)
        return t

    def check(self, content: str, issues: List[Dict], mapper: Optional['TextPageMapper'] = None):
        """
        1. 提取专有名词，建立临时术语库
        2. 检测写法不一致 (如 'Deep Learning' vs 'deep-learning')
        3. 生成统一建议
        """
        if not content:
            return

        if self.warn_on_mixed_allowed_forms:
            for canonical, variants in self.terms.items():
                forms = [canonical] + list(variants or [])
                used_norm = set()
                found_pages = set()
                for form in forms:
                    if not form:
                        continue
                    if re.search(r"[A-Za-z]", form):
                        patt = r"\b" + re.escape(form).replace(r"\ ", r"\s+") + r"\b"
                        for m in re.finditer(patt, content, flags=re.IGNORECASE):
                            used_norm.add(self._norm_en(m.group()))
                            if mapper:
                                found_pages.add(mapper.get_page_num(m.start()))
                    else:
                        for m in re.finditer(re.escape(form), content):
                            used_norm.add(form)
                            if mapper:
                                found_pages.add(mapper.get_page_num(m.start()))
                if len(used_norm) > 1:
                    pg_str = "?"
                    if found_pages:
                        sorted_pgs = sorted(list(found_pages), key=lambda x: int(x) if x.isdigit() else 999)
                        pg_str = ", ".join(sorted_pgs)
                    issues.append(
                        {
                            "issue_type": "Terminology_Inconsistent",
                            "severity": "Info",
                            "evidence": ", ".join(sorted(list(used_norm))[:6]),
                            "page_num": pg_str,
                            "message": f"同一术语存在多种允许写法，可考虑统一为：{canonical}",
                        }
                    )

        # Check forbidden variants
        for canonical, forbidden in self.forbidden_variants.items():
            found_forbidden = []
            found_pages = set()
            for form in forbidden or []:
                if re.search(r"[A-Za-z]", form):
                    pattern_str = r"\b" + re.escape(form) + r"\b"
                    matches = list(re.finditer(pattern_str, content, flags=re.IGNORECASE))
                else:
                    pattern_str = re.escape(form)
                    matches = list(re.finditer(pattern_str, content))

                if matches:
                    found_forbidden.append(form)
                    for m in matches:
                        if mapper:
                            found_pages.add(mapper.get_page_num(m.start()))

            if found_forbidden:
                pg_str = "?"
                if found_pages:
                    sorted_pgs = sorted(list(found_pages), key=lambda x: int(x) if x.isdigit() else 999)
                    pg_str = ", ".join(sorted_pgs)

                issues.append(
                    {
                        "issue_type": "Terminology_Forbidden",
                        "severity": "Warning",
                        "evidence": ", ".join(found_forbidden),
                        "page_num": pg_str,
                        "message": f"检测到不规范术语写法，建议使用：{canonical}",
                    }
                )


class PunctuationChecker:
    """
    语义判定 - 标点符号校验
    对应《分工明细》二、语义判定 - 阶段3
    """
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.allow_mixed = config.get("allow_mixed_punctuation", False)
        self.check_position = config.get("check_citation_position", True)
        self.allow_proof_dot = bool(config.get("allow_proof_dot", True))
        self.allow_keywords_colon = bool(config.get("allow_keywords_colon", True))
        self.skip_math_like = bool(config.get("skip_math_like", True))

    def _is_math_like(self, s: str) -> bool:
        t = (s or "").strip()
        if not t:
            return False
        if re.search(r"[∧∨¬⇒⇔∑∫√∂∇∞≈≠≤≥±×÷]", t):
            return True
        if re.search(r"\b[A-Za-z]\b", t) and re.search(r"(可满足|不满足|满足|等价|推出|蕴含|合取|析取|否定)", t):
            return True
        if len(re.findall(r"[=<>≤≥±×÷*/+\\-≈≠]", t)) >= 2:
            return True
        if re.search(r"[A-Za-zα-ωΑ-Ω]\\w{0,3}\\s*(?:≤|≥|<|>)", t):
            return True
        return False

    def check(self, content: str, layout_data: Dict[str, Any], issues: List[Dict], mapper: Optional['TextPageMapper'] = None):
        """
        1. 杜绝中英文标点混用
        2. 标点位置错误 (如引用标注在标点外)
        """
        if not content:
            return

        # Use elements directly to get page numbers
        elements = layout_data.get("elements", []) if isinstance(layout_data, dict) else []

        # 1. Mixed Punctuation Check
        if not self.allow_mixed:
            # 1a. General punctuation (excluding .)
            cn_en_punct_pattern = re.compile(r"[\u4e00-\u9fff]\s*[,?!;:]")
            # 1b. Check for '.' specifically, avoiding TOC leaders
            cn_en_dot_pattern = re.compile(r"[\u4e00-\u9fff]\s*\.(?!\s*[\.\d])")
            # 1c. English text using Chinese punctuation
            en_cn_punct_pattern = re.compile(r"[a-zA-Z]{2,}\s*[，。？！；：]")

            def _context_snippet(s: str, start: int, end: int, window: int = 18) -> str:
                compact = re.sub(r"\s+", " ", s or "").strip()
                if not compact:
                    return ""
                start = max(0, min(int(start), len(compact)))
                end = max(0, min(int(end), len(compact)))
                left = max(0, start - window)
                right = min(len(compact), end + window)
                prefix = "…" if left > 0 else ""
                suffix = "…" if right < len(compact) else ""
                return prefix + compact[left:right] + suffix

            for e in elements:
                if _is_likely_reference(e):
                    continue
                text_val = _element_get(e, "content")
                pg = _element_get(e, "page_num") or "?"
                region = _element_get(e, "region") or ""

                if text_val:
                    segment = str(text_val)
                    if region in {"chart", "formula"}:
                        continue
                    if re.search(r"http[s]?://|@[A-Za-z0-9_.-]+", segment):
                        continue
                    seg_strip = segment.strip()
                    if self.skip_math_like and self._is_math_like(seg_strip):
                        continue
                    if self.allow_keywords_colon and re.match(r"^(key\\s*words|keywords|abstract)\\s*：", seg_strip, flags=re.IGNORECASE):
                        continue
                    if self.allow_proof_dot and re.match(r"^(证明|定理|引理|推论|命题|定义|注|例|已知|求|解|假设)\s*[.:]", seg_strip):
                        continue
                    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", segment))
                    en_count = len(re.findall(r"[A-Za-z]", segment))
                    num_count = len(re.findall(r"\d", segment))
                    total_alpha = cjk_count + en_count + num_count
                    cjk_ratio = cjk_count / total_alpha if total_alpha > 0 else 0.0
                    en_ratio = en_count / total_alpha if total_alpha > 0 else 0.0
                    for m in cn_en_punct_pattern.finditer(segment):
                        if cjk_ratio < 0.5:
                            continue
                        if self.allow_proof_dot and segment[max(0, m.start()-6):m.end()].strip().endswith(("证明.", "定理.", "引理.", "推论.", "命题.", "定义.", "注.", "例.", "已知:", "求:", "解:", "假设:")):
                            continue
                        # Exclude English punctuation if it's right after English words or numbers
                        if m.start() > 0 and re.match(r"[A-Za-z0-9]", segment[m.start()-1]):
                            # Like "GSM8K," or "T=0.2,"
                            continue
                        punct = segment[m.end() - 1] if m.end() > 0 else ""
                        if punct == "," and re.match(r"^\s*[A-Za-z][A-Za-z0-9_]*\s*=\s*[-+]?\d", segment[m.end():] or ""):
                            continue
                        issues.append({
                            "issue_type": "Punctuation_Mixed",
                            "severity": "Info",
                            "evidence": _context_snippet(segment, m.start(), m.end()),
                            "page_num": pg,
                            "message": "中文文本使用了英文标点",
                        })

                    for m in cn_en_dot_pattern.finditer(segment):
                        if cjk_ratio < 0.5:
                            continue
                        if self.allow_proof_dot and segment[max(0, m.start()-6):m.end()].strip().endswith(("证明.", "定理.", "引理.", "推论.", "命题.", "定义.", "注.", "例.", "已知:", "求:", "解:", "假设:")):
                            continue
                        issues.append({
                            "issue_type": "Punctuation_Mixed",
                            "severity": "Info",
                            "evidence": _context_snippet(segment, m.start(), m.end()),
                            "page_num": pg,
                            "message": "中文文本使用了英文标点(.)",
                        })

                    for m in en_cn_punct_pattern.finditer(segment):
                        if en_ratio < 0.8 or en_count < 5:
                            continue

                        # Exclude Chinese punctuation if it's right after Chinese characters
                        # e.g., in a mixed sentence where English is dominant but there's a Chinese word followed by Chinese punctuation
                        if m.start() > 0 and re.match(r"[\u4e00-\u9fff]", segment[m.start()-1]):
                            continue

                        # Skip if single word (no spaces) - likely a term in a list
                        # e.g. "Transformer，"
                        if " " not in m.group():
                             # Check if the matched part (word+punct) has spaces?
                             # m.group() includes punctuation. "Transformer，" has no space.
                             # But "Large Language Model，" has spaces.
                             if " " not in segment[max(0, m.start()-20):m.end()]: # Heuristic: look back
                                 # Or just check the whole segment if it's short?
                                 if len(segment) < 50 and " " not in segment:
                                     continue

                        # Check if inside Chinese parentheses (e.g. "（Large Language Model，LLM）")
                        match_start = m.start()
                        pre_text = segment[:match_start]
                        post_text = segment[m.end():]
                        if "（" in pre_text and "）" in post_text:
                            last_open = pre_text.rfind("（")
                            next_close = post_text.find("）")
                            if "）" not in pre_text[last_open:] and "（" not in post_text[:next_close]:
                                continue

                        issues.append({
                            "issue_type": "Punctuation_Mixed",
                            "severity": "Info",
                            "evidence": _context_snippet(segment, m.start(), m.end()),
                            "page_num": pg,
                            "message": "英文文本使用了中文标点",
                        })

        # 2. Citation Position Check
        if self.check_position:
            punct_cite_pattern = re.compile(r"([，。,\.])\s*(\[\d+\])")

            for e in elements:
                if _is_likely_reference(e):
                    continue
                text_val = _element_get(e, "content")
                pg = _element_get(e, "page_num") or "?"

                if text_val:
                    segment = str(text_val)
                    for m in punct_cite_pattern.finditer(segment):
                        issues.append({
                            "issue_type": "Citation_Position_Inconsistent",
                            "severity": "Info",
                            "evidence": _context_snippet(segment, m.start(), m.end()),
                            "page_num": pg,
                            "message": "引用标注位置错误 (应置于标点符号之前)",
                        })


def _raw_text_has_numbered_reference_section(s: str) -> bool:
    """
    PDF→MD 常在文末有「# 参考文献」+ 多条 [1] … 条目；layout 分块若未带该段，会误报「无参考文献列表」。
    用原始全文做一次兜底检测。
    """
    if not s or len(s.strip()) < 60:
        return False
    anchor = re.search(
        r"(?:^|\n)#+\s*(?:参考文献|References)\s*(?:\n|$)",
        s,
        re.I | re.M,
    )
    if not anchor:
        anchor = re.search(r"(?:^|\n)\s*参考文献\s*\n", s, re.M)
    if not anchor:
        return False
    tail = s[anchor.end() :]
    hits = re.findall(r"(?m)^\s*\[\s*\d+\s*\]\s+\S", tail[:200000])
    return len(hits) >= 2


def _reference_number_set_from_raw_content(s: str) -> set:
    """
    从全文 MD 的「参考文献」节收集 [n] 编号，与 layout 提取结果合并。
    截断到「致谢 / 在学期间…」等标题之前，避免把后置「学术成果」里的 [1][2] 误并入参考文献表。
    """
    if not s or len(s.strip()) < 80:
        return set()
    anchor = re.search(
        r"(?:^|\n)#+\s*(?:参考文献|References)\s*(?:\n|$)",
        s,
        re.I | re.M,
    )
    if not anchor:
        anchor = re.search(r"(?:^|\n)\s*参考文献\s*\n", s, re.M)
    if not anchor:
        return set()
    tail = s[anchor.end() :][:500000]
    stop = re.search(
        r"(?:^|\n)#+\s*(?:致\s*谢|致谢|在学期间|附录|作者简介|攻读|学位论文独创性)",
        tail,
        re.I | re.M,
    )
    if stop:
        tail = tail[: stop.start()]
    return {m.group(1) for m in re.finditer(r"(?m)^\s*\[\s*(\d+)\s*\]", tail)}


class CitationChecker:
    """
    语义判定 - 引用格式语义校验
    对应《分工明细》二、语义判定 - 阶段3
    """
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.style = config.get("style", "IEEE")
        self.allow_mixed_styles = bool(config.get("allow_mixed_styles", False))
        self.enforce_style = bool(config.get("enforce_style", True))
        self.style_inconsistent_severity = config.get("style_inconsistent_severity", "Warning")
        self.style_mismatch_severity = config.get("style_mismatch_severity", "Warning")
        self.reference_tail_ratio = float(config.get("reference_detect_tail_ratio", 0.25) or 0.25)

    def check(self, content: str, layout_data: Dict[str, Any], issues: List[Dict], mapper: Optional['TextPageMapper'] = None):
        """
        1. 引用风格一致性 (IEEE vs APA)
        2. 引用标注与参考文献条目匹配 (语义匹配)
        """
        if not content:
            return
        text = content
        elements = layout_data.get("elements", []) if isinstance(layout_data, dict) else []
        reference_texts = []
        for e in elements:
            if _is_likely_reference(e):
                text_value = _element_get(e, "content")
                if text_value:
                    reference_texts.append(str(text_value))

        if not reference_texts and elements:
            pages = []
            for e in elements:
                p = _element_get(e, "page_num")
                if p is None:
                    continue
                try:
                    pages.append(int(p))
                except Exception:
                    continue
            max_page = max(pages) if pages else 0
            ref_title_pat = re.compile(r"^\s*(参考文献|references?)\s*$", flags=re.IGNORECASE)
            ref_item_pat = re.compile(r"^\s*(\[\d+\]|\d+\.)\s+\S")
            start_page = None
            for e in elements:
                c = str(_element_get(e, "content") or "").strip()
                if not c:
                    continue
                if ref_title_pat.match(c):
                    try:
                        start_page = int(_element_get(e, "page_num"))
                    except Exception:
                        start_page = None
                    break
            if start_page is None and max_page > 0:
                tail_pages = max(1, int(round(max_page * float(1.0 - self.reference_tail_ratio))))
                start_page = tail_pages
            if start_page is not None:
                for e in elements:
                    p = _element_get(e, "page_num")
                    try:
                        p_int = int(p)
                    except Exception:
                        continue
                    if p_int < int(start_page):
                        continue
                    c = str(_element_get(e, "content") or "")
                    if not c or len(c) < 15:
                        continue
                    if ref_item_pat.match(c):
                        reference_texts.append(c)

        # Sort or process reference texts if needed (layout analysis might be out of order?)
        # For now, assume sequential.
        ref_nums = set(_extract_reference_numbers(reference_texts))
        # layout 常未含完整参考文献块，用原始 MD 节合并编号，避免误报「正文引用无对应条目」
        ref_nums |= _reference_number_set_from_raw_content(text)
        numeric_citations = _extract_numeric_citations(text)
        author_year_citations = _extract_author_year_citations(text)

        def _format_pages(pages: set) -> str:
            if not pages:
                return "?"
            try:
                return ", ".join(sorted({str(p) for p in pages}, key=lambda x: int(x) if str(x).isdigit() else 9999))
            except Exception:
                return ", ".join(sorted({str(p) for p in pages}))

        numeric_pages = set()
        author_year_pages = set()
        if mapper:
            for m in re.finditer(r"(?<![\w\]])\[(\d+(?:\s*,\s*\d+)*)\]", text):
                numeric_pages.add(mapper.get_page_num(m.start()))
            author_year_pat = re.compile(
                r"\(([^()]*\b[A-Za-z][A-Za-z'\-]+(?:\s+et\s+al\.)?[^()]*?,\s*\d{4}[a-z]?[^()]*)\)"
            )
            for m in author_year_pat.finditer(text):
                author_year_pages.add(mapper.get_page_num(m.start()))

        if numeric_citations and author_year_citations:
            if not self.allow_mixed_styles:
                issues.append(
                    {
                        "issue_type": "Citation_Style_Inconsistent",
                        "severity": self.style_inconsistent_severity,
                        "evidence": "numeric + author-year",
                        "page_num": _format_pages(numeric_pages | author_year_pages),
                        "message": "正文引用风格不一致",
                    }
                )
        style = (self.style or "").strip().lower()
        if style and style not in {"auto"} and self.enforce_style:
            if style in {"ieee", "numeric"} and author_year_citations:
                issues.append(
                    {
                        "issue_type": "Citation_Style_Mismatch",
                        "severity": self.style_mismatch_severity,
                        "evidence": "author-year",
                        "page_num": _format_pages(author_year_pages),
                        "message": "引用风格与配置不一致，建议使用数字编号引用",
                    }
                )
            if style in {"apa", "mla", "author-year"} and numeric_citations:
                issues.append(
                    {
                        "issue_type": "Citation_Style_Mismatch",
                        "severity": self.style_mismatch_severity,
                        "evidence": "numeric",
                        "page_num": _format_pages(numeric_pages),
                        "message": "引用风格与配置不一致，建议使用作者-年份引用",
                    }
                )
        if not self.config.get("check_reference_matching", True):
            return

        if not reference_texts and (numeric_citations or author_year_citations):
            has_ref_section = False
            try:
                # Direct evidence of a reference section: title or region tagging
                for e in elements:
                    c = str(_element_get(e, "content") or "").strip()
                    if not c:
                        continue
                    if is_reference_title(c):
                        has_ref_section = True
                        break
                    if (_element_get(e, "region") or "") == "reference":
                        has_ref_section = True
                        break
                # Fallback: tail detection of numbered items resembling references
                if not has_ref_section:
                    pages = []
                    for e in elements:
                        p = _element_get(e, "page_num")
                        try:
                            pages.append(int(p))
                        except Exception:
                            continue
                    max_page = max(pages) if pages else 0
                    start_page = None
                    for e in elements:
                        c = str(_element_get(e, "content") or "").strip()
                        if is_reference_title(c):
                            try:
                                start_page = int(_element_get(e, "page_num"))
                            except Exception:
                                start_page = None
                            break
                    if start_page is None and max_page > 0:
                        tail_pages = max(1, int(round(max_page * float(1.0 - self.reference_tail_ratio))))
                        start_page = tail_pages
                    ref_item_pat = re.compile(r"^\s*(\[\d+\]|\d+\.)\s+\S")
                    if start_page is not None:
                        for e in elements:
                            p = _element_get(e, "page_num")
                            try:
                                p_int = int(p)
                            except Exception:
                                continue
                            if p_int < int(start_page):
                                continue
                            c = str(_element_get(e, "content") or "")
                            if ref_item_pat.match(c or ""):
                                has_ref_section = True
                                break
            except Exception:
                has_ref_section = False
            if not has_ref_section:
                has_ref_section = _raw_text_has_numbered_reference_section(text)
            if not has_ref_section:
                issues.append(
                    {
                        "issue_type": "Reference_List_Missing",
                        "severity": "Warning",
                        "evidence": "",
                        "message": "正文存在引用标注，但未检测到参考文献列表",
                    }
                )
                return

        if numeric_citations:
            # Re-scan to find locations
            for m in re.finditer(r"(?<![\w\]])\[(\d+(?:\s*,\s*\d+)*)\]", text):
                citation_text = m.group(0)
                nums_in_cite = [n for n in re.findall(r"\d+", citation_text) if n and n != "0"]
                if not nums_in_cite:
                    continue
                try:
                    if any(int(n) >= 1000 for n in nums_in_cite):
                        continue
                    if any(int(n) >= 150 for n in nums_in_cite):
                        continue
                except Exception:
                    continue
                # Filter out numbers far beyond reference numbering range (likely non-citation)
                max_ref_val = 0
                try:
                    if ref_nums:
                        max_ref_val = max(int(x) for x in ref_nums if str(x).isdigit())
                except Exception:
                    max_ref_val = 0
                if max_ref_val == 0 and reference_texts:
                    try:
                        max_ref_val = max(1, len(reference_texts))
                    except Exception:
                        max_ref_val = 0
                filtered_nums = nums_in_cite
                if max_ref_val > 0:
                    try:
                        filtered_nums = [n for n in nums_in_cite if int(n) <= max_ref_val + 10]
                    except Exception:
                        filtered_nums = nums_in_cite
                missing_nums = [n for n in filtered_nums if n not in ref_nums]

                if missing_nums:
                    pg = mapper.get_page_num(m.start()) if mapper else "?"
                    ref_pages = set()
                    for e in elements:
                        if _is_likely_reference(e):
                            p = _element_get(e, "page_num")
                            if p is not None:
                                ref_pages.add(p)
                    if pg in ref_pages:
                        continue
                    for missing in missing_nums:
                        issues.append({
                            "issue_type": "Citation_Reference_Missing",
                            "severity": "Info",
                            "evidence": f"[{missing}]",
                            "page_num": pg,
                            "message": "引用标注未在参考文献中找到对应编号",
                        })

        if author_year_citations and reference_texts:
            ref_lower = [r.lower() for r in reference_texts]

            # Re-scan to find locations
            for m in re.finditer(r"\(([^()]*\d{4}[a-z]?[^()]*)\)", text):
                citation_inner = m.group(1)
                if not re.search(r"[A-Za-z]", citation_inner):
                    continue

                parsed = _parse_author_year(citation_inner)
                if not parsed:
                    continue

                author = parsed["author"]
                year = parsed["year"]
                matched = False
                for line in ref_lower:
                    if year in line and author in line:
                        matched = True
                        break

                if not matched:
                    pg = mapper.get_page_num(m.start()) if mapper else "?"
                    ref_pages = set()
                    for e in elements:
                        if _is_likely_reference(e):
                            p = _element_get(e, "page_num")
                            if p is not None:
                                ref_pages.add(p)
                    if pg in ref_pages:
                        continue
                    issues.append(
                        {
                            "issue_type": "Citation_Reference_Missing",
                            "severity": "Info",
                            "evidence": m.group(0),
                            "page_num": pg,
                            "message": "作者-年份引用未在参考文献中找到对应条目",
                        }
                    )


class SemanticChecker:
    """
    语义判定总入口
    负责规则层面的格式校验，并与CV层结果融合
    """
    def __init__(self):
        self.llm_client = LLMClient()
        self.rules = {}

        # Initialize sub-checkers with empty rules initially
        self.typo_checker = TypoChecker({})
        self.term_checker = TerminologyChecker({})
        self.punct_checker = PunctuationChecker({})
        self.cite_checker = CitationChecker({})

    def update_rules(self, rules: Dict[str, Any]):
        """
        Update rules for all sub-checkers
        """
        self.rules = rules
        # Update sub-checkers with specific rule sections
        # Assuming rules structure matches sub-checker expectations
        self.typo_checker.config = rules.get("typo_check", {})
        self.term_checker.config = rules.get("terminology_check", {})
        self.punct_checker.config = rules.get("punctuation_check", {})
        self.cite_checker.config = rules.get("citation_check", {})

        # Re-initialize or update internal config dependent logic if needed
        # For example, TypoChecker reads max_typos_total in __init__
        self.typo_checker = TypoChecker(self.typo_checker.config)
        self.term_checker = TerminologyChecker(self.term_checker.config)
        self.punct_checker = PunctuationChecker(self.punct_checker.config)
        self.cite_checker = CitationChecker(self.cite_checker.config)

    def _dedupe_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        out: List[Dict[str, Any]] = []
        for issue in issues or []:
            if not isinstance(issue, dict):
                continue
            issue_type = str(issue.get("issue_type") or "")
            severity = str(issue.get("severity") or issue.get("level") or "")
            page_num = str(issue.get("page_num") or "?")
            message = str(issue.get("message") or "")
            evidence = str(issue.get("evidence") or "")
            bbox = issue.get("bbox")
            bbox_key = None
            if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                try:
                    bbox_key = tuple(round(float(x), 1) for x in bbox)
                except Exception:
                    bbox_key = None
            key = (issue_type, severity, page_num, message, evidence, bbox_key)
            if key in seen:
                continue
            seen.add(key)
            out.append(issue)
        return out

    @staticmethod
    def _parse_commentary_llm_json(raw: str) -> Tuple[Optional[str], Optional[str]]:
        """LLM 常返回 Markdown 代码块或非纯 JSON，做兜底解析。"""
        s = (raw or "").strip()
        if not s:
            return None, None
        candidates: List[str] = [s]
        m_fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", s, re.I)
        if m_fence:
            candidates.append(m_fence.group(1).strip())
        m_obj = re.search(r"\{[\s\S]*\}", s)
        if m_obj:
            candidates.append(m_obj.group(0).strip())
        seen: set = set()
        for c in candidates:
            if not c or c in seen:
                continue
            seen.add(c)
            try:
                data = json.loads(c)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, dict):
                comment = str(data.get("comment") or "").strip()
                suggestion = str(data.get("suggestion") or "").strip()
                if comment and suggestion:
                    return comment, suggestion
        return None, None

    async def _extract_facts_llm(self, text: str) -> str:
        if self.llm_client.provider == "none":
            return ""
        content = (text or "").strip()
        if not content:
            return ""
        rag = self.rules.get("rag_eval", {}) if isinstance(self.rules, dict) else {}
        try:
            cap = int(rag.get("max_facts_chars", 80000) or 80000)
        except (TypeError, ValueError):
            cap = 80000
        cap = max(4000, min(cap, 200000))
        user_prompt = content[:cap] if len(content) > cap else content
        system_prompt = (
            "你是论文格式审计助手。请从用户提供的论文片段中提取可核查的客观事实点，"
            "用于后续与专家评语进行语义匹配。输出要求：1) 中文；2) 条目化；3) 每条不超过30字；"
            "4) 只输出事实点，不要解释。"
        )
        try:
            return (await self.llm_client.generate_text(system_prompt, user_prompt, temperature=0.1, max_tokens=800)).strip()
        except Exception as e:
            logger.warning(f"facts extraction failed: {e}")
            return ""

    async def _retrieve_expert_comments(
        self,
        query_vector: List[float],
        top_k: int,
        require_text: bool,
        require_metric_id: bool,
        require_active: bool,
    ) -> List[str]:
        if not query_vector:
            return []
        try:
            async with db_manager.session() as session:
                distance = ExpertComment.embedding.op("<=>")(query_vector)
                predicates = [ExpertComment.embedding.is_not(None)]
                if require_text:
                    predicates.append(ExpertComment.text.is_not(None))
                    predicates.append(func.length(func.trim(ExpertComment.text)) > 0)
                if require_metric_id:
                    predicates.append(ExpertComment.metric_id.is_not(None))
                    predicates.append(func.length(func.trim(ExpertComment.metric_id)) > 0)
                if require_active:
                    predicates.append(or_(ExpertComment.active.is_(True), ExpertComment.active.is_(None)))

                stmt = select(ExpertComment.text).where(*predicates).order_by(distance.asc()).limit(top_k)
                result = await session.execute(stmt)
                return [row[0] for row in result.all() if row and row[0]]
        except Exception as e:
            logger.warning(f"expert comment retrieval failed: {e}")
            return []

    async def generate_expert_commentary(
        self, content: str, issues: List[Dict[str, Any]], top_k: int = 5
    ) -> Tuple[Optional[str], Optional[str]]:
        if self.llm_client.provider == "none":
            return None, None
        llm_cfg = self.rules.get("rag_eval", {}) if isinstance(self.rules, dict) else {}
        enabled = bool(llm_cfg.get("enabled", True))
        if not enabled:
            return None, None
        top_k_val = int(llm_cfg.get("top_k", top_k) or top_k)
        top_k_val = max(1, min(20, top_k_val))
        require_text = bool(llm_cfg.get("require_text", True))
        require_metric_id = bool(llm_cfg.get("require_metric_id", False))
        require_active = bool(llm_cfg.get("require_active", True))

        facts = await self._extract_facts_llm(content)
        if not facts:
            return None, None

        try:
            query_vector = await asyncio.to_thread(_embed_text_expert_comment, facts)
        except Exception as e:
            logger.warning(f"facts embedding failed: {e}")
            return None, None

        expert_texts = await self._retrieve_expert_comments(
            query_vector,
            top_k=top_k_val,
            require_text=require_text,
            require_metric_id=require_metric_id,
            require_active=require_active,
        )

        issue_counts: Dict[str, int] = {}
        for it in issues or []:
            if isinstance(it, dict):
                t = it.get("issue_type") or "Unknown"
            else:
                t = getattr(it, "issue_type", "Unknown")
            issue_counts[t] = issue_counts.get(t, 0) + 1
        issue_summary = "\n".join([f"- {k}: {v}" for k, v in sorted(issue_counts.items(), key=lambda x: (-x[1], x[0]))]) or "-"

        expert_block = "\n".join([f"- {t}" for t in expert_texts]) or "-"

        system_prompt = (
            "你是论文标准化审计专家。请基于用户提供的事实点、问题概览，以及检索到的专家评语，"
            "输出两段中文内容：comment 和 suggestion。必须以JSON对象输出，键为 comment 和 suggestion，"
            "不要输出多余文本。comment 用于概述问题与严重性，suggestion 给出可执行修改建议。"
        )
        user_prompt = json.dumps(
            {
                "facts": facts,
                "issue_summary": issue_summary,
                "expert_comments": expert_block,
            },
            ensure_ascii=False,
        )
        try:
            raw = (await self.llm_client.generate_text(system_prompt, user_prompt, temperature=0.2, max_tokens=800)).strip()
            comment, suggestion = self._parse_commentary_llm_json(raw)
            if comment and suggestion:
                return comment, suggestion
            if raw:
                logger.warning(
                    "commentary generation: LLM 返回无法解析为含 comment/suggestion 的 JSON（前 120 字）：%s",
                    raw[:120].replace("\n", " "),
                )
        except Exception as e:
            logger.warning(f"commentary generation failed: {e}")
        return None, None

    def _split_long_paragraph(self, para: str, chunk_size: int, overlap: int) -> List[str]:
        parts: List[str] = []
        if not para or chunk_size <= 0:
            return parts

        s = 0
        min_chunk_size = min(2000, chunk_size // 5)  # Soft lower limit to avoid tiny chunks

        while s < len(para):
            # 1. Determine maximum possible reach
            target = min(s + chunk_size, len(para))

            # If we reached the end, just take it
            if target >= len(para):
                parts.append(para[s:target])
                break

            # 2. Find the best cut point using weighted scoring
            # Goal: Find a cut point close to target, prioritizing sentence completeness.
            # Strategy: Search backwards from target (+lookahead) to s + min_chunk_size.
            # Score = (Distance from s) + Priority_Bonus

            strong_punct = {".", "!", "?", "。", "！", "？"}
            weak_punct = {";", ":", "；", "："}
            clause_punct = {",", "，", "、"}

            lookahead_limit = min(len(para), target + 100)

            # Helper to find last occurrence in range [start, end)
            def find_last_in_range(start_idx, end_idx, chars):
                for i in range(end_idx - 1, start_idx - 1, -1):
                    if para[i] in chars:
                        return i + 1 # Include the delimiter
                return -1

            # Search range: [s + min_chunk_size, lookahead_limit]
            search_start = min(s + min_chunk_size, target)
            search_end = lookahead_limit

            candidates = []

            # Priority 1: Strong Punctuation (Period)
            # Bonus: 10000. Prefers Period at 5000 (Score 15000) over Semicolon at 5000 (Score 9000).
            # But Semicolon at 14000 (Score 18000) beats Period at 5000.
            c_strong = find_last_in_range(search_start, search_end, strong_punct)
            if c_strong != -1:
                candidates.append((c_strong, 10000))

            # Priority 2: Weak Punctuation (Semicolon)
            # Bonus: 4000.
            c_weak = find_last_in_range(search_start, search_end, weak_punct)
            if c_weak != -1:
                candidates.append((c_weak, 4000))

            # Priority 3: Clause Punctuation (Comma)
            # Bonus: 1000.
            c_clause = find_last_in_range(search_start, search_end, clause_punct)
            if c_clause != -1:
                candidates.append((c_clause, 1000))

            # Priority 4: Spaces (for English/Code)
            # Bonus: 0.
            c_space = -1
            for i in range(search_end - 1, search_start - 1, -1):
                if para[i].isspace():
                    c_space = i + 1
                    break
            if c_space != -1:
                candidates.append((c_space, 0))

            # Select best candidate
            best_cut = -1
            best_score = -1

            for cut_idx, weight in candidates:
                score = (cut_idx - s) + weight
                if score > best_score:
                    best_score = score
                    best_cut = cut_idx

            # Fallback: If no valid cut found in preferred range (e.g. first 2000 chars have no punctuation)
            if best_cut == -1:
                # Try searching in the "small chunk" range [s, search_start]
                # Prioritize Strong > Weak > Clause > Space > Hard Cut
                fallback_end = search_start

                c_strong = find_last_in_range(s, fallback_end, strong_punct)
                if c_strong != -1:
                    best_cut = c_strong
                elif (c_weak := find_last_in_range(s, fallback_end, weak_punct)) != -1:
                    best_cut = c_weak
                elif (c_clause := find_last_in_range(s, fallback_end, clause_punct)) != -1:
                    best_cut = c_clause
                elif (c_space := find_last_in_range(s, fallback_end, {' ', '\t', '\n'})) != -1:
                     best_cut = c_space
                else:
                    best_cut = target # Hard cut

            # Final Safety Check
            if best_cut <= s:
                best_cut = target

            parts.append(para[s:best_cut])

            # If we reached the end of the paragraph, stop here.
            # No need to backtrack for overlap if we are done.
            if best_cut >= len(para):
                break

            # Calculate next start with overlap
            next_s = max(0, best_cut - overlap)
            if next_s <= s:
                next_s = best_cut
            s = next_s

        return parts

    def _chunk_text(self, text: str, chunk_size: int = 5000, overlap: int = 500) -> List[str]:
        """
        Split text into chunks respecting paragraph boundaries.
        If a paragraph is too long, split it by character count.
        """
        if not text:
            return []

        paragraphs = text.split('\n')
        chunks = []

        start_idx = 0
        while start_idx < len(paragraphs):
            current_len = 0
            end_idx = start_idx

            # Expand end_idx until chunk_size is reached
            while end_idx < len(paragraphs):
                p_len = len(paragraphs[end_idx]) + 1 # +1 for newline

                # Check if adding this paragraph exceeds limit
                if current_len + p_len > chunk_size:
                    if current_len == 0:
                        # Single huge paragraph - split by character count
                        para = paragraphs[end_idx]
                        chunks.extend(self._split_long_paragraph(para, chunk_size, overlap))

                        start_idx += 1
                        end_idx = start_idx # Signal processed
                        break
                    else:
                        # Stop here, don't include this paragraph in current chunk
                        break

                current_len += p_len
                end_idx += 1

            # If we processed a huge paragraph (current_len == 0 but incremented start_idx), continue
            if current_len == 0:
                if start_idx < len(paragraphs):
                    continue
                else:
                    break

            # If we formed a chunk
            if end_idx > start_idx:
                chunk_text = "\n".join(paragraphs[start_idx:end_idx])
                chunks.append(chunk_text)

            if end_idx == len(paragraphs):
                break

            # Calculate next start_idx based on overlap (backtrack from end_idx)
            overlap_len = 0
            next_start = end_idx
            # Try to keep at least 'overlap' characters from the end of current chunk
            while next_start > start_idx:
                p_len = len(paragraphs[next_start-1]) + 1
                if overlap_len + p_len > overlap:
                    break
                overlap_len += p_len
                next_start -= 1

            # Ensure progress
            if next_start == start_idx:
                next_start += 1

            start_idx = next_start

        return chunks

    async def check(
        self, content: str, layout_data: Dict[str, Any], *, detailed_rules: bool = True
    ) -> Dict[str, Any]:
        """
        执行语义校验的主流程。
        detailed_rules=False 时跳过 rules.yaml 驱动的错别字/术语/标点/引用细则，仍保留布局相关 issue 并尽量启用 LLM 扫描作基本审阅。
        """
        # 支持规则热更新 (可选，每次请求检查更新)
        # self.rule_engine.reload()

        issues = []

        # Initialize Mapper
        mapper = TextPageMapper(layout_data)
        # Use mapper's text to ensure consistency with indices
        text_content = mapper.full_text
        if not text_content:
            # Fallback if mapper produced empty text (e.g. no elements)
            text_content = _resolve_text_content(content, layout_data)

        logger.debug(f"text_content length: {len(text_content)}")

        # 1. 规则校验 (各模块独立执行)
        if detailed_rules:
            try:
                logger.debug("Running typo_checker...")
                self.typo_checker.check(text_content, issues, mapper)
            except Exception as e:
                logger.error(f"typo_checker failed: {e}")

            try:
                logger.debug("Running term_checker...")
                self.term_checker.check(text_content, issues, mapper)
            except Exception as e:
                logger.error(f"term_checker failed: {e}")

            try:
                logger.debug("Running punct_checker...")
                self.punct_checker.check(text_content, layout_data, issues, mapper)
            except Exception as e:
                logger.error(f"punct_checker failed: {e}")

            try:
                logger.debug("Running cite_checker...")
                self.cite_checker.check(text_content, layout_data, issues, mapper)
                logger.debug("cite_checker finished.")
            except Exception as e:
                logger.error(f"cite_checker failed: {e}")
        else:
            logger.info("detailed_rules=False: skipping YAML-driven typo/term/punct/citation checks")

        # 2. LLM 辅助扫描 (Gemini 1.5 Flash / Qwen)
        # 利用长上下文能力辅助扫描复杂格式问题
        # Ref: 分工明细 - LLM Scanner
        base_llm = self.rules.get("llm_scan", {}) if isinstance(self.rules, dict) else {}
        llm_cfg = dict(base_llm) if isinstance(base_llm, dict) else {}
        if not detailed_rules:
            # 基本模式：仍做 LLM 扫描；块数与细则一致（max_chunks=0 表示 max_text_chars 内全部块）
            llm_cfg["enabled"] = True
        llm_enabled = bool(llm_cfg.get("enabled", False))
        llm_max_text_chars = int(llm_cfg.get("max_text_chars", 60000))
        try:
            llm_max_chunks = int(llm_cfg.get("max_chunks", 0))
        except (TypeError, ValueError):
            llm_max_chunks = 0

        if not llm_enabled:
            logger.warning("LLM scan disabled by rules (llm_scan.enabled=false). Skipping LLM scan.")
            llm_feedback = ""
            all_llm_issues = []
        elif self.llm_client.provider == "none":
            logger.warning("LLM scan unavailable (LLM provider not configured or API key missing). Skipping LLM scan.")
            llm_feedback = ""
            all_llm_issues = []
        else:
            logger.debug(f"Starting LLM scan with provider: {self.llm_client.provider}...")
            llm_feedback = ""
            all_llm_issues = []

            try:
                llm_text = text_content
                if llm_max_text_chars > 0 and len(llm_text) > llm_max_text_chars:
                    llm_text = llm_text[:llm_max_text_chars]
                # Process text in chunks for LLM
                chunks = self._chunk_text(llm_text, chunk_size=15000, overlap=500)
                if llm_max_chunks > 0:
                    chunks = chunks[:llm_max_chunks]
                logger.debug(f"Chunks created: {len(chunks)} (max_chunks={llm_max_chunks})")

                feedback_parts = []
                last_pos = 0

                for i, chunk in enumerate(chunks):
                    print(f"Processing chunk {i+1}/{len(chunks)}... Length: {len(chunk)}")
                    logger.debug(f"Processing chunk {i+1}/{len(chunks)}... Length: {len(chunk)}")

                    # Calculate page range for this chunk
                    page_range = "?"
                    start_pos = text_content.find(chunk, last_pos)
                    # If not found (shouldn't happen), try from 0
                    if start_pos == -1:
                        start_pos = text_content.find(chunk)

                    if start_pos != -1:
                        end_pos = start_pos + len(chunk)
                        page_range = mapper.get_page_range(start_pos, end_pos)
                        # Advance last_pos, but allow for overlap (next chunk starts before this one ends)
                        # We just need to ensure we don't find the SAME chunk instance again if there are duplicates?
                        # Since chunks are sequential, simple find from last_pos is usually safe.
                        # Ideally last_pos should track 'processed up to'.
                        # But chunks overlap. Next chunk will start around 'end_pos - overlap'.
                        # So let's update last_pos to start_pos + 1 to be safe.
                        last_pos = start_pos + 1

                    try:
                        chunk_feedback = await self.llm_client.scan_document(chunk)
                        if chunk_feedback:
                            # Parse issues from this chunk
                            chunk_issues, chunk_summary = self._parse_llm_response(chunk_feedback)

                            # Use summary if available, otherwise raw feedback
                            if chunk_summary:
                                feedback_parts.append(chunk_summary)
                            else:
                                feedback_parts.append(chunk_feedback)

                            # Tag issues as from LLM and add page range
                            for issue in chunk_issues:
                                issue["source"] = "LLM"
                                if "page_num" not in issue or issue["page_num"] == "?":
                                    issue["page_num"] = page_range

                            all_llm_issues.extend(chunk_issues)
                    except Exception as e:
                        logger.error(f"Chunk {i+1} processing failed: {e}")



                # Aggregate results
                llm_feedback = "\n".join(feedback_parts)
                logger.debug("LLM scan completed.")
            except Exception as e:
                logger.exception(f"LLM scan failed: {e}")

        # Merge LLM issues into main issues list
        if all_llm_issues:
            issues.extend(all_llm_issues)

        # 3. 结果融合与去重
        # 避免视觉层和语义层对同一问题的重复标注
        # Ref: 分工明细 - 阶段4: 结果去重逻辑

        issues = self._dedupe_issues(issues)

        def _infer_doc_page_range() -> str:
            elements = layout_data.get("elements", []) if isinstance(layout_data, dict) else []
            pages: List[int] = []
            for e in elements:
                p = _element_get(e, "page_num")
                if p is None:
                    continue
                try:
                    pages.append(int(p))
                except Exception:
                    continue
            if not pages:
                if isinstance(layout_data, dict):
                    selected_pages = layout_data.get("selected_pages")
                    if isinstance(selected_pages, list):
                        selected_ints: List[int] = []
                        for p in selected_pages:
                            try:
                                pi = int(p)
                            except Exception:
                                continue
                            if pi > 0:
                                selected_ints.append(pi)
                        if selected_ints:
                            lo, hi = min(selected_ints), max(selected_ints)
                            return str(lo) if lo == hi else f"{lo}-{hi}"

                    parse_report = layout_data.get("parse_report")
                    if isinstance(parse_report, dict):
                        for k in ("page_count", "total_pages", "num_pages"):
                            v = parse_report.get(k)
                            try:
                                vi = int(v)
                            except Exception:
                                vi = 0
                            if vi > 0:
                                return "1" if vi == 1 else f"1-{vi}"
                        pages_val = parse_report.get("pages")
                        if isinstance(pages_val, list):
                            page_ints: List[int] = []
                            for p in pages_val:
                                try:
                                    pi = int(p)
                                except Exception:
                                    continue
                                if pi > 0:
                                    page_ints.append(pi)
                            if page_ints:
                                lo, hi = min(page_ints), max(page_ints)
                                return str(lo) if lo == hi else f"{lo}-{hi}"

                return "N/A"
            lo, hi = min(pages), max(pages)
            if lo <= 0 or hi <= 0:
                return "N/A"
            if lo == hi:
                return str(lo)
            return f"{lo}-{hi}"

        doc_page_range = _infer_doc_page_range()
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            pg_raw = issue.get("page_num")
            pg_str = str(pg_raw).strip() if pg_raw is not None else ""
            if not pg_str or pg_str == "?":
                loc = issue.get("location")
                if isinstance(loc, dict):
                    p = loc.get("page_num")
                    if p is None:
                        p = loc.get("page")
                    if p is not None and str(p).strip():
                        issue["page_num"] = str(p).strip()
                        continue
                issue["page_num"] = doc_page_range

        return {
            "semantic_issues": issues,
            "llm_feedback": llm_feedback,
            "score": self._calculate_score(issues)
        }

    def _parse_llm_response(self, response_text: str) -> Tuple[List[Dict], str]:
        """
        Parses the LLM response which is expected to be a JSON string.
        Returns a tuple of (issues found by LLM, summary string).
        """
        issues = []
        summary = ""
        try:
            # Clean response text
            cleaned = response_text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            elif cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            parsed = json.loads(cleaned)

            # Handle different response formats
            if isinstance(parsed, dict):
                issues = parsed.get("issues", [])
                summary = parsed.get("summary", "")
            elif isinstance(parsed, list):
                # If root is a list of issues
                issues = parsed

        except json.JSONDecodeError:
            # Fallback: try to find JSON object using regex if mixed with text
            try:
                match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if match:
                    parsed = json.loads(match.group(0))
                    if isinstance(parsed, dict):
                        issues = parsed.get("issues", [])
                        summary = parsed.get("summary", "")
            except:
                pass

        # Ensure issues is a list of dicts
        if not isinstance(issues, list):
            issues = []

        # Post-process issues to handle false positives from LLM
        allowed_llm_issue_types = {
            "Terminology_Inconsistency",
            "Terminology_Forbidden",
            "Terminology_Language",
            "Abbreviation_Definition",
            "Citation_Placeholder",
        }

        filtered_issues = []
        for issue in issues:
            if issue.get("issue_type") not in allowed_llm_issue_types:
                continue
            # Fix for "Citation_Placeholder" being too aggressive on isolated lines
            # If evidence looks like a valid citation (e.g. [12], [21,23]), it's likely a layout/parsing artifact, not a missing placeholder.
            if issue.get("issue_type") == "Citation_Placeholder":
                evidence = issue.get("evidence", "").strip()
                msg = issue.get("message", "")

                # Condition 1: Evidence is a valid citation pattern
                is_valid_citation = bool(re.match(r"^\[[\d,\s-]+\]$", evidence))

                # Condition 2: Message explicitly mentions "isolated" or "single line" citations
                # and contains citation-like patterns
                is_isolated_msg = ("孤立" in msg or "单独成行" in msg) and re.search(r"\[\d+(?:,\s*\d+)*\]", msg)

                if is_valid_citation or is_isolated_msg:
                    issue["severity"] = "Info"
                    issue["message"] += " (疑似排版或解析造成的孤立行，非内容缺失)"
                    issue["issue_type"] = "Citation_Layout_Check"

            filtered_issues.append(issue)

        return filtered_issues, summary

    def _calculate_score(self, issues: List[Dict]) -> int:
        """
        基于问题数量和严重程度计算扣分
        """
        counts = {"Critical": 0, "Warning": 0, "Info": 0}
        for issue in issues:
            level = issue.get("severity") or issue.get("level") or "Info"
            if level not in counts:
                level = "Info"
            counts[level] += 1
        critical = counts["Critical"]
        warning = counts["Warning"]
        info = counts["Info"]
        scoring = self.rules.get("scoring", {}) if isinstance(self.rules, dict) else {}
        critical_w = float(scoring.get("critical_weight", 5.0) or 5.0)
        warning_w = float(scoring.get("warning_weight", 2.0) or 2.0)
        info_w = float(scoring.get("info_weight", 0.5) or 0.5)
        deduction = critical_w * critical + warning_w * math.sqrt(warning) + info_w * math.sqrt(info)
        score = 100 - int(round(deduction))
        return max(0, min(100, score))
