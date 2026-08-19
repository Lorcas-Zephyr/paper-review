"""CLI for creating a compact manifest from the local ``papers`` corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

APP_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_ROOT = APP_ROOT / "orchestrator"
if str(ORCHESTRATOR_ROOT) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_ROOT))

from paper_dataset import build_manifest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a JSONL manifest for paper/review PDFs")
    parser.add_argument("--root", default="papers", help="papers/<sample>/ root")
    parser.add_argument("--output", default="papers/manifest.jsonl")
    parser.add_argument("--seed", type=int, default=20260819)
    parser.add_argument("--skip-pages", action="store_true", help="skip optional PDF page parsing")
    args = parser.parse_args()
    summary = build_manifest(
        args.root,
        args.output,
        seed=args.seed,
        include_pages=not args.skip_pages,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

