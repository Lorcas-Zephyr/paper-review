import sys
from pathlib import Path

from crewai import Agent
from crewai import LLM

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from llm_config import get_deepseek_config

DEEPSEEK_CONFIG = get_deepseek_config()

llm = LLM(
    base_url=DEEPSEEK_CONFIG.base_url,
    api_key=DEEPSEEK_CONFIG.api_key,
    model=DEEPSEEK_CONFIG.model,
    temperature=DEEPSEEK_CONFIG.temperature,
)
