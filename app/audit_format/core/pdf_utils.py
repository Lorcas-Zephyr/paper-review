from typing import Any, Dict, List, Tuple
import base64
import os
import fitz
import numpy as np


def open_pdf(content: Any) -> fitz.Document:
    if isinstance(content, (bytes, bytearray)):
        return fitz.open(stream=content, filetype="pdf")
    if isinstance(content, str):
        if os.path.exists(content):
            return fitz.open(content)
        try:
            decoded = base64.b64decode(content, validate=True)
            if b"%PDF" in decoded[:1024]:
                return fitz.open(stream=decoded, filetype="pdf")
        except Exception:
            pass
    raise ValueError("invalid pdf content")


def is_encrypted(doc: fitz.Document) -> bool:
    return bool(getattr(doc, "is_encrypted", False))


def is_scanned_page(page: fitz.Page) -> bool:
    text = page.get_text("text").strip()
    return len(text) < 10


def page_to_image(page: fitz.Page, zoom: float = 2.0) -> np.ndarray:
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    return img


def _to_rect(value: Any) -> fitz.Rect | None:
    try:
        r = fitz.Rect(value)
        if r.is_empty:
            return None
        return r
    except Exception:
        return None


def _touch_or_intersect(a: fitz.Rect, b: fitz.Rect, margin: float) -> bool:
    if a.intersects(b):
        return True
    ax0, ay0, ax1, ay1 = a.x0 - margin, a.y0 - margin, a.x1 + margin, a.y1 + margin
    bx0, by0, bx1, by1 = b.x0, b.y0, b.x1, b.y1
    if ax0 <= bx1 and ax1 >= bx0 and ay0 <= by1 and ay1 >= by0:
        return True
    bx0, by0, bx1, by1 = b.x0 - margin, b.y0 - margin, b.x1 + margin, b.y1 + margin
    ax0, ay0, ax1, ay1 = a.x0, a.y0, a.x1, a.y1
    return bx0 <= ax1 and bx1 >= ax0 and by0 <= ay1 and by1 >= ay0


def extract_drawing_regions(
    page: fitz.Page,
    min_area_ratio: float = 0.02,
    max_area_ratio: float = 0.9,
    merge_margin: float = 4.0,
) -> List[Tuple[float, float, float, float]]:
    page_rect = page.rect
    page_area = max(1.0, float(page_rect.width) * float(page_rect.height))
    try:
        drawings = page.get_drawings() or []
    except Exception:
        return []

    rects: List[fitz.Rect] = []
    for d in drawings:
        r = d.get("rect") or d.get("bbox")
        rr = _to_rect(r)
        if rr is None:
            continue
        rr = rr & page_rect
        if rr.is_empty:
            continue
        area_ratio = (float(rr.width) * float(rr.height)) / page_area
        if area_ratio < 0.0008:
            continue
        rects.append(rr)

    if not rects:
        return []

    rects.sort(key=lambda r: float(r.width) * float(r.height), reverse=True)

    clusters: List[fitz.Rect] = []
    for r in rects:
        merged = False
        for idx, c in enumerate(clusters):
            if _touch_or_intersect(c, r, merge_margin):
                clusters[idx] = c | r
                merged = True
                break
        if not merged:
            clusters.append(r)

    changed = True
    while changed and len(clusters) > 1:
        changed = False
        new_clusters: List[fitz.Rect] = []
        for r in clusters:
            merged = False
            for idx, c in enumerate(new_clusters):
                if _touch_or_intersect(c, r, merge_margin):
                    new_clusters[idx] = c | r
                    merged = True
                    changed = True
                    break
            if not merged:
                new_clusters.append(r)
        clusters = new_clusters

    out: List[Tuple[float, float, float, float]] = []
    pw, ph = float(page_rect.width), float(page_rect.height)
    for c in clusters:
        c = c & page_rect
        if c.is_empty:
            continue
        w, h = float(c.width), float(c.height)
        if w <= 1.0 or h <= 1.0:
            continue
        area_ratio = (w * h) / page_area
        if area_ratio < float(min_area_ratio) or area_ratio > float(max_area_ratio):
            continue
        slender = min(w, h) / max(w, h) < 0.06
        if slender and (w / max(pw, 1.0) > 0.8 or h / max(ph, 1.0) > 0.8):
            continue
        out.append((float(c.x0), float(c.y0), float(c.x1), float(c.y1)))
    return out


def extract_blocks(page: fitz.Page) -> List[Dict[str, Any]]:
    try:
        return page.get_text("dict").get("blocks", [])
    except Exception:
        blocks = page.get_text("blocks")
        output: List[Dict[str, Any]] = []
        for b in blocks or []:
            if not b or len(b) < 5:
                continue
            x0, y0, x1, y1 = b[0], b[1], b[2], b[3]
            text = b[4] if len(b) > 4 else ""
            block_type = b[6] if len(b) > 6 else 0
            if block_type == 0:
                lines = []
                for line_text in str(text or "").splitlines():
                    lt = line_text.strip()
                    if not lt:
                        continue
                    lines.append(
                        {
                            "bbox": (x0, y0, x1, y1),
                            "spans": [{"text": lt, "size": 0}],
                        }
                    )
                output.append({"type": 0, "bbox": (x0, y0, x1, y1), "lines": lines})
            else:
                output.append({"type": 1, "bbox": (x0, y0, x1, y1)})
        return output


def split_columns(blocks: List[Dict[str, Any]], page_width: float) -> List[List[Dict[str, Any]]]:
    text_blocks = [b for b in blocks if b.get("type") == 0]
    if not text_blocks:
        return []
    xs = [b.get("bbox", [0, 0, 0, 0])[0] for b in text_blocks]
    if not xs:
        return [text_blocks]
    mid = page_width * 0.5
    left = [b for b in text_blocks if b.get("bbox", [0, 0, 0, 0])[0] < mid]
    right = [b for b in text_blocks if b.get("bbox", [0, 0, 0, 0])[0] >= mid]
    if not left or not right:
        return [text_blocks]
    return [left, right]


def normalize_bbox(bbox: List[float], page_width: float, page_height: float) -> List[float]:
    x0, y0, x1, y1 = bbox
    return [x0 / page_width, y0 / page_height, x1 / page_width, y1 / page_height]
