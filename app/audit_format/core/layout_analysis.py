from typing import Dict, Any, List, Optional, Tuple
from pydantic import BaseModel
import asyncio
import re
import statistics
from .pdf_utils import open_pdf, extract_blocks, extract_drawing_regions, is_encrypted, is_scanned_page, split_columns, page_to_image
from .layout_zones import is_reference_title, classify_line_region, is_caption, is_heading_text, is_formula_text
from .layout_exceptions import ParseError, ParseReport
from .layout_rules import check_citation_reference_match, load_rules
from .layout_adapter import with_anchor
from .vision_utils import detect_text_lines, to_gray


class VisualElement(BaseModel):
    type: str
    content: str
    bbox: List[float]
    page_num: int
    region: str
    paper_id: Optional[str] = None
    chunk_id: Optional[str] = None


def _safe_median(values: List[float], default: float = 10.0) -> float:
    if not values:
        return default
    return float(statistics.median(values))


def _bbox_from_rect(rect: Tuple[float, float, float, float]) -> List[float]:
    return [float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])]


def _sort_blocks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(blocks, key=lambda b: (b.get("bbox", [0, 0, 0, 0])[1], b.get("bbox", [0, 0, 0, 0])[0]))


def _text_from_line(line: Dict[str, Any]) -> str:
    spans = line.get("spans", [])
    return "".join([s.get("text", "") for s in spans]).strip()


def _max_font_size(line: Dict[str, Any]) -> float:
    spans = line.get("spans", [])
    sizes = [s.get("size", 0) for s in spans if s.get("size") is not None]
    return float(max(sizes)) if sizes else 0.0


def _find_citations(text: str) -> List[str]:
    matches = []
    t = text or ""
    code_like = bool(
        re.search(
            r"(?:\w+\s*:=|\breturn\b|\bfor\b|\bwhile\b|\bif\b|==|!=|->|=>|;|\{|\}|\w+\[[^\]]+\]|\w+\s*=\s*\w+\s*\()",
            t,
        )
    )
    if not code_like:
        for m in re.finditer(r"\[(\d+(?:\s*,\s*\d+)*)\]", t):
            nums = [int(x) for x in re.findall(r"\d+", m.group(1) or "") if x.isdigit()]
            if nums and any(n == 0 for n in nums):
                continue
            if nums and any(n >= 1000 for n in nums):
                continue
            matches.append(m.group(0))
    for m in re.finditer(r"\(([A-Za-z][^)]{0,40}\d{4}[^)]*)\)", text):
        matches.append(m.group(0))
    return matches


def _is_toc_like_heading(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if re.search(r"(?:\.{3,}|…{2,}|·{3,}|_{3,}|-{5,})\s*\d+\s*$", t):
        return True
    if re.search(r"(?:\.{2,}|…{2,})", t) and re.search(r"\s{2,}\d+\s*$", t):
        return True
    return False


def _cn_number_to_int(s: str) -> Optional[int]:
    t = (s or "").strip()
    if not t:
        return None
    if t.isdigit():
        try:
            return int(t)
        except Exception:
            return None
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000}
    section = 0
    number = 0
    seen = False
    for ch in t:
        if ch in digits:
            number = digits[ch]
            seen = True
            continue
        if ch in units:
            unit = units[ch]
            if number == 0:
                number = 1
            section += number * unit
            number = 0
            seen = True
            continue
        return None
    if not seen:
        return None
    val = section + number
    return val if val > 0 else None


def _parse_heading_parts(text: str) -> Optional[List[int]]:
    t = (text or "").strip()
    if not t:
        return None
    m = re.match(r"^\s*(\d+(?:\.\d+)*)(?:[.、]|\s+)", t)
    if m:
        rest = t[m.end():].strip()
        if len(rest) < 2:
            return None
        if not re.search(r"[A-Za-z\u4e00-\u9fff]", rest):
            return None
        try:
            return [int(x) for x in (m.group(1) or "").split(".") if x.strip().isdigit()]
        except Exception:
            return None
    m = re.match(r"^第([0-9一二三四五六七八九十百千两零]+)[章节]", t)
    if m:
        n = _cn_number_to_int(m.group(1))
        if n is None:
            return None
        return [n]
    return None


