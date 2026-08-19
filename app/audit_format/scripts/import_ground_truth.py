import argparse
import asyncio
import difflib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from uuid import UUID

from sqlalchemy import select

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

from core.database import GroundTruthIssue, db_manager  # noqa: E402


def _normalize_text(v: Any) -> str:
    return str(v or "").strip()


def _parse_uuid(v: Any) -> UUID | None:
    if isinstance(v, UUID):
        return v
    s = _normalize_text(v)
    if not s:
        return None
    try:
        return UUID(s)
    except Exception:
        return None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _norm_match_text(v: Any) -> str:
    s = _normalize_text(v)
    if not s:
        return ""
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[，,。\.；;：:、!！\?？\(\)（）\[\]【】“”\"'‘’]", "", s)
    return s.lower()


def _extract_numbered_issues(md_text: str, limit: int) -> List[str]:
    lines = (md_text or "").splitlines()
    out: List[str] = []
    start = 0
    for idx, raw in enumerate(lines):
        if not str(raw or "").lstrip().startswith("#"):
            continue
        s = _normalize_text(raw)
        if "质询问题" in s:
            start = idx + 1
            break
    if start == 0:
        for idx, raw in enumerate(lines):
            if not str(raw or "").lstrip().startswith("#"):
                continue
            s = _normalize_text(raw)
            if "论文存在的主要问题" in s or ("主要问题" in s and "修改意见" in s):
                start = idx + 1
                break

    pat = re.compile(r"^\s*(\d{1,2})\s*[\.、\)）]\s*(.+?)\s*$")
    i = start
    while i < len(lines) and len(out) < limit:
        line = lines[i].strip()
        i += 1
        if not line:
            continue
        if (line.startswith("#") or line.lower().startswith("<table")) and out:
            break
        m = pat.match(line)
        if not m:
            continue
        parts = [m.group(2).strip()]
        while i < len(lines):
            nxt = lines[i].strip()
            if not nxt:
                i += 1
                break
            if nxt.startswith("#") or nxt.lower().startswith("<table"):
                break
            if pat.match(nxt):
                break
            if len(nxt) <= 200:
                parts.append(nxt)
            i += 1
        combined = " ".join([p for p in parts if p]).strip()
        if combined:
            out.append(combined)
    return out


def _load_spans_from_middle_json(middle_json_path: Path) -> List[Dict[str, Any]]:
    if not middle_json_path.exists():
        return []
    try:
        data = json.loads(middle_json_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    pdf_info = data.get("pdf_info", []) if isinstance(data, dict) else []
    spans: List[Dict[str, Any]] = []
    for page in pdf_info or []:
        if not isinstance(page, dict):
            continue
        page_idx = page.get("page_idx")
        page_num = None
        try:
            page_num = int(page_idx) + 1
        except Exception:
            page_num = None
        for pb in (page.get("para_blocks", []) or []):
            if not isinstance(pb, dict):
                continue
            blocks = pb.get("blocks", None)
            candidate_blocks: List[Dict[str, Any]] = []
            if isinstance(blocks, list) and blocks:
                candidate_blocks.extend([b for b in blocks if isinstance(b, dict)])
            else:
                candidate_blocks.append(pb)
            for block in candidate_blocks:
                if not isinstance(block, dict):
                    continue
                for line in (block.get("lines", []) or []):
                    if not isinstance(line, dict):
                        continue
                    line_parts: List[str] = []
                    line_bboxes: List[List[float]] = []
                    for span in (line.get("spans", []) or []):
                        if not isinstance(span, dict):
                            continue
                        content = span.get("content")
                        bbox = span.get("bbox")
                        if not content or not isinstance(content, str):
                            continue
                        if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
                            bbox = None
                        else:
                            try:
                                line_bboxes.append([float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])])
                            except Exception:
                                pass
                        line_parts.append(content)
                        spans.append({"content": content, "bbox": bbox, "page_num": page_num})
                    if len(line_parts) > 1:
                        agg = "".join([p for p in line_parts if p]).strip()
                        if agg:
                            agg_bbox = None
                            if line_bboxes:
                                xs0 = [b[0] for b in line_bboxes]
                                ys0 = [b[1] for b in line_bboxes]
                                xs1 = [b[2] for b in line_bboxes]
                                ys1 = [b[3] for b in line_bboxes]
                                agg_bbox = [min(xs0), min(ys0), max(xs1), max(ys1)]
                            spans.append({"content": agg, "bbox": agg_bbox, "page_num": page_num})
    return spans


