"""Download every open-source model used by the paper-review services.

The files are stored under ``app/model_cache`` (ignored by Git).  DeepSeek is
deliberately excluded because it is an API-only model in this project.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from modelscope import snapshot_download

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from local_model_config import (  # noqa: E402
    LOCAL_MODEL_DIR,
    MINERU_CONFIG_FILE,
    MINERU_MODEL_DIR,
    MODEL_IDS,
    configure_local_model_environment,
)


def download_huggingface_models() -> list[dict[str, str]]:
    results = []
    for name, model_id in MODEL_IDS.items():
        print(f"[model] downloading {name}: {model_id}", flush=True)
        snapshot_path = snapshot_download(
            model_id=model_id,
            cache_dir=str(LOCAL_MODEL_DIR / "modelscope-cache"),
            local_dir=str(LOCAL_MODEL_DIR / model_id.replace("/", "--")),
            ignore_file_pattern=[
                "onnx/**",
                "openvino/**",
                "*.onnx",
                "*.h5",
                "*.msgpack",
                "*.ot",
            ],
        )
        results.append({"name": name, "model_id": model_id, "snapshot": snapshot_path})
        print(f"[model] ready {model_id}: {snapshot_path}", flush=True)
    return results


def write_mineru_config() -> None:
    """Tell MinerU to use the project-local pipeline model directory."""
    MINERU_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    config = {
        "models-dir": {"pipeline": str(MINERU_MODEL_DIR)},
        "model-source": "modelscope",
    }
    MINERU_CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[mineru] config written: {MINERU_CONFIG_FILE}", flush=True)


def download_mineru_models() -> None:
    write_mineru_config()
    print("[mineru] downloading local pipeline models from ModelScope", flush=True)
    command = [sys.executable, "-m", "mineru.cli.models_download", "-s", "modelscope", "-m", "pipeline"]
    subprocess.run(command, check=True, env=os.environ.copy())

    config = json.loads(MINERU_CONFIG_FILE.read_text(encoding="utf-8"))
    config["model-source"] = "local"
    MINERU_CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[mineru] offline configuration ready: {MINERU_CONFIG_FILE}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-mineru", action="store_true")
    parser.add_argument("--only-mineru", action="store_true")
    args = parser.parse_args()

    configure_local_model_environment()
    if not args.only_mineru:
        download_huggingface_models()
    if not args.skip_mineru:
        download_mineru_models()


if __name__ == "__main__":
    main()
