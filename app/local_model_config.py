"""Shared local model cache and offline runtime configuration.

Open-source model weights are downloaded once into ``app/model_cache`` and
all services resolve them from there.  DeepSeek remains an API model because
its weights are not distributed for local inference.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


APP_ROOT = Path(__file__).resolve().parent
MODEL_CACHE_DIR = APP_ROOT / "model_cache"
HF_CACHE_DIR = MODEL_CACHE_DIR / "huggingface"
HF_HUB_DIR = HF_CACHE_DIR / "hub"
LOCAL_MODEL_DIR = MODEL_CACHE_DIR / "models"
MINERU_MODEL_DIR = MODEL_CACHE_DIR / "mineru"
MINERU_CONFIG_FILE = MODEL_CACHE_DIR / "mineru.json"

MODEL_IDS = {
    "paper_embedding": "sentence-transformers/all-mpnet-base-v2",
    "format_embedding": "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
    "reflection_embedding": "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
    "logic_nli": "cross-encoder/nli-deberta-v3-base",
    "logic_fallback": "hfl/chinese-roberta-wwm-ext-large",
}


def configure_local_model_environment() -> None:
    """Configure cache locations before importing transformers or MinerU."""
    if load_dotenv is not None:
        load_dotenv(APP_ROOT / ".env", override=False)

    MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    HF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    MINERU_MODEL_DIR.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("HF_HOME", str(HF_CACHE_DIR))
    os.environ.setdefault("HF_HUB_CACHE", str(HF_HUB_DIR))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(HF_CACHE_DIR / "transformers"))
    os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(HF_HUB_DIR))
    os.environ.setdefault("MODELSCOPE_CACHE", str(MINERU_MODEL_DIR))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("MINERU_TOOLS_CONFIG_JSON", str(MINERU_CONFIG_FILE))


def _ascii_native_cache_dir() -> Path:
    """Return an ASCII-only cache path for native libraries on Windows."""
    override = os.getenv("PAPER_REVIEW_NATIVE_CACHE")
    candidates = [
        Path(override) if override else None,
        Path(tempfile.gettempdir()) / "paper-review-model-cache",
        Path(os.getenv("LOCALAPPDATA", "")) / "paper-review-model-cache",
        Path(os.getenv("SystemDrive", "C:")) / "Temp" / "paper-review-model-cache",
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            str(candidate).encode("ascii")
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except (OSError, UnicodeEncodeError):
            continue
    raise RuntimeError(
        "No writable ASCII-only cache directory is available. Set "
        "PAPER_REVIEW_NATIVE_CACHE to an ASCII-only path."
    )


def configure_fasttext_model_path() -> Path | None:
    """Move fast-langdetect's native model behind an ASCII Windows path."""
    if os.name != "nt":
        return None

    from fast_langdetect.ft_detect import infer

    source = Path(infer.LOCAL_SMALL_MODEL_PATH)
    target_dir = _ascii_native_cache_dir() / "fasttext"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    if not target.exists() or target.stat().st_size != source.stat().st_size:
        shutil.copy2(source, target)

    os.environ["FTLANG_CACHE"] = str(target_dir)
    infer.CACHE_DIRECTORY = str(target_dir)
    infer.LOCAL_SMALL_MODEL_PATH = target
    return target


def enable_offline_model_mode() -> None:
    """Prevent runtime model loaders from making network requests."""
    configure_local_model_environment()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["MINERU_MODEL_SOURCE"] = "local"
    configure_fasttext_model_path()


def model_cache_dir(model_id: str) -> Path:
    """Return the project cache root used by ``transformers`` for a model."""
    return LOCAL_MODEL_DIR / model_id.replace("/", "--")


def model_is_cached(model_id: str) -> bool:
    """Check whether a model has a usable local snapshot."""
    try:
        path = model_cache_dir(model_id)
        return path.exists() and any(
            (path / marker).exists()
            for marker in ("config.json", "modules.json", "tokenizer_config.json")
        )
    except Exception:
        return False


def require_local_model(model_id: str) -> str:
    """Return ``model_id`` for offline loaders or raise a useful error."""
    if not model_is_cached(model_id):
        raise RuntimeError(
            f"Local model is not cached: {model_id}. "
            "Run `python app/scripts/download_local_models.py` first."
        )
    return str(model_cache_dir(model_id))


def load_sentence_transformer(model_id: str, *, device: str | None = None):
    """Load a SentenceTransformer strictly from the shared local cache."""
    enable_offline_model_mode()
    local_path = require_local_model(model_id)
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(
        local_path,
        device=device,
        cache_folder=str(HF_HUB_DIR),
        local_files_only=True,
    )


configure_local_model_environment()
