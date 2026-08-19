"""
统一数据库配置模块
连接信息通过 DB_* 环境变量配置。

Week3更新：
- 输入表从agent_audits改为agent_audit_result
- 输出表从agent_audits改为reflect_agent_verdict
- 新增从main_rules和rule_judge表读取规则
- 新增评估状态检查（是否已评估、是否需要重新评估）
- 新增检查4个审计组是否齐全
"""
import asyncpg
import hashlib
import json
import logging
import os
from typing import Optional, List, Dict, Any, Tuple
from contextlib import asynccontextmanager
from datetime import datetime

logger = logging.getLogger(__name__)

# 数据库配置
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "postgres"),
}

# 4个审计智能体编码（不含代码审计）
REQUIRED_AGENT_CODES = {"FMT", "REF", "EXP", "LOG"}


class DatabaseManager:
    """统一数据库管理器"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or DB_CONFIG
        self.pool: Optional[asyncpg.Pool] = None

    @asynccontextmanager
    async def acquire(self):
        """获取无池化的数据库单次连接 (保证评测后关闭连接)"""
        connection = await asyncpg.connect(
            host=self.config["host"],
            port=self.config["port"],
            user=self.config["user"],
            password=self.config["password"],
            database=self.config["database"],
            command_timeout=60
        )
        try:
            yield connection
        finally:
            await connection.close()

    async def connect(self):
        """(为兼容旧代码保留空实现)"""
        pass

    async def disconnect(self):
        """(为兼容旧代码保留空实现)"""
        pass

    # ==================== 规则读取 ====================

    async def fetch_rules(self) -> List[Dict[str, Any]]:
        """
        从main_rules和rule_judge表读取评审规则（JOIN查询）

        Returns:
            规则列表，每条包含main_rules和rule_judge的合并字段
        """
        try:
            async with self.acquire() as conn:
                query = """
                    SELECT
                        m.rule_id, m.agent_code, m.agent_name_en, m.agent_name_cn,
                        m.rule_name_en, m.rule_name_cn, m.rule_detail,
                        m.full_score, m.severity, m.rule_type,
                        j.judge_id, j.check_indicator, j.operator,
                        j.threshold_val, j.threshold_unit_en, j.threshold_unit_cn,
                        j.is_core_rule
                    FROM main_rules m
                    LEFT JOIN rule_judge j ON m.rule_id = j.rule_id
                    ORDER BY m.agent_code, m.rule_id
                """
                rows = await conn.fetch(query)
                rules = []
                for row in rows:
                    rules.append(dict(row))
                logger.info(f"从数据库读取了{len(rules)}条评审规则")
                return rules
        except Exception as e:
            logger.error(f"读取规则表失败: {e}")
            raise

    async def fetch_rules_by_agent(self, agent_code: str) -> List[Dict[str, Any]]:
        """读取指定智能体的规则"""
        try:
            async with self.acquire() as conn:
                query = """
                    SELECT
                        m.rule_id, m.agent_code, m.agent_name_en, m.agent_name_cn,
                        m.rule_name_en, m.rule_name_cn, m.rule_detail,
                        m.full_score, m.severity, m.rule_type,
                        j.judge_id, j.check_indicator, j.operator,
                        j.threshold_val, j.threshold_unit_en, j.threshold_unit_cn,
                        j.is_core_rule
                    FROM main_rules m
                    LEFT JOIN rule_judge j ON m.rule_id = j.rule_id
                    WHERE m.agent_code = $1
                    ORDER BY m.rule_id
                """
                rows = await conn.fetch(query, agent_code)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"读取{agent_code}规则失败: {e}")
            raise

    # ==================== 审计结果读取（agent_audits表） ====================
    async def fetch_audit_results(self, paper_id: Optional[str] = None, dedup_by_rule: bool = False) -> List[Dict[str, Any]]:
        """
        从 agent_audits 表读取审计结果并适配为 Reflection Agent 兼容格式

        Args:
            paper_id: 论文ID，如果为None则读取所有
            dedup_by_rule: 是否去重（这里按agent_name去重，保留最新的）

        Returns:
            审计结果列表
        """
        agent_name_map = {
            "Format_Agent": "FMT",
            "Citation_Agent": "REF",
            "Experiment_Agent": "EXP",
            "逻辑审计组": "LOG"
        }
        try:
            async with self.acquire() as conn:
                if dedup_by_rule:
                    base_filter = "WHERE paper_id = $1" if paper_id else ""
                    query = f"""
                        SELECT id, paper_id, agent_name, score,
                               audit_level, created_at, result_json
                        FROM (
                            SELECT *, ROW_NUMBER() OVER (
                                PARTITION BY paper_id, agent_name
                                ORDER BY created_at DESC
                            ) AS rn
                            FROM agent_audits
                            {base_filter}
                        ) sub
                        WHERE rn = 1 AND status = 'SUCCESS'
                        ORDER BY paper_id, agent_name, created_at
                    """
                    rows = await conn.fetch(query, paper_id) if paper_id else await conn.fetch(query)
                else:
                    if paper_id:
                        query = """
                            SELECT id, paper_id, agent_name, score,
                                   audit_level, created_at, result_json
                            FROM agent_audits
                            WHERE paper_id = $1 AND status = 'SUCCESS'
                            ORDER BY agent_name, created_at
                        """
                        rows = await conn.fetch(query, paper_id)
                    else:
                        query = """
                            SELECT id, paper_id, agent_name, score,
                                   audit_level, created_at, result_json
                            FROM agent_audits
                            WHERE status = 'SUCCESS'
                            ORDER BY paper_id, agent_name, created_at
                        """
                        rows = await conn.fetch(query)

                results = []
                for row in rows:
                    result_json = row["result_json"]
                    if isinstance(result_json, str):
                        try:
                            result_json = json.loads(result_json)
                        except json.JSONDecodeError:
                            logger.warning(f"无法解析result_json: {str(result_json)[:100]}")
                            result_json = {}

                    agent_name = row["agent_name"]
                    agent_code = agent_name_map.get(agent_name, "UNK")

                    results.append({
                        "result_id": str(row["id"]),
                        "paper_id": str(row["paper_id"]),
                        "paper_name": None,
                        "agent_code": agent_code,
                        "rule_id": "", # Map required dummy value
                        "is_compliant": None,
                        "actual_value": None,
                        "score_obtained": row["score"],
                        "audit_suggestion": "",
                        "audit_time": row["created_at"],
                        "result_json": result_json,
                    })

                logger.info(f"读取到{len(results)}条审计结果")
                return results

        except Exception as e:
            logger.error(f"读取agent_audit_result表失败: {e}")
            raise

    # 兼容旧接口
    async def fetch_agent_audits(self, paper_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return await self.fetch_audit_results(paper_id)

    async def get_agent_results(self, paper_id: str) -> List[Dict[str, Any]]:
        return await self.fetch_audit_results(paper_id)

    # ==================== 论文ID和状态检查 ====================
    async def get_paper_ids(self) -> List[str]:
        """获取agent_audit_result表中所有不同的paper_id"""
        try:
            async with self.acquire() as conn:
                query = """
                    SELECT DISTINCT paper_id
                    FROM agent_audit_result
                    ORDER BY paper_id
                """
                rows = await conn.fetch(query)
                paper_ids = [row["paper_id"] for row in rows]
                logger.info(f"找到{len(paper_ids)}篇论文")
                return paper_ids
        except Exception as e:
            logger.error(f"获取论文ID列表失败: {e}")
            raise

    async def check_paper_has_all_agents(self, paper_id: str) -> Tuple[bool, List[str]]:
        """
        检查指定paper_id是否已收集齐所有必须的agent结果
        (FMT, REF, EXP, LOG) -> 映射为 Format_Agent, Citation_Agent, Experiment_Agent, 逻辑审计组

        Returns:
            (是否齐全, 已有的agent_code列表)
        """
        agent_name_map = {
            "Format_Agent": "FMT",
            "Citation_Agent": "REF",
            "Experiment_Agent": "EXP",
            "逻辑审计组": "LOG"
        }
        try:
            async with self.acquire() as conn:
                query = """
                    SELECT DISTINCT agent_name
                    FROM agent_audits
                    WHERE paper_id = $1 AND status = 'SUCCESS'
                """
                rows = await conn.fetch(query, paper_id)
                existing_codes = set()
                for row in rows:
                    aname = row["agent_name"]
                    if aname in agent_name_map:
                        existing_codes.add(agent_name_map[aname])

                has_all = REQUIRED_AGENT_CODES.issubset(existing_codes)
                return has_all, list(existing_codes)
        except Exception as e:
            logger.error(f"检查审计组完整性失败: {e}")
            raise

    async def check_paper_has_all_rules(self, paper_id: str) -> Tuple[bool, Dict[str, Any]]:
        """
        检查指定paper_id的每个agent是否已应用其所有规则

        Returns:
            (是否齐全, 详情字典 {"agent_code": {"expected": set, "actual": set, "missing": set}})
        """
        try:
            async with self.acquire() as conn:
                # 查询每个agent_code应有的rule_id（从main_rules表）
                expected_query = """
                    SELECT agent_code, rule_id
                    FROM main_rules
                    WHERE agent_code IN ('FMT', 'REF', 'EXP', 'LOG')
                    ORDER BY agent_code, rule_id
                """
                expected_rows = await conn.fetch(expected_query)

                expected_rules = {}
                for row in expected_rows:
                    code = row["agent_code"]
                    if code not in expected_rules:
                        expected_rules[code] = set()
                    expected_rules[code].add(row["rule_id"])

                # 查询该paper_id实际已有的(agent_code, rule_id)（去重后）
                actual_query = """
                    SELECT DISTINCT agent_code, rule_id
                    FROM agent_audit_result
                    WHERE paper_id = $1
                """
                actual_rows = await conn.fetch(actual_query, paper_id)

                actual_rules = {}
                for row in actual_rows:
                    code = row["agent_code"]
                    if code not in actual_rules:
                        actual_rules[code] = set()
                    actual_rules[code].add(row["rule_id"])

                # 比较
                details = {}
                all_complete = True
                for agent_code in REQUIRED_AGENT_CODES:
                    expected = expected_rules.get(agent_code, set())
                    actual = actual_rules.get(agent_code, set())
                    missing = expected - actual
                    details[agent_code] = {
                        "expected_count": len(expected),
                        "actual_count": len(actual),
                        "missing_count": len(missing),
                        "missing_rules": list(missing),
                    }
                    if missing:
                        all_complete = False

                return all_complete, details
        except Exception as e:
            logger.error(f"检查规则完整性失败: {e}")
            raise

    async def check_verdict_exists(self, paper_id: str) -> Tuple[bool, Optional[datetime]]:
        """
        检查reflect_agent_verdict表中是否已有该paper_id的评估结果

        Returns:
            (是否存在, 最新verdict_time)
        """
        try:
            async with self.acquire() as conn:
                query = """
                    SELECT verdict_time
                    FROM reflect_agent_verdict
                    WHERE paper_id = $1
                    ORDER BY verdict_time DESC
                    LIMIT 1
                """
                row = await conn.fetchrow(query, paper_id)
                if row:
                    return True, row["verdict_time"]
                return False, None
        except Exception as e:
            logger.error(f"检查评估结果是否存在失败: {e}")
            raise

    async def check_needs_reevaluation(self, paper_id: str) -> bool:
        """
        检查是否需要重新评估：
        如果agent_audits中有created_at在reflect_agent_verdict的verdict_time之后的记录，
        则需要重新评估。

        Returns:
            是否需要重新评估
        """
        try:
            exists, verdict_time = await self.check_verdict_exists(paper_id)
            if not exists:
                return True  # 没有评估过，需要评估

            async with self.acquire() as conn:
                query = """
                    SELECT COUNT(*) as cnt
                    FROM agent_audits
                    WHERE paper_id = $1 AND created_at > $2
                """
                row = await conn.fetchrow(query, paper_id, verdict_time)
                needs_reeval = row["cnt"] > 0
                if needs_reeval:
                    logger.info(f"论文{paper_id}的审计结果有更新，需要重新评估")
                return needs_reeval
        except Exception as e:
            logger.error(f"检查是否需要重新评估失败: {e}")
            raise

    # ==================== 论文内容读取 ====================
    async def get_paper_content(self, paper_id: str) -> str:
        """
        获取论文内容（用于证据验证）
        从paper_sections表读取

        Args:
            paper_id: 论文ID

        Returns:
            论文完整内容
        """
        try:
            async with self.acquire() as conn:
                query = """
                    SELECT section_id, section_name, section_content
                    FROM paper_sections
                    WHERE paper_id = $1
                    ORDER BY section_id
                """
                rows = await conn.fetch(query, paper_id)

                if not rows:
                    logger.warning(f"未找到论文内容: {paper_id}")
                    return ""

                sections = []
                for row in rows:
                    section_name = row["section_name"]
                    section_content = row["section_content"]
                    sections.append(f"## {section_name}\n\n{section_content}")

                content = "\n\n".join(sections)
                logger.info(f"获取论文内容成功: {paper_id}, 长度={len(content)}")
                return content

        except Exception as e:
            logger.error(f"获取论文内容失败: {e}")
            return ""

    async def get_paper_section_by_name(self, paper_id: str, section_name: str) -> Optional[str]:
        """
        获取论文指定章节内容（用于幻觉过滤中的location验证）

        Args:
            paper_id: 论文ID
            section_name: 章节名称（如"4.2"）

        Returns:
            章节内容，未找到返回None
        """
        try:
            async with self.acquire() as conn:
                query = """
                    SELECT section_content
                    FROM paper_sections
                    WHERE paper_id = $1 AND section_name LIKE $2
                    LIMIT 1
                """
                row = await conn.fetchrow(query, paper_id, f"%{section_name}%")
                if row:
                    return row["section_content"]
                return None
        except Exception as e:
            logger.error(f"获取论文章节内容失败: {e}")
            return None

    # ==================== 保存反思评估结果（reflect_agent_verdict表） ====================
    async def save_verdict(
        self,
        paper_id: str,
        paper_name: Optional[str],
        initial_score: float,
        conflict_resolution: Optional[str],
        conflict_penalty: float,
        final_score: float,
        filtered_suggestions: Optional[Any],
        prioritized_suggestions: Optional[Any],
        final_verdict: str,
        verdict_tags: str = "Executive_Summary,Critical_Fix_List,Score_Calibration"
    ):
        """
        保存反思评估结果到reflect_agent_verdict表

        Args:
            paper_id: 论文ID
            paper_name: 论文题目
            initial_score: 初始综合得分（4个Agent得分和）
            conflict_resolution: 冲突裁决内容（JSON格式）
            conflict_penalty: 冲突扣分值
            final_score: 校准后最终综合得分（100分制）
            filtered_suggestions: 去重后审计建议（JSON格式）
            prioritized_suggestions: 优先级排序后建议（JSON格式）
            final_verdict: 最终定性结论
            verdict_tags: 结果标签
        """
        try:
            # verdict_id必须<=32字符
            # 固定部分: "VER-" (4) + "-" (1) + 时间戳14位 = 19字符，paper部分最多13字符
            pid_short = paper_id if len(paper_id) <= 13 else hashlib.md5(paper_id.encode()).hexdigest()[:8]
            verdict_id = f"VER-{pid_short}-{datetime.now().strftime('%Y%m%d%H%M%S')}"

            async with self.acquire() as conn:
                query = """
                    INSERT INTO reflect_agent_verdict (
                        verdict_id, paper_id, paper_name,
                        initial_score, conflict_resolution, conflict_penalty,
                        final_score, filtered_suggestions, prioritized_suggestions,
                        final_verdict, verdict_tags, verdict_time
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, NOW())
                    ON CONFLICT (paper_id) DO UPDATE SET
                        verdict_id = EXCLUDED.verdict_id,
                        paper_name = EXCLUDED.paper_name,
                        initial_score = EXCLUDED.initial_score,
                        conflict_resolution = EXCLUDED.conflict_resolution,
                        conflict_penalty = EXCLUDED.conflict_penalty,
                        final_score = EXCLUDED.final_score,
                        filtered_suggestions = EXCLUDED.filtered_suggestions,
                        prioritized_suggestions = EXCLUDED.prioritized_suggestions,
                        final_verdict = EXCLUDED.final_verdict,
                        verdict_tags = EXCLUDED.verdict_tags,
                        verdict_time = NOW()
                """
                await conn.execute(
                    query,
                    verdict_id,
                    paper_id,
                    paper_name,
                    initial_score,
                    conflict_resolution,
                    conflict_penalty,
                    final_score,
                    json.dumps(filtered_suggestions, ensure_ascii=False) if filtered_suggestions else None,
                    json.dumps(prioritized_suggestions, ensure_ascii=False) if prioritized_suggestions else None,
                    final_verdict,
                    verdict_tags,
                )
                logger.info(f"保存反思评估结果成功: {verdict_id}, paper_id={paper_id}, 最终得分={final_score}")

        except Exception as e:
            logger.error(f"保存反思评估结果失败: {e}")
            raise

    # 兼容旧接口
    async def save_reflection_result(
        self,
        paper_id: str,
        task_id: str = "",
        final_score: float = 0.0,
        verdict: str = "",
        result_json: Dict[str, Any] = None,
        usage_tokens: int = 0,
        latency_ms: int = 0
    ):
        """兼容旧接口，内部调用save_verdict"""
        result_json = result_json or {}
        conflict_resolution_data = result_json.get("plugin_metadata", {}).get("conflict_resolution", {})
        conflict_penalty = 0.0
        initial_score = final_score + conflict_penalty

        await self.save_verdict(
            paper_id=paper_id,
            paper_name=result_json.get("plugin_metadata", {}).get("paper_title"),
            initial_score=initial_score,
            conflict_resolution=json.dumps(conflict_resolution_data, ensure_ascii=False) if conflict_resolution_data else None,
            conflict_penalty=conflict_penalty,
            final_score=final_score,
            filtered_suggestions=result_json.get("critical_issues", []) + result_json.get("major_issues", []) + result_json.get("minor_issues", []),
            prioritized_suggestions=result_json.get("critical_issues", []),
            final_verdict=verdict,
        )


# 全局数据库管理器实例
db_manager = DatabaseManager()
