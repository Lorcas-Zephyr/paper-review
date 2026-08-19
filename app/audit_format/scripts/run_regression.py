import argparse
import asyncio
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


@dataclass(frozen=True)
class Sample:
    sample_id: str
    kind: str
    path: Path
    url: Optional[str] = None
    sha256: Optional[str] = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_manifest(path: Path) -> List[Sample]:
    data = json.loads(path.read_text(encoding="utf-8"))
    samples = []
    for s in data.get("samples", []) or []:
        sample_id = str(s.get("id") or "").strip()
        kind = str(s.get("kind") or "").strip()
        rel = str(s.get("path") or "").strip()
        url = str(s.get("url") or "").strip() or None
        sha256 = str(s.get("sha256") or "").strip() or None
        if not sample_id or not rel:
            continue
        samples.append(Sample(sample_id=sample_id, kind=kind, path=_repo_root() / rel, url=url, sha256=sha256))
    return samples


def _scan_default_samples() -> List[Sample]:
    root = _repo_root()
    paper_root = root / "paper"
    samples: List[Sample] = []
    if not paper_root.exists():
        return samples

    mineru_root = paper_root / "papers-reviews-mineru10篇处理"
    if mineru_root.exists():
        for p in sorted(mineru_root.glob("**/paper/hybrid_auto/paper_origin*.pdf")):
            parts = p.parts
            sid = "unknown"
            try:
                idx = parts.index("papers-reviews-mineru10篇处理")
                sid = parts[idx + 1]
            except Exception:
                sid = "unknown"
            sample_id = f"papers-mineru10-{sid}-{p.stem}".replace(" ", "_")
            samples.append(Sample(sample_id=sample_id, kind="paper_origin", path=p))

    for p in sorted(paper_root.glob("*.pdf")):
        sample_id = f"paper-top-{p.stem}".replace(" ", "_")
        samples.append(Sample(sample_id=sample_id, kind="paper_misc", path=p))

    uniq: Dict[str, Sample] = {}
    for s in samples:
        if s.sample_id not in uniq:
            uniq[s.sample_id] = s
    return list(uniq.values())


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


async def _maybe_download(sample: Sample) -> bool:
    if sample.path.exists():
        return True
    if not sample.url:
        return False

    try:
        import httpx
    except Exception:
        return False

    sample.path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = sample.path.with_suffix(sample.path.suffix + ".tmp")

    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
        async with client.stream("GET", sample.url) as resp:
            resp.raise_for_status()
            with tmp_path.open("wb") as f:
                async for chunk in resp.aiter_bytes():
                    if chunk:
                        f.write(chunk)

    if sample.sha256:
        digest = _sha256_file(tmp_path)
        if digest.lower() != sample.sha256.lower():
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            return False

    tmp_path.replace(sample.path)
    return True


def _extract_text(pdf_path: Path, pages: Optional[List[int]]) -> Tuple[str, int]:
    import fitz

    doc = fitz.open(str(pdf_path))
    try:
        selected = set(pages or [])
        text_parts = []
        for idx, page in enumerate(doc):
            page_num = idx + 1
            if selected and page_num not in selected:
                continue
            text_parts.append(page.get_text())
        return "".join(text_parts), len(doc)
    finally:
        doc.close()


async def _run_one(
    sample: Sample,
    pages: Optional[List[int]],
    run_layout: bool,
    run_semantic: bool,
    timeout_sec: int,
) -> Dict[str, Any]:
    from core.layout_analysis import LayoutAnalyzer
    from core.semantic_check import SemanticChecker

    result: Dict[str, Any] = {
        "id": sample.sample_id,
        "kind": sample.kind,
        "path": str(sample.path),
        "exists": sample.path.exists(),
    }
    if not await _maybe_download(sample):
        result["exists"] = sample.path.exists()
        return result
    result["exists"] = True

    text_content, page_count = _extract_text(sample.path, pages)
    result["page_count"] = page_count
    result["text_len"] = len(text_content)

    layout_data: Optional[Dict[str, Any]] = None
    if run_layout:
        layout_analyzer = LayoutAnalyzer()
        layout_input: Any = str(sample.path)
        if pages:
            layout_input = {"pdf_path": str(sample.path), "pages": pages}
        layout_data = await asyncio.wait_for(layout_analyzer.analyze(layout_input), timeout=timeout_sec)
        result["layout_elements"] = len((layout_data or {}).get("elements", []) or [])
        result["layout_issues"] = len(((layout_data or {}).get("layout_result", {}) or {}).get("layout_issues", []) or [])

    if run_semantic:
        semantic_checker = SemanticChecker()
        semantic_result = await asyncio.wait_for(
            semantic_checker.check(text_content, layout_data or {"elements": []}),
            timeout=timeout_sec,
        )
        result["semantic_issues"] = len((semantic_result or {}).get("semantic_issues", []) or [])

    return result


def _parse_pages(spec: str) -> List[int]:
    s = (spec or "").strip()
    if not s:
        return []
    out: List[int] = []
    for part in s.split(","):
        p = part.strip()
        if not p:
            continue
        if "-" in p:
            a, b = p.split("-", 1)
            try:
                start = int(a.strip())
                end = int(b.strip())
            except Exception:
                continue
            if start <= 0 or end <= 0:
                continue
            if start > end:
                start, end = end, start
            out.extend(list(range(start, end + 1)))
        else:
            try:
                v = int(p)
            except Exception:
                continue
            if v > 0:
                out.append(v)
    uniq = sorted(set(out))
    return uniq


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default=str(AGENT_DIR / "scripts" / "regression_samples.json"))
    parser.add_argument("--pages", default="1")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--layout", action="store_true")
    parser.add_argument("--semantic", action="store_true")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--scan", action="store_true")
    args = parser.parse_args()

    os.environ.setdefault("LLM_PROVIDER", "mock")
    os.environ.setdefault("LOG_LEVEL", "WARNING")

    pages = _parse_pages(args.pages)
    if not pages:
        pages = []

    manifest_path = Path(args.manifest)
    samples: List[Sample]
    if args.scan:
        samples = _scan_default_samples()
    else:
        samples = _load_manifest(manifest_path) if manifest_path.exists() else _scan_default_samples()

    if args.limit and args.limit > 0:
        samples = samples[: args.limit]

    if args.list:
        for s in samples:
            rel = None
            try:
                rel = s.path.relative_to(_repo_root())
            except Exception:
                rel = s.path
            print(f"{s.sample_id}\t{s.kind}\t{rel}")
        return 0

    async def _run_all() -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for s in samples:
            out.append(await _run_one(s, pages or None, bool(args.layout), bool(args.semantic), int(args.timeout)))
        return out

    results = asyncio.run(_run_all())
    print(json.dumps({"count": len(results), "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
