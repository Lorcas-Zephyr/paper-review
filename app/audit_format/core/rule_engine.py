import yaml
import os
import asyncio
from typing import Dict, Any
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from .database import db_manager, AgentRule
from utils.logger import setup_logger

logger = setup_logger(__name__)

class RuleEngine:
    """
    负责加载和管理审计规则 (支持从 DB 和 rules.yaml 加载)
    """
    def __init__(self, config_path: str | None = None):
        if not config_path:
            config_path = str(Path(__file__).resolve().parents[1] / "rules.yaml")
        self.config_path = config_path
        self.rules: Dict[str, Any] = {}
        # Initial load from YAML as fallback/default
        self.load_rules_from_yaml()

    def load_rules_from_yaml(self):
        """
        从 YAML 文件加载规则 (同步)
        """
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self.rules = yaml.safe_load(f) or {}
                logger.info(f"Loaded {len(self.rules)} rules from YAML")
            except Exception as e:
                logger.error(f"Failed to load rules from YAML: {e}")
                self.rules = {}
        else:
            logger.warning(f"Rules file not found: {self.config_path}")
            self.rules = {}

    async def load_rules_from_db(self) -> bool:
        """
        从数据库加载规则 (异步)
        覆盖 YAML 中的同名规则
        """
        try:
            async with db_manager.session() as session:
                stmt = select(AgentRule)
                result = await session.execute(stmt)
                rules = result.scalars().all()

                for rule in rules:
                    try:
                        content = yaml.safe_load(rule.content)
                        self.rules[rule.rule_id] = content
                    except:
                        self.rules[rule.rule_id] = rule.content

                logger.info(f"Loaded {len(rules)} rules from DB, total rules: {len(self.rules)}")
            return True
        except Exception as e:
            if isinstance(e, ProgrammingError):
                orig = getattr(e, "orig", None)
                sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
                combined = f"{e} {orig}" if orig else str(e)
                lowered = combined.lower()
                if (
                    (sqlstate == "42P01")
                    or ("undefinedtable" in lowered)
                    or ("does not exist" in lowered)
                    or ("relation" in lowered and "does not exist" in lowered)
                ) and ("agent_rules" in lowered):
                    logger.warning("Rules table missing in DB (agent_rules). Skipping DB rule loading and using YAML rules.")
                    return False
            logger.warning(f"Failed to load rules from DB: {e!r}")
            # Fallback to YAML is already done in __init__
            return False

    def get_rule(self, module_name: str) -> Dict[str, Any]:
        """
        获取指定模块的规则配置
        """
        return self.rules.get(module_name, {})

    async def reload(self):
        """
        重新加载规则 (支持热更新)
        """
        self.load_rules_from_yaml()
        await self.load_rules_from_db()
