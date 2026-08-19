import os

from crewai import Agent
from crewai import LLM

_BASE = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()

llm = LLM(
    base_url=_BASE,
    api_key=_KEY,
    model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
    temperature=0,
)
