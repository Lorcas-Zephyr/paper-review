import json
import logging
import os
from typing import List

import asyncpg

logger = logging.getLogger(__name__)


class DatabaseClient:
    def __init__(self):
        self.db_config = {
            "host": os.getenv("DB_HOST", "127.0.0.1"),
            "port": int(os.getenv("DB_PORT", "5432")),
            "user": os.getenv("DB_USER", "postgres"),
            "password": os.getenv("DB_PASSWORD", ""),
            "database": os.getenv("DB_NAME", "postgres")
        }
        self.pool = None

    async def connect(self):
        """创建数据库连接池"""
        if not self.pool:
            try:
                self.pool = await asyncpg.create_pool(
                    **self.db_config,
                    init=self._init_connection
                )
                logger.info("数据库连接池创建成功")
            except Exception as e:
                logger.error("数据库连接失败: %s", str(e))
                raise

    @staticmethod
    async def _init_connection(conn):
        """初始化连接：注册JSON编解码器，解决asyncpg传dict参数的问题"""
        await conn.set_type_codec(
            'jsonb',
            encoder=json.dumps,
            decoder=json.loads,
            schema='pg_catalog'
        )
        await conn.set_type_codec(
            'json',
            encoder=json.dumps,
            decoder=json.loads,
            schema='pg_catalog'
        )

    async def disconnect(self):
        """关闭数据库连接池"""
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("数据库连接池已关闭")

    async def get_agent_results(self, paper_id: str) -> List[dict]:
        """获取特定论文的所有Agent结果（字段名对齐review_tasks表定义）

        review_tasks表关键字段：
          task_id, paper_id, chunk_id, agent_name, agent_version,
          status, score, audit_level, result_json, error_msg,
          usage_tokens, latency_ms, created_at, updated_at
        """
        if not self.pool:
            await self.connect()

        query = """
        SELECT task_id, agent_name, agent_version,
               score, audit_level, result_json,
               usage_tokens, latency_ms
        FROM review_tasks
        WHERE paper_id = $1 AND status = 'SUCCESS'
        ORDER BY created_at DESC
        """

        try:
            rows = await self.pool.fetch(query, paper_id)
            results = []
            for row in rows:
                # 将DB行转换为文档协议格式的Agent结果
                result_json = row['result_json'] or {}
                results.append({
                    "request_id": row['task_id'],
                    "agent_info": {
                        "name": row['agent_name'],
                        "version": row['agent_version'] or "v1.0"
                    },
                    "result": {
                        "score": row['score'] or result_json.get('score', 70),
                        "audit_level": row['audit_level'] or result_json.get('audit_level', 'Info'),
                        "comment": result_json.get('comment', ''),
                        "suggestion": result_json.get('suggestion', ''),
                        "tags": result_json.get('tags', [])
                    },
                    "usage": {
                        "tokens": row['usage_tokens'] or 0,
                        "latency_ms": row['latency_ms'] or 0
                    }
                })
            logger.info("查询到%d条Agent结果, paper_id=%s", len(results), paper_id)
            return results
        except Exception as e:
            logger.error("查询Agent结果失败: %s", str(e))
            return []

    async def get_paper_content(self, paper_id: str) -> str:
        """获取论文原始内容（用于上下文）

        paper_sections表字段：paper_id, section_name, content
        """
        if not self.pool:
            await self.connect()

        query = """
        SELECT string_agg(content, E'\\n\\n' ORDER BY section_name) as full_content
        FROM paper_sections
        WHERE paper_id = $1
        """

        try:
            row = await self.pool.fetchrow(query, paper_id)
            content = row['full_content'] if row and row['full_content'] else ""
            logger.info("获取论文内容, paper_id=%s, 长度=%d", paper_id, len(content))
            return content
        except Exception as e:
            logger.error("查询论文内容失败: %s", str(e))
            return ""

    async def save_resolution_result(self, request_id: str, resolution_data: dict, usage: dict):
        """保存冲突裁决结果（字段名对齐review_tasks表定义）"""
        if not self.pool:
            await self.connect()

        query = """
        INSERT INTO review_tasks (
            task_id, paper_id, agent_name, agent_version,
            result_json, status, score, audit_level,
            usage_tokens, latency_ms
        ) VALUES (
            $1, $2, $3, $4,
            $5, 'SUCCESS', $6, $7,
            $8, $9
        )
        ON CONFLICT (task_id) DO UPDATE SET
            result_json = EXCLUDED.result_json,
            score = EXCLUDED.score,
            audit_level = EXCLUDED.audit_level,
            usage_tokens = EXCLUDED.usage_tokens,
            latency_ms = EXCLUDED.latency_ms,
            status = EXCLUDED.status,
            updated_at = NOW()
        """

        try:
            resolved_issues = resolution_data.get('resolved_issues', [])
            avg_score = (
                sum(issue.get('score', 70) for issue in resolved_issues)
                / max(1, len(resolved_issues))
            )
            highest_level = max(
                (issue.get('final_level', 'Info') for issue in resolved_issues),
                key=lambda x: {'Info': 0, 'Warning': 1, 'Critical': 2}.get(x, 0),
                default='Info'
            )

            await self.pool.execute(
                query,
                request_id,
                resolution_data.get('paper_id', ''),
                'ReflectionJudge_ConflictResolver',
                'v1.0',
                resolution_data,  # JSON编解码器会自动序列化dict
                int(avg_score),
                highest_level,
                usage.get('tokens', 0),
                usage.get('latency_ms', 0)
            )
            logger.info("裁决结果已保存, request_id=%s", request_id)
            return True
        except Exception as e:
            logger.error("保存裁决结果失败: %s", str(e))
            return False
