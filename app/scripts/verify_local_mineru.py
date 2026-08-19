import asyncio
import argparse
import faulthandler
import shutil
import sys
import tempfile
from pathlib import Path

import fitz

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from local_model_config import enable_offline_model_mode

enable_offline_model_mode()

from pdf_to_md import main  # noqa: E402


def find_sample_pdf() -> Path:
    papers_dir = APP_ROOT.parent / "papers"
    try:
        return next(path for path in papers_dir.rglob("*.pdf") if path.is_file())
    except StopIteration as exc:
        raise FileNotFoundError(f"No PDF was found under {papers_dir}") from exc


def main_test(source: Path, *, debug_traces: bool = False) -> None:
    if debug_traces:
        faulthandler.enable()
        faulthandler.dump_traceback_later(30, repeat=True)
    work_dir = Path(tempfile.mkdtemp(prefix="paper-review-mineru-"))
    try:
        one_page = work_dir / "sample.pdf"
        source_doc = fitz.open(source)
        sample_doc = fitz.open()
        sample_doc.insert_pdf(source_doc, from_page=0, to_page=0)
        sample_doc.save(one_page)
        sample_doc.close()
        source_doc.close()

        main.OUTPUT_DIR = work_dir / "outputs"
        main.OUTPUT_DIR.mkdir()
        result = asyncio.run(main.parse_pdf_enhanced(str(one_page)))
        if not result.get("success"):
            raise RuntimeError(result.get("error"))
        markdown = result["files"]["markdown"]
        if not markdown or not markdown["content"].strip():
            raise RuntimeError("MinerU produced no Markdown")
        print(f"MinerU local GPU smoke OK: markdown_chars={len(markdown['content'])}")
    finally:
        if debug_traces:
            faulthandler.cancel_dump_traceback_later()
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", nargs="?", type=Path, default=None)
    parser.add_argument("--debug-traces", action="store_true")
    args = parser.parse_args()
    main_test(args.pdf or find_sample_pdf(), debug_traces=args.debug_traces)
