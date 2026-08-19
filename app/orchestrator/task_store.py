from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

import psycopg2
from psycopg2.extras import Json, RealDictCursor

logger = logging.getLogger(__name__)


class PostgresTaskStore:
    """Persistent task store for orchestrator status queries."""

    def __init__(self) -> None:
        self._enabled = True
        self._dsn = {
            "dbname": os.getenv("POSTGRES_DB", "postgres"),
            "user": os.getenv("POSTGRES_USER", "postgres"),
            "password": os.getenv("POSTGRES_PASSWORD", ""),
            "host": os.getenv("POSTGRES_HOST", "127.0.0.1"),
            "port": int(os.getenv("POSTGRES_PORT", "5432")),
        }
        self._ensure_schema()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _connect(self):
        return psycopg2.connect(**self._dsn)

    def _ensure_schema(self) -> None:
        sql = """
        CREATE TABLE IF NOT EXISTS orchestrator_tasks (
            request_id TEXT PRIMARY KEY,
            paper_id TEXT NOT NULL,
            overall_status TEXT NOT NULL,
            progress JSONB NOT NULL DEFAULT '{}'::jsonb,
            aggregated_report JSONB NULL,
            message TEXT NULL,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL,
            planner_status JSONB NOT NULL DEFAULT '{}'::jsonb,
            plan JSONB NULL,
            round INTEGER NOT NULL DEFAULT 0,
            consultation_count INTEGER NOT NULL DEFAULT 0,
            scheduler_backend TEXT NOT NULL DEFAULT 'ai_planner_runtime'
        );
        """
        try:
            conn = self._connect()
            cur = conn.cursor()
            cur.execute(sql)
            conn.commit()
            # Existing deployments created before the AI scheduler need an
            # additive migration.  ALTER TABLE is idempotent and non-destructive.
            for statement in (
                "ALTER TABLE orchestrator_tasks ADD COLUMN IF NOT EXISTS planner_status JSONB NOT NULL DEFAULT '{}'::jsonb",
                "ALTER TABLE orchestrator_tasks ADD COLUMN IF NOT EXISTS plan JSONB NULL",
                "ALTER TABLE orchestrator_tasks ADD COLUMN IF NOT EXISTS round INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE orchestrator_tasks ADD COLUMN IF NOT EXISTS consultation_count INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE orchestrator_tasks ADD COLUMN IF NOT EXISTS scheduler_backend TEXT NOT NULL DEFAULT 'ai_planner_runtime'",
            ):
                cur.execute(statement)
            conn.commit()
            cur.close()
            conn.close()
        except Exception as exc:
            self._enabled = False
            logger.warning("Task store init failed, fallback to in-memory only: %s", exc)

    def upsert_task(self, task: Dict[str, Any]) -> None:
        if not self._enabled:
            return
        sql = """
        INSERT INTO orchestrator_tasks (
            request_id, paper_id, overall_status, progress, aggregated_report, message, created_at, updated_at,
            planner_status, plan, round, consultation_count, scheduler_backend
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (request_id) DO UPDATE SET
            paper_id = EXCLUDED.paper_id,
            overall_status = EXCLUDED.overall_status,
            progress = EXCLUDED.progress,
            aggregated_report = EXCLUDED.aggregated_report,
            message = EXCLUDED.message,
            updated_at = EXCLUDED.updated_at,
            planner_status = EXCLUDED.planner_status,
            plan = EXCLUDED.plan,
            round = EXCLUDED.round,
            consultation_count = EXCLUDED.consultation_count,
            scheduler_backend = EXCLUDED.scheduler_backend;
        """
        try:
            conn = self._connect()
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    task["request_id"],
                    task.get("paper_id", "processing"),
                    task.get("overall_status"),
                    Json(task.get("progress", {})),
                    Json(task.get("aggregated_report")) if task.get("aggregated_report") is not None else None,
                    task.get("message"),
                    self._to_datetime(task.get("created_at")),
                    self._to_datetime(task.get("updated_at")),
                    Json(task.get("planner_status", {})),
                    Json(task.get("plan")) if task.get("plan") is not None else None,
                    int(task.get("round", 0) or 0),
                    int(task.get("consultation_count", 0) or 0),
                    task.get("scheduler_backend", "ai_planner_runtime"),
                ),
            )
            conn.commit()
            cur.close()
            conn.close()
        except Exception as exc:
            logger.warning("Task store upsert failed: %s", exc)

    def get_task(self, request_id: str) -> Optional[Dict[str, Any]]:
        if not self._enabled:
            return None
        sql = """
        SELECT request_id, paper_id, overall_status, progress, aggregated_report, message, created_at, updated_at,
               planner_status, plan, round, consultation_count, scheduler_backend
        FROM orchestrator_tasks
        WHERE request_id = %s
        """
        try:
            conn = self._connect()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(sql, (request_id,))
            row = cur.fetchone()
            cur.close()
            conn.close()
            if not row:
                return None
            task = dict(row)
            task["progress"] = task.get("progress") or {}
            return task
        except Exception as exc:
            logger.warning("Task store query failed: %s", exc)
            return None

    @staticmethod
    def _to_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return datetime.now()
