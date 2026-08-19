import yaml
from pathlib import Path
from pydantic import BaseModel, Field

class DatabaseConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 5432
    user: str = "postgres"
    password: str = ""
    database: str = "postgres"
    min_size: int = 5
    max_size: int = 20

class LLMConfig(BaseModel):
    model: str = "gpt-4"
    temperature: float = 0.3
    max_tokens: int = 2000
    api_base: str | None = None
    api_key: str = Field(default="", validation_alias="LITELLM_API_KEY")

class EvidenceValidationConfig(BaseModel):
    exact_match: bool = True
    semantic_threshold: float = 0.85
    enable_semantic: bool = True
    model_name: str = "all-MiniLM-L6-v2"  # sentence-transformers 模型

class DialogueConfig(BaseModel):
    quality_threshold: float = 4.0
    max_regeneration: int = 2
    role: str = "导师"  # 默认角色
    field_personas: dict = {
        "机器学习": "IEEE Fellow，专注可复现性研究15年",
        "软件工程": "ACM杰出科学家，主导过3个开源项目架构评审",
        "理论计算机": "图灵奖提名者，强调形式化证明严谨性"
    }

class ConflictResolutionConfig(BaseModel):
    """冲突裁决配置"""
    always_use_llm: bool = False  # 是否始终使用LLM裁决（纯LLM-as-a-Judge模式）
    score_diff_threshold: int = 20  # 分数差异阈值
    conflict_threshold: float = 0.7  # 冲突置信度阈值
    enable_semantic_conflict: bool = True  # 是否启用语义冲突检测

class Settings(BaseModel):
    database: DatabaseConfig = DatabaseConfig()
    llm: LLMConfig = LLMConfig()
    evidence: EvidenceValidationConfig = EvidenceValidationConfig()
    dialogue: DialogueConfig = DialogueConfig()
    conflict_resolution: ConflictResolutionConfig = ConflictResolutionConfig()
    debug: bool = False

    class Config:
        env_prefix = "REF_"
        env_nested_delimiter = "__"

    @classmethod
    def from_yaml(cls, path: Path = Path("config/reflection_config.yaml")):
        if path.exists():
            with open(path) as f:
                yaml_data = yaml.safe_load(f)
            return cls(**yaml_data)
        else:
            # 如果配置文件不存在，使用默认值
            return cls()

# 尝试加载配置，如果失败则使用默认值
try:
    settings = Settings.from_yaml()
except Exception:
    settings = Settings()
