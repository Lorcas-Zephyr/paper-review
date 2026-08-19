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
            updated_at TIMESTAMP NOT NULL
        );
        """
        try:
            conn = self._connect()
            cur = conn.cursor()
            cur.execute(sql)
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
            request_id, paper_id, overall_status, progress, aggregated_report, message, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (request_id) DO UPDATE SET
            paper_id = EXCLUDED.paper_id,
            overall_status = EXCLUDED.overall_status,
            progress = EXCLUDED.progress,
            aggregated_report = EXCLUDED.aggregated_report,
            message = EXCLUDED.message,
            updated_at = EXCLUDED.updated_at;
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
        SELECT request_id, paper_id, overall_status, progress, aggregated_report, message, created_at, updated_at
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
