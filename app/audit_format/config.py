from enum import Enum
from typing import List
import os
from dotenv import load_dotenv
from urllib.parse import quote_plus, urlsplit

load_dotenv(override=False)

# Agent基本信息
AGENT_NAME = "Standardization_Auditor_Agent"
AGENT_VERSION = "v1.1"  # Updated version
AGENT_CODE = os.getenv("AGENT_CODE", "FMT").strip() or "FMT"

# 环境配置
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# LLM 配置
GEMINI_MODEL_NAME = "gemini-1.5-flash"
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")  # 从环境变量获取 API Key

# Qwen 配置 (DashScope Compatible)
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
QWEN_MODEL_NAME = os.getenv("QWEN_MODEL_NAME", "qwen-plus")

# DeepSeek 配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL_NAME = os.getenv("DEEPSEEK_MODEL_NAME", "deepseek-chat")

# LLM Provider: "gemini", "qwen", "deepseek" or "mock"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock")
LLM_TIMEOUT_SEC = int(os.getenv("LLM_TIMEOUT_SEC", "600"))

# RAG / Embedding 配置
SBERT_MODEL_NAME = os.getenv("SBERT_MODEL_NAME", "sentence-transformers/paraphrase-multilingual-mpnet-base-v2")
SBERT_DEVICE = os.getenv("SBERT_DEVICE", "")

# 布局分析配置
LAYOUT_ANALYSIS_TIMEOUT = int(os.getenv("LAYOUT_ANALYSIS_TIMEOUT", "600")) # 5分钟，适应长文档处理

# 数据库配置
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

if not DATABASE_URL:
    db_host = os.getenv("DB_HOST", "127.0.0.1").strip()
    db_port = os.getenv("DB_PORT", "5432").strip()
    db_name = os.getenv("DB_NAME", "postgres").strip()
    db_user = os.getenv("DB_USER", "postgres").strip()
    db_password = os.getenv("DB_PASSWORD", "")
    user_enc = quote_plus(db_user)
    if db_password:
        auth = user_enc + ":" + quote_plus(db_password)
    else:
        auth = user_enc
    DATABASE_URL = "postgresql+asyncpg://" + auth + "@" + db_host + ":" + db_port + "/" + db_name

def mask_database_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return "Hidden"
    try:
        normalized = raw.replace("postgresql+asyncpg://", "postgresql://", 1)
        s = urlsplit(normalized)
        if not s.hostname:
            return "Hidden"
        host = s.hostname
        port = f":{s.port}" if s.port else ""
        path = s.path or ""
        scheme = s.scheme or "postgresql"
        return f"{scheme}://***@{host}{port}{path}"
    except Exception:
        return "Hidden"

# 专属System Prompt (Moved to core/prompts.py)
# SYSTEM_PROMPT = "..."

# 专属Tags
class AuditTag(str, Enum):
    CITATION_INCONSISTENCY = "Citation_Inconsistency"
    LABEL_MISSING = "Label_Missing"
    PUNCTUATION_ERROR = "Punctuation_Error"
    HIERARCHY_FAULT = "Hierarchy_Fault"

# 允许的标签列表（用于校验）
ALLOWED_TAGS = [tag.value for tag in AuditTag]
