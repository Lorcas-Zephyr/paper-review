"""Manifest builder for the local paper/review PDF corpus.

The corpus is intentionally accessed by path at runtime.  Only this compact
manifest (and optionally extracted text for approved samples) belongs in the
project data contract; raw PDFs stay outside Git and require controlled access.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional


@dataclass(frozen=True)
class PaperSample:
    paper_id: str
    paper_path: str
    review_paths: List[str]
    paper_sha256: str
    paper_size_bytes: int
    paper_pages: Optional[int]
    review_count: int
    split: str

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _page_count(path: Path) -> Optional[int]:
    """Best-effort page count without making PDF parsing mandatory."""
    try:
        from pypdf import PdfReader  # type: ignore

        return len(PdfReader(str(path)).pages)
    except Exception:
        try:
            from PyPDF2 import PdfReader  # type: ignore

            return len(PdfReader(str(path)).pages)
        except Exception:
            return None


def _split_for(paper_id: str, seed: int) -> str:
    value = hashlib.sha256(f"{seed}:{paper_id}".encode("utf-8")).hexdigest()
    bucket = int(value[:8], 16) % 100
    if bucket < 70:
        return "train"
    if bucket < 90:
        return "validation"
    return "test"


def iter_samples(root: os.PathLike[str] | str, *, seed: int = 20260819, include_pages: bool = True) -> Iterator[PaperSample]:
    """Yield one sample per ``<id>/paper.pdf`` directory."""
    root_path = Path(root).expanduser().resolve()
    if not root_path.exists():
        raise FileNotFoundError(f"papers root does not exist: {root_path}")
    for sample_dir in sorted(path for path in root_path.iterdir() if path.is_dir()):
        paper = sample_dir / "paper.pdf"
        if not paper.is_file():
            continue
        reviews_dir = sample_dir / "reviews"
        reviews = sorted(path.relative_to(root_path).as_posix() for path in reviews_dir.glob("review-*.pdf") if path.is_file()) if reviews_dir.is_dir() else []
        yield PaperSample(
            paper_id=sample_dir.name,
            paper_path=paper.relative_to(root_path).as_posix(),
            review_paths=reviews,
            paper_sha256=_sha256(paper),
            paper_size_bytes=paper.stat().st_size,
            paper_pages=_page_count(paper) if include_pages else None,
            review_count=len(reviews),
            split=_split_for(sample_dir.name, seed),
        )


def build_manifest(
    root: os.PathLike[str] | str,
    output: os.PathLike[str] | str,
    *,
    seed: int = 20260819,
    include_pages: bool = True,
) -> Dict[str, Any]:
    """Build JSONL manifest and return summary metadata."""
    samples = list(iter_samples(root, seed=seed, include_pages=include_pages))
    output_path = Path(output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for sample in samples:
            handle.write(json.dumps(sample.as_dict(), ensure_ascii=False) + "\n")
    return {
        "manifest": str(output_path),
        "root": str(Path(root).expanduser().resolve()),
        "seed": seed,
        "sample_count": len(samples),
        "review_count": sum(sample.review_count for sample in samples),
        "splits": {
            split: sum(1 for sample in samples if sample.split == split)
            for split in ("train", "validation", "test")
        },
    }


def load_manifest(path: os.PathLike[str] | str) -> List[Dict[str, Any]]:
    """Load and validate the JSONL manifest without touching PDF bytes."""
    rows: List[Dict[str, Any]] = []
    with Path(path).expanduser().open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid manifest JSON at line {line_number}") from exc
            required = {"paper_id", "paper_path", "review_paths", "paper_sha256", "split"}
            missing = required - set(row)
            if missing:
                raise ValueError(f"manifest line {line_number} missing fields: {sorted(missing)}")
            rows.append(row)
    return rows


def extract_pdf_text(path: os.PathLike[str] | str, *, max_chars: Optional[int] = None) -> str:
    """Extract text for an approved sample at evaluation time.

    Extraction is intentionally opt-in and bounded; manifest generation never
    reads the full corpus into memory.
    """
    reader_cls = None
    try:
        from pypdf import PdfReader  # type: ignore

        reader_cls = PdfReader
    except Exception:
        try:
            from PyPDF2 import PdfReader  # type: ignore

            reader_cls = PdfReader
        except Exception as exc:
            raise RuntimeError("install pypdf or PyPDF2 to extract PDF text") from exc
    chunks: List[str] = []
    for page in reader_cls(str(path)).pages:
        chunks.append(page.extract_text() or "")
        if max_chars is not None and sum(len(item) for item in chunks) >= max_chars:
            break
    text = "\n\n".join(chunks)
    return text[:max_chars] if max_chars is not None else text


def load_sample(
    root: os.PathLike[str] | str,
    row: Mapping[str, Any],
    *,
    include_reviews: bool = False,
    max_chars: int = 120_000,
) -> Dict[str, Any]:
    """Resolve one manifest row and optionally extract bounded text."""
    root_path = Path(root).expanduser().resolve()
    paper_path = root_path / str(row["paper_path"])
    sample = dict(row)
    sample["paper_text"] = extract_pdf_text(paper_path, max_chars=max_chars)
    if include_reviews:
        sample["review_texts"] = [
            extract_pdf_text(root_path / str(review_path), max_chars=max_chars // 2)
            for review_path in row.get("review_paths", [])
        ]
    return sample