class PDFParser:
    def __init__(self):
        pass

    async def parse(self, content: Any) -> Dict[str, Any]:
        return await asyncio.to_thread(self._parse_sync, content)

    def _parse_sync(self, content: Any) -> Dict[str, Any]:
        parse_errors = []
        parse_report = ParseReport()
        page_set = None
        pdf_payload = content
        if isinstance(content, dict):
            pdf_payload = (
                content.get("pdf_path")
                or content.get("pdf")
                or content.get("path")
                or content.get("content")
            )
            pages = content.get("pages") or content.get("page_nums") or content.get("page_numbers")
            if pages:
                try:
                    page_set = {int(p) for p in pages if int(p) > 0}
                except Exception:
                    page_set = None
        elif isinstance(content, (tuple, list)) and len(content) == 2:
            pdf_payload, pages = content[0], content[1]
            if pages:
                try:
                    page_set = {int(p) for p in pages if int(p) > 0}
                except Exception:
                    page_set = None
        try:
            doc = open_pdf(pdf_payload)
        except Exception as exc:
            parse_errors.append(ParseError(error_type="invalid_pdf", message=str(exc)).model_dump())
            return {"elements": [], "parse_errors": parse_errors, "parse_report": parse_report.model_dump()}
        if is_encrypted(doc):
            parse_report.encrypted = True
            parse_errors.append(ParseError(error_type="encrypted_pdf", message="pdf is encrypted").model_dump())
            return {"elements": [], "parse_errors": parse_errors, "parse_report": parse_report.model_dump()}
        elements: List[VisualElement] = []
        reference_mode_global = False
        for page_index in range(len(doc)):
            page = doc[page_index]
            page_num = page_index + 1
            if page_set is not None and page_num not in page_set:
                continue
            page_rect = page.rect
            scanned = is_scanned_page(page)
            if scanned:
                parse_report.scanned_pages += 1
            blocks = extract_blocks(page)
            page_has_image_blocks = any(b.get("type") == 1 for b in blocks)
            try:
                page_has_raster_images = bool(page.get_images(full=True))
            except Exception:
                page_has_raster_images = False
            page_has_any_images = page_has_image_blocks or page_has_raster_images
            columns = split_columns(blocks, page_rect.width)
            if len(columns) > 1:
                parse_report.multi_column_pages += 1

            # CV Analysis Integration
            visual_lines = []
            if scanned:
                try:
                    img = page_to_image(page)
                    gray = to_gray(img)
                    visual_lines = detect_text_lines(gray)
                    parse_report.visual_elements_count += len(visual_lines)
                except Exception:
                    pass

            font_sizes = []
            for b in blocks:
                if b.get("type") != 0:
                    continue
                for line in b.get("lines", []):
                    size = _max_font_size(line)
                    if size > 0:
                        font_sizes.append(size)
            body_size = _safe_median(font_sizes, default=10.0)
            reference_mode = reference_mode_global
            if columns:
                if len(columns) > 1:
                    text_blocks = _sort_blocks(columns[0]) + _sort_blocks(columns[1])
                else:
                    text_blocks = _sort_blocks(columns[0])
            else:
                text_blocks = _sort_blocks([b for b in blocks if b.get("type") == 0])
            if not text_blocks:
                # Use CV to check for scanned content
                if visual_lines and len(visual_lines) > 5:
                    elements.append(
                        VisualElement(
                            type="scanned_content",
                            content="[SCANNED_CONTENT_DETECTED]",
                            bbox=[0.0, 0.0, 1.0, 1.0],
                            page_num=page_num,
                            region="main",
                        )
                    )
                else:
                    elements.append(
                        VisualElement(
                            type="image",
                            content="",
                            bbox=[0.0, 0.0, page_rect.width, page_rect.height],
                            page_num=page_num,
                            region="main",
                        )
                    )
                continue
            page_has_caption = False
            caption_lines: List[Tuple[List[float], str]] = []
            pending_caption = None

            def _union_bbox(a: List[float], b: List[float]) -> List[float]:
                return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]

            def _is_caption_continuation(t: str) -> bool:
                s = (t or "").strip()
                if not s:
                    return False
                if is_reference_title(s) or is_caption(s):
                    return False
                if is_heading_text(s) or is_formula_text(s):
                    return False
                if re.fullmatch(r"第\s*\d+\s*页", s):
                    return False
                if re.search(r"[。！？]$", s):
                    return False
                if len(s) < 4 or len(s) > 90:
                    return False
                return True

            def _flush_caption():
                nonlocal pending_caption
                if not pending_caption:
                    return
                caption_lines.append((pending_caption["bbox"], pending_caption["text"]))
                elements.append(
                    VisualElement(
                        type="title",
                        content=pending_caption["text"],
                        bbox=pending_caption["bbox"],
                        page_num=page_num,
                        region="chart",
                    )
                )
                pending_caption = None

            for block in text_blocks:
                block_bbox = _bbox_from_rect(block.get("bbox", (0, 0, 0, 0)))
                for line in block.get("lines", []):
                    text = _text_from_line(line)
                    if not text:
                        continue
                    line_bbox = _bbox_from_rect(line.get("bbox", block_bbox))
                    max_size = _max_font_size(line)
                    if pending_caption:
                        if pending_caption.get("lines", 1) < 3 and _is_caption_continuation(text):
                            pending_caption["bbox"] = _union_bbox(pending_caption["bbox"], line_bbox)
                            pending_caption["text"] = (pending_caption["text"] + " " + text).strip()
                            pending_caption["lines"] = int(pending_caption.get("lines", 1)) + 1
                            continue
                        _flush_caption()
                    if is_reference_title(text):
                        reference_mode = True
                        reference_mode_global = True
                        elements.append(
                            VisualElement(
                                type="title",
                                content=text,
                                bbox=line_bbox,
                                page_num=page_num,
                                region="reference",
                            )
                        )
                        continue
                    if is_caption(text):
                        page_has_caption = True
                        pending_caption = {"bbox": line_bbox, "text": text, "lines": 1}
                        continue
                    region = classify_line_region(text, max_size, body_size, reference_mode)
                    citations = _find_citations(text)
                    if citations:
                        for c in citations:
                            elements.append(
                                VisualElement(
                                    type="citation",
                                    content=c,
                                    bbox=line_bbox,
                                    page_num=page_num,
                                    region="citation",
                                )
                            )
                    if region == "formula":
                        elements.append(
                            VisualElement(
                                type="formula",
                                content=text,
                                bbox=line_bbox,
                                page_num=page_num,
                                region="formula",
                            )
                        )
                        continue
                    if region == "title":
                        elements.append(
                            VisualElement(
                                type="title",
                                content=text,
                                bbox=line_bbox,
                                page_num=page_num,
                                region="title",
                            )
                        )
                        continue
                    region = "reference" if reference_mode else "main"
                    elements.append(
                        VisualElement(
                            type="text",
                            content=text,
                            bbox=line_bbox,
                            page_num=page_num,
                            region=region,
                        )
                    )
            _flush_caption()
            if page_has_caption and not page_has_any_images:
                drawing_regions = extract_drawing_regions(page)
                if drawing_regions:
                    used = set()
                    figure_caption_pat = re.compile(r"^\s*(?:图|Figure|Fig\.?)\s*", flags=re.IGNORECASE)
                    table_caption_pat = re.compile(r"^\s*(?:表|Table)\s*", flags=re.IGNORECASE)
                    for cap_bbox, cap_text in caption_lines:
                        cap_w = max(1.0, cap_bbox[2] - cap_bbox[0])
                        is_figure = bool(figure_caption_pat.match(cap_text or ""))
                        is_table = bool(table_caption_pat.match(cap_text or ""))
                        best = None
                        best_score = None
                        for x0, y0, x1, y1 in drawing_regions:
                            key = (round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1))
                            if key in used:
                                continue
                            overlap = min(cap_bbox[2], x1) - max(cap_bbox[0], x0)
                            if overlap <= 0:
                                continue
                            r_w = max(1.0, x1 - x0)
                            if overlap < min(cap_w, r_w) * 0.2:
                                continue
                            tol = 6.0
                            if is_figure and y0 > cap_bbox[3] + tol:
                                continue
                            if is_table and y1 < cap_bbox[1] - tol:
                                continue
                            if y1 <= cap_bbox[1]:
                                dy = cap_bbox[1] - y1
                            elif y0 >= cap_bbox[3]:
                                dy = y0 - cap_bbox[3]
                            else:
                                dy = 0.0
                            score = dy * 1000.0 + abs((cap_bbox[0] + cap_bbox[2]) / 2.0 - (x0 + x1) / 2.0)
                            if best_score is None or score < best_score:
                                best_score = score
                                best = (x0, y0, x1, y1, key)
                        if best is not None:
                            x0, y0, x1, y1, key = best
                            used.add(key)
                            elements.append(
                                VisualElement(
                                    type="image",
                                    content="",
                                    bbox=[x0, y0, x1, y1],
                                    page_num=page_num,
                                    region="chart",
                                )
                            )
                    if not used:
                        x0, y0, x1, y1 = max(drawing_regions, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
                        elements.append(
                            VisualElement(
                                type="image",
                                content="",
                                bbox=[x0, y0, x1, y1],
                                page_num=page_num,
                                region="chart",
                            )
                        )
            for block in blocks:
                if block.get("type") == 1:
                    image_bbox = _bbox_from_rect(block.get("bbox", (0, 0, 0, 0)))
                    elements.append(
                        VisualElement(
                            type="image",
                            content="",
                            bbox=image_bbox,
                            page_num=page_num,
                            region="chart",
                        )
                    )
        return {
            "elements": elements,
            "parse_errors": parse_errors,
            "parse_report": parse_report.model_dump(),
        }

    def _identify_zones(self, page_obj):
        return None

    def _extract_elements(self, zone_info) -> List[VisualElement]:
        return []


class VisualValidator:
    def __init__(self):
        self.rules = {}

    def update_rules(self, rules: Dict[str, Any]):
        self.rules = rules

    async def validate(self, elements: List[VisualElement]) -> Dict[str, Any]:
        return await asyncio.to_thread(self._validate_sync, elements)

    def _validate_sync(self, elements: List[VisualElement]) -> Dict[str, Any]:
        issues = []
        issues.extend(self._check_charts(elements))
        issues.extend(self._check_formulas(elements))
        issues.extend(self._check_titles(elements))
        issues.extend(self._check_citations(elements))
        return {"layout_issues": self._dedupe_issues(issues)}

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

    def _check_charts(self, elements: List[VisualElement]) -> List[Dict[str, Any]]:
        issues = []
        rule_config = self.rules.get("figure_table_check", {})
        fig_caption_pos = rule_config.get("caption_requirement", "bottom")
        table_caption_pos = rule_config.get("table_caption_requirement", "top")
        min_figure_area_ratio = float(rule_config.get("min_figure_area_ratio", 0.03))

        def _norm_label_num(value: str) -> str:
            s = (value or "").strip()
            if not s:
                return ""
            s = re.sub(r"\s+", "", s)
            for ch in ("−", "–", "—", "－"):
                s = s.replace(ch, "-")
            s = s.replace(".", "-")
            if s.endswith("."):
                s = s[:-1]
            return s

        caption_num_pat = re.compile(r"^\s*(?:图|表|Figure|Fig\.?|Table)\s*([0-9]+(?:\s*[-.−–—－]\s*[0-9]+)*)", flags=re.IGNORECASE)
        # Require a boundary before/after 图/表/Figure/Table to avoid matching inside words like “代表”
        ref_pat = re.compile(
            r"(?<![\u4e00-\u9fffA-Za-z0-9])(?:图|表|(?<![A-Za-z])(?:Figure|Fig\.?|Table)(?![A-Za-z]))\s*([0-9]+(?:\s*[-.−–—－]\s*[0-9]+)*)",
            flags=re.IGNORECASE,
        )
        figure_caption_pat = re.compile(r"^\s*(?:图|Figure|Fig\.?)\s*", flags=re.IGNORECASE)
        table_caption_pat = re.compile(r"^\s*(?:表|Table)\s*", flags=re.IGNORECASE)

        captions = [e for e in elements if (e.type == "chart") or (e.type == "title" and is_caption(e.content))]
        images = [e for e in elements if e.type == "image"]
        text_refs = [e for e in elements if e.type == "text" and (getattr(e, "region", "") or "") != "reference"]
        caption_nums = set()
        for c in captions:
            m = caption_num_pat.match(c.content or "")
            if m and m.group(1):
                norm = _norm_label_num(m.group(1))
                if norm:
                    caption_nums.add(norm)
        for t in text_refs:
            text_content = t.content or ""
            for m in ref_pat.finditer(text_content):
                raw_num = m.group(1) or ""
                end_pos = m.end(1)
                next_ch = text_content[end_pos:end_pos + 1]
                if next_ch and next_ch in {"-", ".", "−", "–", "—", "－"}:
                    continue
                if re.match(r"^\s*\d+\s*\.\s*\d+\s*\.\s*\d+", raw_num):
                    continue
                norm_num = re.sub(r"\s+", "", raw_num.strip())
                sep_count = sum(norm_num.count(ch) for ch in ("-", ".", "−", "–", "—", "－"))
                if sep_count >= 2:
                    continue
                ref_num = _norm_label_num(raw_num)
                if ref_num and ref_num not in caption_nums:
                    issues.append(
                        {
                            "issue_type": "Label_Missing",
                            "severity": "Info",
                            "page_num": t.page_num,
                            "bbox": t.bbox,
                            "evidence": t.content,
                            "message": "正文引用的图表编号未在图表标题中找到",
                            "location": {"page": t.page_num, "bbox": t.bbox}
                        }
                    )

        captions_by_page: Dict[int, List[VisualElement]] = {}
        for c in captions:
            captions_by_page.setdefault(int(getattr(c, "page_num", 0) or 0), []).append(c)
        has_any_caption = bool(captions)

        images_by_page: Dict[int, List[VisualElement]] = {}
        for img in images:
            images_by_page.setdefault(int(getattr(img, "page_num", 0) or 0), []).append(img)

        for c in captions:
            is_figure = bool(figure_caption_pat.match(c.content or ""))
            is_table = bool(table_caption_pat.match(c.content or ""))

            same_page_images = [i for i in images if i.page_num == c.page_num]

            if is_figure:
                if not same_page_images:
                    continue

                page_elements = [e for e in elements if e.page_num == c.page_num]
                max_y = max((e.bbox[3] for e in page_elements), default=842.0)
                max_x = max((e.bbox[2] for e in page_elements), default=595.0)
                c_mid_x = (c.bbox[0] + c.bbox[2]) / 2.0
                tol_y = 6.0

                if fig_caption_pos == "bottom":
                    candidates = [
                        i
                        for i in same_page_images
                        if (c.bbox[1] - i.bbox[3]) <= max_y * 0.33 and (c.bbox[1] - i.bbox[3]) >= -tol_y
                    ]
                else:
                    candidates = [
                        i
                        for i in same_page_images
                        if (i.bbox[1] - c.bbox[3]) <= max_y * 0.33 and (i.bbox[1] - c.bbox[3]) >= -tol_y
                    ]
                if not candidates:
                    continue
                gx0 = min(i.bbox[0] for i in candidates)
                gy0 = min(i.bbox[1] for i in candidates)
                gx1 = max(i.bbox[2] for i in candidates)
                gy1 = max(i.bbox[3] for i in candidates)
                margin = float(max_x) * 0.02
                if c_mid_x < gx0 - margin or c_mid_x > gx1 + margin:
                    continue

                if fig_caption_pos == "bottom" and c.bbox[1] < gy1 - 5.0:
                     issues.append({
                        "issue_type": "Label_Missing",
                        "severity": "Info",
                        "page_num": c.page_num,
                        "bbox": c.bbox,
                        "evidence": c.content,
                        "message": f"图标题应位于图下方 (规则要求: {fig_caption_pos})",
                        "location": {"page": c.page_num, "bbox": c.bbox}
                    })
                elif fig_caption_pos == "top" and c.bbox[3] > gy0 + 5.0:
                     issues.append({
                        "issue_type": "Label_Missing",
                        "severity": "Info",
                        "page_num": c.page_num,
                        "bbox": c.bbox,
                        "evidence": c.content,
                        "message": f"图标题应位于图上方 (规则要求: {fig_caption_pos})",
                        "location": {"page": c.page_num, "bbox": c.bbox}
                    })

            if is_table:
                pass

        if not has_any_caption:
            return issues

        for page_num, page_images in images_by_page.items():
            page_elements = [e for e in elements if e.page_num == page_num]
            if any(getattr(e, "type", "") == "scanned_content" for e in page_elements):
                continue
            max_x = max((e.bbox[2] for e in page_elements if getattr(e, "bbox", None)), default=595.0)
            max_y = max((e.bbox[3] for e in page_elements if getattr(e, "bbox", None)), default=842.0)
            page_area = max(1.0, float(max_x) * float(max_y))
            page_captions = captions_by_page.get(page_num, [])
            figure_captions = [c for c in page_captions if figure_caption_pat.match(c.content or "")]
            table_captions = [c for c in page_captions if table_caption_pat.match(c.content or "")]
            caption_pool = figure_captions + table_captions
            covered = set()
            if figure_captions:
                for cap in figure_captions:
                    cap_mid_x = (cap.bbox[0] + cap.bbox[2]) / 2.0
                    candidates = [
                        i
                        for i in page_images
                        if (cap.bbox[1] - i.bbox[3]) <= max_y * 0.30 and (cap.bbox[1] - i.bbox[3]) >= -6.0
                    ]
                    if not candidates:
                        continue
                    gx0 = min(i.bbox[0] for i in candidates)
                    gx1 = max(i.bbox[2] for i in candidates)
                    if cap_mid_x < gx0 - float(max_x) * 0.03 or cap_mid_x > gx1 + float(max_x) * 0.03:
                        continue
                    for i in candidates:
                        try:
                            covered.add((round(float(i.bbox[0]), 1), round(float(i.bbox[1]), 1), round(float(i.bbox[2]), 1), round(float(i.bbox[3]), 1)))
                        except Exception:
                            continue

            def _truncate(s: str, n: int = 120) -> str:
                t = re.sub(r"\s+", " ", (s or "")).strip()
                if len(t) <= n:
                    return t
                return t[: max(0, n - 1)] + "…"

            def _nearby_text(img_el: VisualElement, prefer: str) -> str:
                candidates: List[Tuple[float, str]] = []
                for e in page_elements:
                    if getattr(e, "type", "") not in {"text", "title", "chart"}:
                        continue
                    if (getattr(e, "region", "") or "") == "reference":
                        continue
                    txt = (getattr(e, "content", "") or "").strip()
                    if not txt:
                        continue
                    if is_reference_title(txt):
                        continue
                    if re.search(r"[\.·…]{5,}\s*\d+\s*$", txt):
                        continue
                    overlap = min(e.bbox[2], img_el.bbox[2]) - max(e.bbox[0], img_el.bbox[0])
                    if overlap <= 0:
                        continue
                    min_w = min(max(1.0, e.bbox[2] - e.bbox[0]), max(1.0, img_el.bbox[2] - img_el.bbox[0]))
                    if overlap < min_w * 0.15:
                        continue
                    if prefer == "bottom":
                        dy = float(e.bbox[1]) - float(img_el.bbox[3])
                    else:
                        dy = float(img_el.bbox[1]) - float(e.bbox[3])
                    if dy < -3.0:
                        continue
                    if dy > float(max_y) * 0.18:
                        continue
                    candidates.append((dy, txt))
                candidates.sort(key=lambda x: x[0])
                picked = [_truncate(t) for _, t in candidates[:2]]
                return " | ".join([p for p in picked if p])

            def _implicit_caption(img_el: VisualElement) -> Optional[Tuple[VisualElement, str]]:
                candidates: List[Tuple[float, VisualElement, str]] = []
                for e in page_elements:
                    if getattr(e, "type", "") not in {"text", "title"}:
                        continue
                    if (getattr(e, "region", "") or "") == "reference":
                        continue
                    txt = (getattr(e, "content", "") or "").strip()
                    if not txt:
                        continue
                    if is_reference_title(txt):
                        continue
                    if re.search(r"[\.·…]{5,}\s*\d+\s*$", txt):
                        continue
                    if not is_caption(txt):
                        continue
                    overlap = min(e.bbox[2], img_el.bbox[2]) - max(e.bbox[0], img_el.bbox[0])
                    if overlap <= 0:
                        continue
                    min_w = min(max(1.0, e.bbox[2] - e.bbox[0]), max(1.0, img_el.bbox[2] - img_el.bbox[0]))
                    if overlap < min_w * 0.15:
                        continue
                    dy_below = float(e.bbox[1]) - float(img_el.bbox[3])
                    dy_above = float(img_el.bbox[1]) - float(e.bbox[3])
                    near_below = 0.0 <= dy_below <= float(max_y) * 0.18
                    near_above = 0.0 <= dy_above <= float(max_y) * 0.18
                    if not (near_below or near_above):
                        continue
                    if near_below:
                        candidates.append((dy_below, e, "bottom"))
                    else:
                        candidates.append((dy_above, e, "top"))
                if not candidates:
                    return None
                candidates.sort(key=lambda x: x[0])
                _, best_e, pos = candidates[0]
                return best_e, pos

            subcap_pat = re.compile(r"^\s*(?:图|Figure|Fig\.?)\s*[（(]?\s*[A-Za-z]\s*[)）]", flags=re.IGNORECASE)

            def _has_subfigure_caption(img_el: VisualElement) -> bool:
                for e in page_elements:
                    if getattr(e, "type", "") not in {"text", "title", "chart"}:
                        continue
                    if (getattr(e, "region", "") or "") == "reference":
                        continue
                    txt = (getattr(e, "content", "") or "").strip()
                    if not txt:
                        continue
                    if not subcap_pat.match(txt):
                        continue
                    overlap = min(e.bbox[2], img_el.bbox[2]) - max(e.bbox[0], img_el.bbox[0])
                    if overlap <= 0:
                        continue
                    min_w = min(max(1.0, e.bbox[2] - e.bbox[0]), max(1.0, img_el.bbox[2] - img_el.bbox[0]))
                    if overlap < min_w * 0.05:
                        continue
                    dy_below = float(e.bbox[1]) - float(img_el.bbox[3])
                    dy_above = float(img_el.bbox[1]) - float(e.bbox[3])
                    if (-6.0 <= dy_below <= float(max_y) * 0.18) or (-6.0 <= dy_above <= float(max_y) * 0.18):
                        return True
                return False

            for img in page_images:
                try:
                    key = (round(float(img.bbox[0]), 1), round(float(img.bbox[1]), 1), round(float(img.bbox[2]), 1), round(float(img.bbox[3]), 1))
                except Exception:
                    key = None
                iw = max(0.0, img.bbox[2] - img.bbox[0])
                ih = max(0.0, img.bbox[3] - img.bbox[1])
                img_area_ratio = (iw * ih) / page_area
                if img_area_ratio < min_figure_area_ratio:
                    continue
                # Skip if image covers almost the entire page (likely background or scanned page wrapper)
                if img_area_ratio > 0.8:
                    continue

                # Skip if image is extremely wide but short (likely header/footer or separator)
                # e.g. width > 80% of page but height < 5% of page
                if iw > float(max_x) * 0.8 and ih < float(max_y) * 0.05:
                    continue

                # Skip if image content (if available via OCR/extraction) is long text
                # Likely a text region misclassified as image
                img_content = (getattr(img, "content", "") or "").strip()

                # Check for sub-figure labels (e.g. "(a)", "a)", "(b)")
                if re.match(r"^(\(?[a-z]\)?|[a-z]\.)\s", img_content, re.IGNORECASE) or \
                   (len(img_content) < 10 and re.match(r"^(\(?[a-z]\)?|[a-z]\.)$", img_content, re.IGNORECASE)):
                    continue

                if len(img_content) > 50:
                    # Check if it looks like a figure label though
                    if not re.match(r"^(图|表|Figure|Table|Fig\.?)\s*\d", img_content, re.IGNORECASE):
                        continue

                # Check for overlapping text elements (misclassified text region)
                # try:
                #     if not (img.bbox and len(img.bbox) == 4):
                #         continue
                #     overlapping_text_len = 0
                #     for e in page_elements:
                #         if getattr(e, "type", "") == "text" and getattr(e, "bbox", None) and len(e.bbox) == 4:
                #             # Calculate intersection area
                #             ix0 = max(img.bbox[0], e.bbox[0])
                #             iy0 = max(img.bbox[1], e.bbox[1])
                #             ix1 = min(img.bbox[2], e.bbox[2])
                #             iy1 = min(img.bbox[3], e.bbox[3])
                #             if ix1 > ix0 and iy1 > iy0:
                #                 # If significant overlap (e.g. text is mostly inside image)
                #                 text_area = (e.bbox[2] - e.bbox[0]) * (e.bbox[3] - e.bbox[1])
                #                 if text_area > 0:
                #                     intersect_area = (ix1 - ix0) * (iy1 - iy0)
                #                     if intersect_area > text_area * 0.5:
                #                         overlapping_text_len += len((getattr(e, "content", "") or "").strip())
                #
                #     if overlapping_text_len > 50:
                #          continue
                # except Exception:
                #     pass

                if img.bbox[3] <= float(max_y) * 0.12:
                    continue
                if img.bbox[1] >= float(max_y) * 0.88:
                    continue
                if img_area_ratio >= 0.6:
                    x0, y0, x1, y1 = img.bbox
                    near_edges = (x0 <= float(max_x) * 0.05 and x1 >= float(max_x) * 0.95 and y0 <= float(max_y) * 0.10 and y1 >= float(max_y) * 0.90)
                    text_count = len([e for e in page_elements if getattr(e, "type", "") in {"text", "title", "citation", "chart", "formula"} and (getattr(e, "region", "") or "") != "reference"])
                    if near_edges and text_count >= 15:
                        continue
                if key is not None and key in covered:
                    continue
                if _has_subfigure_caption(img):
                    continue

                implicit = _implicit_caption(img)
                if implicit:
                    cap, pos = implicit
                    is_table_cap = bool(table_caption_pat.match(cap.content or ""))
                    expected_pos = table_caption_pos if is_table_cap else fig_caption_pos
                    noun = "表" if is_table_cap else "图"
                    if expected_pos == "bottom" and pos != "bottom":
                        issues.append({
                            "issue_type": "Label_Missing",
                            "severity": "Info",
                            "page_num": cap.page_num,
                            "bbox": cap.bbox,
                            "evidence": cap.content,
                            "message": f"{noun}标题应位于{noun}下方 (规则要求: {expected_pos})",
                            "location": {"page": cap.page_num, "bbox": cap.bbox},
                        })
                        continue
                    if expected_pos == "top" and pos != "top":
                        issues.append({
                            "issue_type": "Label_Missing",
                            "severity": "Info",
                            "page_num": cap.page_num,
                            "bbox": cap.bbox,
                            "evidence": cap.content,
                            "message": f"{noun}标题应位于{noun}上方 (规则要求: {expected_pos})",
                            "location": {"page": cap.page_num, "bbox": cap.bbox},
                        })
                        continue
                    continue
                if not caption_pool:
                    ev = _nearby_text(img, "bottom") or _nearby_text(img, "top")
                    issues.append(
                        {
                            "issue_type": "Label_Missing",
                            "severity": "Info",
                            "page_num": img.page_num,
                            "bbox": img.bbox,
                            "evidence": ev,
                            "message": "检测到图片但未找到图标题",
                            "location": {"page": img.page_num, "bbox": img.bbox},
                        }
                    )
                    continue

                best = None
                best_score = None
                for c in caption_pool:
                    overlap = min(c.bbox[2], img.bbox[2]) - max(c.bbox[0], img.bbox[0])
                    if overlap <= 0:
                        c_mid = (c.bbox[0] + c.bbox[2]) / 2.0
                        if c_mid < img.bbox[0] - float(max_x) * 0.03 or c_mid > img.bbox[2] + float(max_x) * 0.03:
                            continue
                    min_w = min(max(1.0, c.bbox[2] - c.bbox[0]), max(1.0, img.bbox[2] - img.bbox[0]))
                    if overlap < min_w * 0.2:
                        continue
                    dy = abs(c.bbox[1] - img.bbox[1])
                    if dy > max_y * 0.25:
                        continue
                    score = dy * 1000.0 + abs((c.bbox[0] + c.bbox[2]) / 2.0 - (img.bbox[0] + img.bbox[2]) / 2.0)
                    if best_score is None or score < best_score:
                        best_score = score
                        best = c
                if best is None:
                    ev = _nearby_text(img, "bottom") or _nearby_text(img, "top")
                    issues.append(
                        {
                            "issue_type": "Label_Missing",
                            "severity": "Info",
                            "page_num": img.page_num,
                            "bbox": img.bbox,
                            "evidence": ev,
                            "message": "检测到图片但未找到图标题",
                            "location": {"page": img.page_num, "bbox": img.bbox},
                        }
                    )
        return issues

    def _check_formulas(self, elements: List[VisualElement]) -> List[Dict[str, Any]]:
        issues = []
        rule_config = self.rules.get("formula_check", {})
        numbering_pos = rule_config.get("numbering", "right")
        require_numbering = bool(rule_config.get("require_numbering", False))
        check_reference = bool(rule_config.get("check_reference", False))
        unref_ratio_threshold = float(rule_config.get("unreferenced_ratio_threshold", 0.0) or 0.0)
        min_unref_count = int(rule_config.get("min_unreferenced_count", 0) or 0)
        missing_severity = rule_config.get("missing_severity", "Warning")
        num_pat = r"\d+(?:\s*[-.−–—－]\s*\d+)*"
        dash_chars = "−–—－"

        def _norm_num(n: str) -> str:
            if not n:
                return ""
            s = re.sub(r"\s+", "", str(n))
            for ch in dash_chars:
                s = s.replace(ch, "-")
            if re.fullmatch(r"\d+(?:-\d+)+", s):
                s = s.replace("-", ".")
            return s

        def _is_display_formula(text: str) -> bool:
            s = (text or "").strip()
            if not s:
                return False
            if re.search(rf"(（|\()({num_pat})(）|\))\s*$", s):
                return True
            if ":=" in s:
                return False
            if re.search(r"[；;。;]\s*$", s):
                return False
            if re.search(r"[\(\[（【\{⟨<]\s*$", s):
                return False
            if re.match(r"^\s*\d+\s*:\s*\S", s):
                return False
            if re.match(r"^\s*\d+\)\s+\S", s):
                return False
            if re.match(r"^\s*\d+\s*且\s+\S", s):
                return False
            if re.match(r"^[^\d\s\(\)【】\[\]\{\}<>]{1,2}\s*[:：]\s*\S", s):
                return False
            if re.search(r"[∧∨¬⇒⇔]", s):
                return False
            if re.search(r"(其中|定义|我们|令|则|即|表示)", s):
                return False
            if re.fullmatch(r"!\([^)]*\)", s):
                return False
            if re.fullmatch(r"\[\s*!\([^)]*\)\s*\]", s):
                return False
            if "⟨" in s and "⟩" not in s:
                return False
            if "⟩" in s and "⟨" not in s:
                return False
            if "{" in s and "}" not in s:
                return False
            if "}" in s and "{" not in s:
                return False
            if re.search(r"⟨[^⟩]+⟩", s) and "," in s:
                return False
            if "←" in s:
                return False
            if re.match(r"^\s*算法\s*\d", s):
                return False
            if "。" in s:
                return False
            if "˙" in s:
                return False
            # Exclude likely code patterns
            if re.search(r"(\[\]|\{\}|return\s|def\s|class\s|import\s|print\()", s):
                return False
            if re.search(r"[=<>≤≥±×÷*/+\-≈≠]\s*$", s):
                return False
            cn = len(re.findall(r"[\u4e00-\u9fff]", s))
            if cn / max(len(s), 1) > 0.15:
                return False
            if "," in s and s.count("=") >= 2 and not re.search(r"[∧∨¬⇒⇔∑∫√]", s):
                parts = [p.strip() for p in re.split(r"[,，]", s) if p.strip()]
                if len(parts) >= 2 and all("=" in p for p in parts):
                    return False
            if re.fullmatch(r"\S+\s*(?:≤|≥|<|>)\s*\S+(?:\s*[+\-−–—]\s*\d+(?:\.\d+)?)?", s):
                return False
            if re.fullmatch(r"[A-Za-zα-ωΑ-Ω]\w{0,3}\s*[≤≥<>]=?\s*-?\d+(?:\.\d+)?", s):
                return False
            if re.fullmatch(r"[A-Za-zα-ωΑ-Ω]\w{0,2}", s):
                return False
            if re.fullmatch(r"[∑∫√α-ωΑ-Ω∂∇∞≈≠≤≥±×÷]", s):
                return False
            if len(s) <= 6:
                return False
            # Exclude scientific notation like "5 × 102" or "1.28 × 106"
            if re.fullmatch(r"[\d\.]+\s*[×xX*]\s*10\s*[-−]?\d+", s):
                return False
            # Exclude simple assignment or property access like "T=0.2" or "H=0.85"
            if re.fullmatch(r"[A-Za-z]+\s*=\s*[\d\.]+", s):
                return False
            # Exclude simple numbers with units or simple arithmetic
            if re.fullmatch(r"[\d\.]+\s*[+\-−–—]\s*[\d\.]+", s):
                return False
            # Exclude trailing assignments without complex formula features (e.g. "布，H=0.85")
            if re.search(r"[\u4e00-\u9fff]\s*[,，]?\s*[A-Za-z]+\s*=\s*[\d\.]+$", s):
                return False
            # Exclude Chinese text that might end up with weird math-like symbols due to OCR or parsing
            if "，" in s or "。" in s or "、" in s or "：" in s:
                return False

            if re.search(r"[=<>≤≥±×÷*/+\-≈≠]", s):
                return True
            if re.search(r"[∑∫√∂∇∞]", s):
                return True
            if re.search(r"(\\[a-zA-Z]+|\^|\{.*\})", s):
                return True
            return False

        formulas = [e for e in elements if e.type == "formula" and _is_display_formula(e.content)]
        if not formulas:
            return issues
        page_max_x = {}
        page_max_y = {}
        for e in elements:
            page_max_x[e.page_num] = max(page_max_x.get(e.page_num, 0), e.bbox[2])
            page_max_y[e.page_num] = max(page_max_y.get(e.page_num, 0), e.bbox[3])
        text_refs = [e for e in elements if e.type == "text"]
        ref_nums = set()
        for t in text_refs:
            for m in re.finditer(rf"(?:式|公式)\s*(?:（|\()?\s*({num_pat})\s*(?:）|\))?", t.content):
                ref_nums.add(_norm_num(m.group(1)))
            for m in re.finditer(rf"(?:Eq\.?|Equation)\s*(?:\(|（)?\s*({num_pat})\s*(?:\)|）)?", t.content, flags=re.IGNORECASE):
                ref_nums.add(_norm_num(m.group(1)))
            for m in re.finditer(rf"(?:（|\()({num_pat})(?:）|\))", t.content):
                raw = m.group(1)
                if not re.search(r"[-.−–—－]", raw):
                    continue
                ref_nums.add(_norm_num(raw))
        num_only_pat = re.compile(rf"^\s*(?:（|\()({num_pat})(?:）|\))\s*$")
        page_num_only = {}
        for e in elements:
            if e.type not in {"text", "title", "formula"}:
                continue
            m = num_only_pat.match(e.content or "")
            if not m:
                continue
            page_num_only.setdefault(e.page_num, []).append((e, _norm_num(m.group(1))))
        total_numbered = 0
        for f in formulas:
            num = None
            num_bbox: Optional[List[float]] = None
            m_end = re.search(rf"(（|\()({num_pat})(）|\))\s*$", f.content)
            if m_end:
                num = _norm_num(m_end.group(2))
                num_bbox = f.bbox
            else:
                # Fallback: check if the number is at the beginning (left-aligned case) or just separated
                # Some PDFs might have "(1) formula" or "formula (1)"
                # But here we stick to the end for standard cases, but relax regex
                candidates = page_num_only.get(f.page_num, [])
                if candidates:
                    fcy = (f.bbox[1] + f.bbox[3]) / 2.0
                    fh = max(1.0, f.bbox[3] - f.bbox[1])
                    max_x = page_max_x.get(f.page_num, f.bbox[2])
                    best = None
                    best_bbox = None
                    best_score = None
                    for e, num in candidates:
                        ecy = (e.bbox[1] + e.bbox[3]) / 2.0
                        if abs(ecy - fcy) > max(3.5, fh * 0.8):
                            continue
                        if (e.bbox[2] - e.bbox[0]) > 120:
                            continue
                        if numbering_pos == "right":
                            if e.bbox[0] < f.bbox[2] - 4:
                                continue
                            max_y = page_max_y.get(f.page_num, 842.0) or 842.0
                            if e.bbox[1] <= max_y * 0.08 or e.bbox[3] >= max_y * 0.96:
                                continue
                            if e.bbox[2] < max_x * 0.85:
                                continue
                        elif numbering_pos == "left":
                            if e.bbox[2] > f.bbox[0] + 4:
                                continue
                        score = abs(ecy - fcy) * 1000.0 + abs(e.bbox[0] - f.bbox[2])
                        if best_score is None or score < best_score:
                            best_score = score
                            best = num
                            best_bbox = e.bbox
                    if best is not None:
                        num = _norm_num(best)
                        num_bbox = best_bbox

            if not num:
                if require_numbering:
                    issues.append(
                        {
                            "issue_type": "Formula_Missing",
                            "severity": missing_severity,
                            "page_num": f.page_num,
                            "bbox": f.bbox,
                            "evidence": f.content,
                            "message": "公式未检测到编号",
                            "location": {"page": f.page_num, "bbox": f.bbox}
                        }
                    )
                continue
            total_numbered += 1
            if check_reference and num not in ref_nums:
                issues.append(
                    {
                        "issue_type": "Formula_Ref_Missing",
                        "severity": "Info",
                        "page_num": f.page_num,
                        "bbox": f.bbox,
                        "evidence": f.content,
                        "message": "公式编号未在正文引用中出现",
                        "location": {"page": f.page_num, "bbox": f.bbox},
                        "_internal_formula_ref_missing": True,
                    }
                )
            max_x = page_max_x.get(f.page_num, f.bbox[2])

            # Dynamic Rule Check: Formula Numbering
            if numbering_pos == "right":
                check_bbox = num_bbox or f.bbox
                if check_bbox[2] < max_x * 0.6:
                    issues.append(
                        {
                            "issue_type": "Formula_Misaligned",
                            "severity": "Warning",
                            "page_num": f.page_num,
                            "bbox": f.bbox,
                            "evidence": f.content,
                            "message": "公式编号疑似未右对齐",
                            "location": {"page": f.page_num, "bbox": f.bbox}
                        }
                    )
            elif numbering_pos == "left":
                # Assuming left margin is near 0
                 check_bbox = num_bbox or f.bbox
                 if check_bbox[0] > max_x * 0.15: # Simple heuristic
                    issues.append(
                        {
                            "issue_type": "Formula_Misaligned",
                            "severity": "Warning",
                            "page_num": f.page_num,
                            "bbox": f.bbox,
                            "evidence": f.content,
                            "message": "公式编号疑似未左对齐",
                            "location": {"page": f.page_num, "bbox": f.bbox}
                        }
                    )
        if check_reference and total_numbered > 0:
            unref_count = sum(1 for i in issues if i.get("_internal_formula_ref_missing"))
            ratio = unref_count / float(total_numbered)
            should_emit = True
            if min_unref_count > 0 and unref_count < min_unref_count:
                should_emit = False
            if unref_ratio_threshold > 0.0 and ratio < unref_ratio_threshold:
                should_emit = False
            if not should_emit:
                issues = [i for i in issues if not i.get("_internal_formula_ref_missing")]
            else:
                for i in issues:
                    if i.get("_internal_formula_ref_missing"):
                        i.pop("_internal_formula_ref_missing", None)
        return issues

    def _check_titles(self, elements: List[VisualElement]) -> List[Dict[str, Any]]:
        issues = []
        rule_config = self.rules.get("heading_check", {})
        max_depth = rule_config.get("max_depth", 4)
        continuity_check = bool(rule_config.get("continuity_check", False))
        continuity_severity = rule_config.get("continuity_severity", "Info")
        column_threshold = float(rule_config.get("column_threshold", 200.0))

        page_max_y: Dict[int, float] = {}
        for e in elements:
            if not getattr(e, "bbox", None):
                continue
            page_max_y[e.page_num] = max(page_max_y.get(e.page_num, 0.0), float(e.bbox[3]))

        heading_candidate_pos: Dict[str, Tuple[int, float, float]] = {}
        for e in elements:
            if not getattr(e, "bbox", None):
                continue
            max_y = page_max_y.get(e.page_num, 842.0) or 842.0
            if e.bbox[1] <= max_y * 0.08 or e.bbox[3] >= max_y * 0.95:
                continue
            content = (getattr(e, "content", "") or "").strip()
            if not content:
                continue
            if not is_heading_text(content):
                continue
            if _is_toc_like_heading(content):
                continue
            parts = _parse_heading_parts(content)
            if not parts:
                continue
            key = ".".join(str(x) for x in parts)
            pos = (int(getattr(e, "page_num", 0) or 0), float(e.bbox[1]), float(e.bbox[0]))
            if key not in heading_candidate_pos or pos < heading_candidate_pos[key]:
                heading_candidate_pos[key] = pos

        titles = [e for e in elements if e.type == "title" and e.region == "title"]
        title_text_counts: Dict[str, int] = {}
        for t in titles:
            key = re.sub(r"\s+", "", (t.content or "")).strip()
            if not key:
                continue
            title_text_counts[key] = title_text_counts.get(key, 0) + 1
        chapter_only_pat = re.compile(r"^第[0-9一二三四五六七八九十百千两零]+章$")
        titles = sorted(
            titles,
            key=lambda e: (
                int(getattr(e, "page_num", 0) or 0),
                float((getattr(e, "bbox", None) or [0.0, 0.0, 0.0, 0.0])[1]),
                float((getattr(e, "bbox", None) or [0.0, 0.0, 0.0, 0.0])[0]),
            ),
        )
        numbered = []
        for t in titles:
            max_y = page_max_y.get(t.page_num, 842.0) or 842.0
            if t.bbox[1] <= max_y * 0.08 or t.bbox[3] >= max_y * 0.95:
                continue
            compact = re.sub(r"\s+", "", (t.content or "")).strip()
            if compact and title_text_counts.get(compact, 0) >= 3 and chapter_only_pat.match(compact):
                continue
            if not is_heading_text(t.content):
                continue
            if _is_toc_like_heading(t.content):
                continue
            parts = _parse_heading_parts(t.content)
            if not parts:
                continue

            # Dynamic Rule Check: Max Depth
            if len(parts) > max_depth:
                 issues.append(
                    {
                        "issue_type": "Hierarchy_Fault",
                        "severity": "Warning",
                        "page_num": t.page_num,
                        "bbox": t.bbox,
                        "evidence": t.content,
                        "message": f"标题层级过深 (最大允许: {max_depth})",
                        "location": {"page": t.page_num, "bbox": t.bbox}
                    }
                )

            numbered.append((t, parts))
        seen_numbers: Dict[Tuple[int, ...], VisualElement] = {}
        last_child_by_parent: Dict[Tuple[int, ...], int] = {}
        prev_heading: Optional[Tuple[VisualElement, List[int]]] = None

        for curr, curr_parts in numbered:
            curr_key = tuple(curr_parts)
            if curr_key in seen_numbers:
                first = seen_numbers[curr_key]
                issues.append(
                    {
                        "issue_type": "Hierarchy_Fault",
                        "severity": continuity_severity,
                        "page_num": curr.page_num,
                        "bbox": curr.bbox,
                        "evidence": curr.content,
                        "message": f"标题序号重复: {'.'.join(str(x) for x in curr_parts)} (首次出现: 第{first.page_num}页)",
                        "location": {"page": curr.page_num, "bbox": curr.bbox},
                    }
                )
            else:
                seen_numbers[curr_key] = curr

            if continuity_check:
                if prev_heading:
                    prev, prev_parts = prev_heading
                    same_page = curr.page_num == prev.page_num
                    prev_cx = (prev.bbox[0] + prev.bbox[2]) / 2.0
                    curr_cx = (curr.bbox[0] + curr.bbox[2]) / 2.0
                    same_column = abs(curr_cx - prev_cx) < column_threshold
                    if same_page and same_column:
                        if len(curr_parts) > len(prev_parts) + 1 and curr_parts[: len(prev_parts)] == prev_parts:
                            issues.append(
                                {
                                    "issue_type": "Hierarchy_Fault",
                                    "severity": continuity_severity,
                                    "page_num": curr.page_num,
                                    "bbox": curr.bbox,
                                    "evidence": curr.content,
                                    "message": "标题层级跳跃",
                                    "location": {"page": curr.page_num, "bbox": curr.bbox},
                                }
                            )

                parent = tuple(curr_parts[:-1])
                child = curr_parts[-1]
                last_child = last_child_by_parent.get(parent, 0)
                expected = last_child + 1
                if child > expected:
                    prefix = ".".join(str(x) for x in parent)
                    expected_str = f"{prefix + '.' if prefix else ''}{expected}"
                    curr_pos = (int(getattr(curr, "page_num", 0) or 0), float(curr.bbox[1]), float(curr.bbox[0]))
                    exp_pos = heading_candidate_pos.get(expected_str)
                    if exp_pos and exp_pos < curr_pos:
                        last_child_by_parent[parent] = max(last_child, child)
                        prev_heading = (curr, curr_parts)
                        continue
                    issues.append(
                        {
                            "issue_type": "Hierarchy_Fault",
                            "severity": continuity_severity,
                            "page_num": curr.page_num,
                            "bbox": curr.bbox,
                            "evidence": curr.content,
                            "message": f"标题序号不连续 (期望: {expected_str})",
                            "location": {"page": curr.page_num, "bbox": curr.bbox},
                        }
                    )
                last_child_by_parent[parent] = max(last_child, child)

            prev_heading = (curr, curr_parts)
        return issues

    def _check_citations(self, elements: List[VisualElement]) -> List[Dict[str, Any]]:
        citations = [e for e in elements if e.type == "citation"]
        references = [e for e in elements if e.region == "reference"]
        issues = check_citation_reference_match(citations, references)
        return [issue.model_dump() for issue in issues]


class LayoutAnalyzer:
    def __init__(self):
        self.parser = PDFParser()
        self.validator = VisualValidator()

    def update_rules(self, rules: Dict[str, Any]):
        self.validator.update_rules(rules)

    async def analyze(self, content: Any) -> Dict[str, Any]:
        parse_result = await self.parser.parse(content)
        elements = parse_result.get("elements", [])
        validation_result = await self.validator.validate(elements)
        validation_result["layout_issues"] = with_anchor(validation_result.get("layout_issues", []))
        return {
            "elements": elements,
            "layout_result": validation_result,
            "parse_errors": parse_result.get("parse_errors", []),
            "parse_report": parse_result.get("parse_report", {}),
        }