def _match_issue_to_span(issue_text: str, spans: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    issue_norm = _norm_match_text(issue_text)
    if not issue_norm or not spans:
        return None
    best: Dict[str, Any] | None = None
    best_score = 0.0
    best_len = 0
    for s in spans:
        content = s.get("content")
        if not content:
            continue
        content_norm = _norm_match_text(content)
        if not content_norm:
            continue
        score = 0.0
        if issue_norm in content_norm:
            score = 1.0
        elif content_norm in issue_norm:
            if len(content_norm) < max(6, int(len(issue_norm) * 0.2)):
                score = 0.0
            else:
                score = min(0.95, float(len(content_norm)) / float(max(1, len(issue_norm))))
        else:
            score = difflib.SequenceMatcher(None, issue_norm, content_norm).ratio()
        c_len = len(content_norm)
        if score > best_score or (score == best_score and c_len > best_len):
            best_score = score
            best_len = c_len
            best = s
            if best_score >= 0.98:
                break
    if best and best_score >= 0.6:
        return best
    return None


def _guess_issue_type(text: str) -> str:
    t = _normalize_text(text)
    if not t:
        return "Other"
    if "错别字" in t or "书写错误" in t or "别字" in t or "笔误" in t or "语病" in t:
        return "Typo_Error"
    if ("实验" in t or "结果" in t or "数据" in t) and (
        "疑问" in t or "原因" in t or "过拟合" in t or "为什么" in t or "是否" in t or "?" in t or "？" in t
    ):
        return "Experiment_Result_Question"
    if "标题" in t and ("编号" in t or "连续" in t or "层级" in t):
        return "Hierarchy_Fault"
    if ("图" in t or "表" in t) and ("标题" in t or "题注" in t or "标注" in t) and ("缺" in t or "无" in t):
        return "Label_Missing"
    if "参考文献" in t and ("格式" in t or "排版" in t):
        return "Citation_Style_Inconsistent"
    if ("参考文献" in t or "引用" in t or "标注" in t or "【" in t or "[" in t) and ("缺" in t or "未" in t or "找不到" in t):
        return "Citation_Reference_Missing"
    if "参考文献" in t or "引用" in t or "标注" in t or "【" in t or "[" in t:
        return "Citation_Inconsistency"
    if "公式" in t and ("不可读" in t or "模糊" in t or "看不清" in t):
        return "Formula_Readability"
    if "标点" in t and ("混用" in t or "不一致" in t):
        return "Punctuation_Mixed"
    if (
        "格式" in t
        or "排版" in t
        or "字体" in t
        or "字号" in t
        or "行距" in t
        or "页眉" in t
        or "页脚" in t
        or "对齐" in t
        or "缩进" in t
        or "空格" in t
        or "标点" in t
    ):
        return "Formatting_Issue"
    return "Other"


def _guess_severity(text: str) -> str | None:
    t = _normalize_text(text)
    if not t:
        return None
    critical_kw = ["无法", "严重", "不可用", "致命"]
    warning_kw = ["错误", "缺失", "缺少", "不一致", "不可读", "模糊", "问题", "质询", "错别字", "书写错误", "语病"]
    if any(k in t for k in critical_kw):
        return "Critical"
    if any(k in t for k in warning_kw):
        return "Warning"
    return "Info"


def _extract_from_reviews(paper_root: Path, limit_per_review: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    mineru_root = paper_root / "papers-reviews-mineru10篇处理"
    if not mineru_root.exists():
        return out
    for sid_dir in sorted([p for p in mineru_root.iterdir() if p.is_dir()]):
        reviews_root = sid_dir / "reviews"
        if not reviews_root.exists():
            continue
        for review_dir in sorted([p for p in reviews_root.iterdir() if p.is_dir()]):
            hybrid_dir = review_dir / "hybrid_auto"
            if not hybrid_dir.exists():
                continue
            md_path = hybrid_dir / f"{review_dir.name}.md"
            if not md_path.exists():
                md_candidates = list(hybrid_dir.glob("*.md"))
                md_path = md_candidates[0] if md_candidates else md_path
            middle_json_path = hybrid_dir / f"{review_dir.name}_middle.json"
            if not middle_json_path.exists():
                middle_candidates = list(hybrid_dir.glob("*_middle.json"))
                middle_json_path = middle_candidates[0] if middle_candidates else middle_json_path
            if not md_path.exists():
                continue
            try:
                md_text = md_path.read_text(encoding="utf-8")
            except Exception:
                continue
            issues = _extract_numbered_issues(md_text, max(1, int(limit_per_review or 4)))
            spans = _load_spans_from_middle_json(middle_json_path)
            for idx, issue_text in enumerate(issues, start=1):
                matched = _match_issue_to_span(issue_text, spans)
                sample_id = f"papers-mineru10-{sid_dir.name}-{review_dir.name}"
                source_rel = None
                try:
                    source_rel = str(md_path.relative_to(_repo_root()))
                except Exception:
                    source_rel = str(md_path)
                item: Dict[str, Any] = {
                    "sample_id": sample_id,
                    "issue_type": _guess_issue_type(issue_text),
                    "severity": _guess_severity(issue_text),
                    "message": issue_text,
                    "evidence": (matched or {}).get("content") or issue_text,
                    "page_num": (matched or {}).get("page_num"),
                    "bbox": (matched or {}).get("bbox"),
                    "source": f"{source_rel}#issue-{idx}",
                }
                out.append(item)
    return out


async def import_items(items: List[Dict[str, Any]]) -> Dict[str, int]:
    now = datetime.utcnow()
    inserted = 0
    updated = 0
    skipped = 0

    try:
        async with db_manager.session() as session:
            for raw in items:
                if not isinstance(raw, dict):
                    skipped += 1
                    continue
                issue_type = _normalize_text(raw.get("issue_type"))
                if not issue_type:
                    skipped += 1
                    continue
                sample_id = _normalize_text(raw.get("sample_id")) or None
                paper_id = _parse_uuid(raw.get("paper_id"))
                chunk_id = _normalize_text(raw.get("chunk_id")) or None
                severity = _normalize_text(raw.get("severity")) or None
                message = _normalize_text(raw.get("message")) or None
                evidence = _normalize_text(raw.get("evidence")) or None
                page_num = raw.get("page_num")
                bbox = raw.get("bbox")
                source = _normalize_text(raw.get("source")) or None

                stmt = select(GroundTruthIssue).where(GroundTruthIssue.issue_type == issue_type).limit(1)
                if sample_id:
                    stmt = stmt.where(GroundTruthIssue.sample_id == sample_id)
                if paper_id:
                    stmt = stmt.where(GroundTruthIssue.paper_id == paper_id)
                if chunk_id:
                    stmt = stmt.where(GroundTruthIssue.chunk_id == chunk_id)
                if evidence:
                    stmt = stmt.where(GroundTruthIssue.evidence == evidence)
                if message:
                    stmt = stmt.where(GroundTruthIssue.message == message)
                existing = (await session.execute(stmt)).scalars().first()

                if existing:
                    existing.severity = severity or existing.severity
                    existing.page_num = page_num if page_num is not None else existing.page_num
                    existing.bbox = bbox if bbox is not None else existing.bbox
                    existing.source = source or existing.source
                    existing.updated_at = now
                    updated += 1
                else:
                    session.add(
                        GroundTruthIssue(
                            sample_id=sample_id,
                            paper_id=paper_id,
                            chunk_id=chunk_id,
                            issue_type=issue_type,
                            severity=severity,
                            message=message,
                            evidence=evidence,
                            page_num=page_num,
                            bbox=bbox,
                            source=source,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    inserted += 1

            await session.commit()
        return {"inserted": inserted, "updated": updated, "skipped": skipped}
    finally:
        await db_manager.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path", nargs="?", help="JSON 文件路径（格式：{items:[...] } 或 [...]）")
    ap.add_argument("--extract-from-reviews", action="store_true")
    ap.add_argument("--paper-root", default=str(_repo_root() / "paper"))
    ap.add_argument("--limit-per-review", type=int, default=4)
    ap.add_argument("--out", default="")
    ap.add_argument("--import", dest="do_import", action="store_true")
    args = ap.parse_args()

    if args.extract_from_reviews:
        paper_root = Path(str(args.paper_root)).resolve()
        items = _extract_from_reviews(paper_root, int(args.limit_per_review or 4))
        payload = {"generated_at": datetime.utcnow().isoformat() + "Z", "count": len(items), "items": items}
        if args.out:
            out_path = Path(str(args.out)).expanduser().resolve()
            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(str(out_path))
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        if args.do_import:
            result = asyncio.run(import_items(items))
            print(result)
        return 0

    if not args.json_path:
        raise SystemExit("missing json_path (or use --extract-from-reviews)")

    path = os.path.abspath(args.json_path)
    data = json.loads(open(path, "r", encoding="utf-8").read())
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        items = data["items"]
    elif isinstance(data, list):
        items = data
    else:
        raise SystemExit("invalid json format: expected {items:[...]} or [...]")

    result = asyncio.run(import_items(items))
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
