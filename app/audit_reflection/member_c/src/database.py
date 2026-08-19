import asyncpg
from typing import List, Optional
from src.config import settings
from src.models import AgentResult

class Database:
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(
            host=settings.database.host,
            port=settings.database.port,
            user=settings.database.user,
            password=settings.database.password,
            database=settings.database.database,
            min_size=settings.database.min_size,
            max_size=settings.database.max_size,
        )

    async def close(self):
        if self.pool:
            await self.pool.close()

    async def get_all_agent_results(self, paper_id: str) -> List[AgentResult]:
        query = """
        SELECT agent_name, result_json, score, audit_level
        FROM review_tasks
        WHERE paper_id = $1 AND status = 'SUCCESS'
        ORDER BY created_at DESC
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, paper_id)
        results = []
        for row in rows:
            # 假设 result_json 已经是 dict
            results.append(AgentResult(
                agent_name=row['agent_name'],
                result_json=row['result_json'],
                score=row['score'],
                audit_level=row['audit_level'],
                evidence_quotes=row['result_json'].get('evidence_quotes', [])
            ))
        return results

    async def get_paper_content(self, paper_id: str) -> List[dict]:
        query = """
        SELECT section_name, content
        FROM paper_sections
        WHERE paper_id = $1
        ORDER BY section_order
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, paper_id)
        return [dict(r) for r in rows]

    async def save_reflection_result(self, paper_id: str, result: ReflectionResult):
        query = """
        INSERT INTO reflection_results
        (id, paper_id, final_score, needs_human_review, review_reason, mentor_dialogue, dialogue_quality_score, plugin_metadata, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
        """
        async with self.pool.acquire() as conn:
            await conn.execute(
                query,
                result.id,  # 需要生成 UUID
                paper_id,
                result.final_score,
                result.needs_human_review,
                result.human_review_reason,
                result.mentor_dialogue.dict() if result.mentor_dialogue else None,
                result.mentor_dialogue.quality_score if result.mentor_dialogue else None,
                result.plugin_metadata
            )
