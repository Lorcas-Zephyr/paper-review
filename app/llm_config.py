"""Single source of truth for the project's DeepSeek configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


APP_ROOT = Path(__file__).resolve().parent
ENV_FILE = APP_ROOT / ".env"
if load_dotenv is not None:
    load_dotenv(ENV_FILE, override=False)


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(_first_env(name, default=str(default)))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(_first_env(name, default=str(default)))
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = _first_env(name, default="true" if default else "false")
    return raw.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class DeepSeekConfig:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"
    timeout_seconds: int = 600
    max_tokens: int = 4096
    temperature: float = 0.0
    json_mode: bool = True
    thinking_enabled: bool = False

    @property
    def chat_completions_url(self) -> str:
        base = self.base_url.rstrip("/")
        return base if base.endswith("/chat/completions") else f"{base}/chat/completions"

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def public_dict(self) -> Dict[str, Any]:
        return {
            "provider": "deepseek",
            "base_url": self.base_url,
            "chat_completions_url": self.chat_completions_url,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "json_mode": self.json_mode,
            "thinking_enabled": self.thinking_enabled,
            "configured": self.configured,
        }


def get_deepseek_config(*, require_key: bool = False) -> DeepSeekConfig:
    config = DeepSeekConfig(
        api_key=_first_env("DEEPSEEK_API_KEY", "LLM_API_KEY"),
        base_url=_first_env("DEEPSEEK_BASE_URL", "LLM_BASE_URL", default="https://api.deepseek.com"),
        model=_first_env("DEEPSEEK_MODEL", "DEEPSEEK_MODEL_NAME", "LLM_MODEL", default="deepseek-v4-flash"),
        timeout_seconds=_int_env("DEEPSEEK_TIMEOUT_SECONDS", _int_env("LLM_TIMEOUT_SEC", 600)),
        max_tokens=_int_env("DEEPSEEK_MAX_TOKENS", 4096),
        temperature=_float_env("DEEPSEEK_TEMPERATURE", 0.0),
        json_mode=_bool_env("DEEPSEEK_JSON_MODE", True),
        thinking_enabled=_bool_env("DEEPSEEK_THINKING_ENABLED", False),
    )
    if require_key and not config.configured:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured in app/.env")
    return config
