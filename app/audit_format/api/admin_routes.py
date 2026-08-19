from __future__ import annotations

import os
import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

import yaml
from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import and_, func, or_, select

from core.database import AgentRule, ExpertComment, GroundTruthIssue, ReviewTask, TaskStatus, db_manager
from core.semantic_check import _embed_text_expert_comment


def _require_admin(x_admin_token: str | None) -> None:
    expected = (os.getenv("ADMIN_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="admin endpoints disabled (set ADMIN_TOKEN)")
    if (x_admin_token or "").strip() != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


def _yaml_dump(content: Any) -> str:
    if isinstance(content, str):
        return content
    return yaml.safe_dump(content, allow_unicode=True)


def _normalize_text(s: Any) -> str:
    return str(s or "").strip()


def _issue_key(issue: Dict[str, Any]) -> tuple:
    issue_type = _normalize_text(issue.get("issue_type"))
    severity = _normalize_text(issue.get("severity"))
    page_num = issue.get("page_num")
    if page_num is None or page_num == "":
        page_val: Any = None
    else:
        try:
            page_val = int(page_num)
        except Exception:
            page_val = str(page_num)
    evidence = _normalize_text(issue.get("evidence")).lower()
    message = _normalize_text(issue.get("message")).lower()
    bbox = issue.get("bbox")
    bbox_key = None
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            bbox_key = tuple(round(float(x), 1) for x in bbox)
        except Exception:
            bbox_key = None
    return (issue_type, severity, page_val, evidence, message, bbox_key)


def _clamp_weight(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except Exception:
        return None
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f


def build_admin_router(rule_engine, layout_analyzer, semantic_checker) -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["Admin"])

    @router.post("/rules/reload")
    async def reload_rules(x_admin_token: str | None = Header(default=None)) -> Dict[str, Any]:
        _require_admin(x_admin_token)
        await rule_engine.reload()
        layout_analyzer.update_rules(rule_engine.rules)
        semantic_checker.update_rules(rule_engine.rules)
        return {"ok": True, "rule_keys": sorted(list(rule_engine.rules.keys()))}

    @router.post("/rules/seed_from_yaml")
    async def seed_rules_from_yaml(
        x_admin_token: str | None = Header(default=None),
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        _require_admin(x_admin_token)
        try:
            with open(rule_engine.config_path, "r", encoding="utf-8") as f:
                yaml_rules = yaml.safe_load(f) or {}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"failed to read rules.yaml: {e}")

        now = datetime.utcnow()
        inserted = 0
        updated = 0
        skipped = 0

        async with db_manager.session() as session:
            for rid, content in (yaml_rules or {}).items():
                rid = _normalize_text(rid)
                if not rid:
                    skipped += 1
                    continue
                content_str = _yaml_dump(content)
                existing = (
                    (await session.execute(select(AgentRule).where(AgentRule.rule_id == rid).limit(1)))
                    .scalars()
                    .first()
                )
                if existing:
                    if overwrite:
                        existing.content = content_str
                        existing.updated_at = now
                        updated += 1
                    else:
                        skipped += 1
                else:
                    session.add(AgentRule(rule_id=rid, content=content_str, updated_at=now))
                    inserted += 1
            await session.commit()

        await rule_engine.reload()
        layout_analyzer.update_rules(rule_engine.rules)
        semantic_checker.update_rules(rule_engine.rules)
        return {"ok": True, "inserted": inserted, "updated": updated, "skipped": skipped}

    @router.get("/rules/db")
    async def list_rules_db(
        x_admin_token: str | None = Header(default=None),
    ) -> Dict[str, Any]:
        _require_admin(x_admin_token)
        async with db_manager.session() as session:
            rows = (await session.execute(select(AgentRule).order_by(AgentRule.rule_id.asc()))).scalars().all()
        return {
            "count": len(rows),
            "rules": [{"rule_id": r.rule_id, "updated_at": (r.updated_at.isoformat() if r.updated_at else None)} for r in rows],
        }

    @router.put("/rules/{rule_id}")
    async def upsert_rule_db(
        rule_id: str,
        payload: Dict[str, Any],
        x_admin_token: str | None = Header(default=None),
    ) -> Dict[str, Any]:
        _require_admin(x_admin_token)
        rid = _normalize_text(rule_id)
        if not rid:
            raise HTTPException(status_code=400, detail="missing rule_id")
        content = payload.get("content")
        if content is None:
            raise HTTPException(status_code=400, detail="missing payload.content")
        content_str = _yaml_dump(content)
        now = datetime.utcnow()

        async with db_manager.session() as session:
            existing = (
                (await session.execute(select(AgentRule).where(AgentRule.rule_id == rid).limit(1)))
                .scalars()
                .first()
            )
            if existing:
                existing.content = content_str
                existing.updated_at = now
            else:
                session.add(AgentRule(rule_id=rid, content=content_str, updated_at=now))
            await session.commit()

        await rule_engine.reload()
        layout_analyzer.update_rules(rule_engine.rules)
        semantic_checker.update_rules(rule_engine.rules)
        return {"ok": True, "rule_id": rid}

    @router.get("/expert_comments")
    async def list_expert_comments(
        x_admin_token: str | None = Header(default=None),
        metric_id: Optional[str] = None,
        active: Optional[bool] = None,
        q: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        _require_admin(x_admin_token)
        limit_val = max(1, min(200, int(limit or 50)))
        predicates = []
        if metric_id:
            predicates.append(ExpertComment.metric_id == metric_id)
        if active is not None:
            predicates.append(ExpertComment.active.is_(active))
        if q:
            qs = f"%{q.strip()}%"
            predicates.append(or_(ExpertComment.text.ilike(qs), ExpertComment.rule_title.ilike(qs), ExpertComment.rule_code.ilike(qs)))

        stmt = select(ExpertComment).order_by(ExpertComment.comment_id.desc()).limit(limit_val)
        if predicates:
            stmt = stmt.where(and_(*predicates))

        async with db_manager.session() as session:
            rows = (await session.execute(stmt)).scalars().all()

        return {
            "count": len(rows),
            "items": [
                {
                    "comment_id": r.comment_id,
                    "metric_id": r.metric_id,
                    "rule_code": r.rule_code,
                    "rule_category": r.rule_category,
                    "severity": r.severity,
                    "weight": r.weight,
                    "active": r.active,
                    "source": r.source,
                    "text": r.text,
                }
                for r in rows
            ],
        }

    @router.post("/expert_comments/upsert")
    async def upsert_expert_comment(
        payload: Dict[str, Any],
        x_admin_token: str | None = Header(default=None),
    ) -> Dict[str, Any]:
        _require_admin(x_admin_token)
        comment_id = payload.get("comment_id")
        metric_id = _normalize_text(payload.get("metric_id"))
        text_val = _normalize_text(payload.get("text"))
        if not metric_id and not comment_id:
            raise HTTPException(status_code=400, detail="missing metric_id or comment_id")
        if not text_val:
            raise HTTPException(status_code=400, detail="missing text")

        embed = bool(payload.get("embed", True))
        now = datetime.utcnow()

        async with db_manager.session() as session:
            existing = None
            if comment_id:
                existing = (
                    (await session.execute(select(ExpertComment).where(ExpertComment.comment_id == comment_id).limit(1)))
                    .scalars()
                    .first()
                )
            if not existing and metric_id:
                existing = (
                    (await session.execute(
                        select(ExpertComment)
                        .where(ExpertComment.metric_id == metric_id, ExpertComment.text == text_val)
                        .limit(1)
                    ))
                    .scalars()
                    .first()
                )

            embedding = None
            if embed:
                embedding = await asyncio.to_thread(_embed_text_expert_comment, f"{metric_id}\n{text_val}")

            if existing:
                existing.metric_id = metric_id or existing.metric_id
                existing.text = text_val
                existing.rule_code = _normalize_text(payload.get("rule_code")) or existing.rule_code
                existing.rule_category = _normalize_text(payload.get("rule_category")) or existing.rule_category
                existing.rule_title = (
                    _normalize_text(payload.get("rule_title"))
                    or existing.rule_title
                    or metric_id
                    or existing.rule_code
                    or "UNKNOWN"
                )
                existing.rule_text = _normalize_text(payload.get("rule_text")) or existing.rule_text or text_val
                existing.indicator_name = _normalize_text(payload.get("indicator_name")) or existing.indicator_name or metric_id or "UNKNOWN"
                existing.operator = _normalize_text(payload.get("operator")) or existing.operator or "N/A"
                existing.is_hard_rule = bool(payload.get("is_hard_rule")) if payload.get("is_hard_rule") is not None else (existing.is_hard_rule if existing.is_hard_rule is not None else False)
                existing.evidence_pattern = _normalize_text(payload.get("evidence_pattern")) or existing.evidence_pattern or ""
                existing.severity = _normalize_text(payload.get("severity")) or existing.severity
                w = _clamp_weight(payload.get("weight"))
                existing.weight = w if w is not None else existing.weight
                existing.source = _normalize_text(payload.get("source")) or existing.source
                if payload.get("active") is not None:
                    existing.active = bool(payload.get("active"))
                if embedding is not None:
                    existing.embedding = embedding
                existing.updated_at = now
                obj = existing
            else:
                obj = ExpertComment(
                    metric_id=metric_id,
                    text=text_val,
                    rule_code=_normalize_text(payload.get("rule_code")),
                    rule_category=_normalize_text(payload.get("rule_category")),
                    rule_title=_normalize_text(payload.get("rule_title")) or metric_id or _normalize_text(payload.get("rule_code")) or "UNKNOWN",
                    rule_text=_normalize_text(payload.get("rule_text")) or text_val,
                    indicator_name=_normalize_text(payload.get("indicator_name")) or metric_id or "UNKNOWN",
                    operator=_normalize_text(payload.get("operator")) or "N/A",
                    is_hard_rule=bool(payload.get("is_hard_rule")) if payload.get("is_hard_rule") is not None else False,
                    evidence_pattern=_normalize_text(payload.get("evidence_pattern")) or "",
                    severity=_normalize_text(payload.get("severity")),
                    weight=_clamp_weight(payload.get("weight")),
                    source=_normalize_text(payload.get("source")) or "admin",
                    active=bool(payload.get("active")) if payload.get("active") is not None else True,
                    embedding=embedding,
                    created_at=now,
                    updated_at=now,
                )
                session.add(obj)

            await session.commit()
            await session.refresh(obj)

        return {"ok": True, "comment_id": obj.comment_id}

    @router.post("/expert_comments/reembed")
    async def reembed_expert_comments(
        payload: Dict[str, Any],
        x_admin_token: str | None = Header(default=None),
    ) -> Dict[str, Any]:
        _require_admin(x_admin_token)
        limit = int(payload.get("limit", 200) or 200)
        limit_val = max(1, min(2000, limit))
        require_text = bool(payload.get("require_text", True))

        predicates = []
        predicates.append(or_(ExpertComment.embedding.is_(None)))
        if require_text:
            predicates.append(ExpertComment.text.is_not(None))
            predicates.append(func.length(func.trim(ExpertComment.text)) > 0)

        async with db_manager.session() as session:
            rows = (
                (await session.execute(select(ExpertComment).where(and_(*predicates)).limit(limit_val)))
                .scalars()
                .all()
            )
            updated = 0
            now = datetime.utcnow()
            for r in rows:
                metric = _normalize_text(r.metric_id)
                text_val = _normalize_text(r.text)
                if require_text and not text_val:
                    continue
                r.embedding = await asyncio.to_thread(_embed_text_expert_comment, f"{metric}\n{text_val}")
                r.updated_at = now
                updated += 1
            await session.commit()

        return {"ok": True, "updated": updated}

    @router.post("/ground_truth/batch_upsert")
    async def upsert_ground_truth_batch(
        payload: Dict[str, Any],
        x_admin_token: str | None = Header(default=None),
    ) -> Dict[str, Any]:
        _require_admin(x_admin_token)
        items = payload.get("items") or []
        if not isinstance(items, list) or not items:
            raise HTTPException(status_code=400, detail="missing items[]")

        now = datetime.utcnow()
        inserted = 0
        skipped = 0

        async with db_manager.session() as session:
            for raw in items:
                if not isinstance(raw, dict):
                    skipped += 1
                    continue
                issue_type = _normalize_text(raw.get("issue_type"))
                if not issue_type:
                    skipped += 1
                    continue
                sample_id = _normalize_text(raw.get("sample_id")) or None
                paper_id = raw.get("paper_id")
                if isinstance(paper_id, str) and paper_id.strip():
                    try:
                        paper_id = UUID(paper_id.strip())
                    except Exception:
                        paper_id = None
                chunk_id = _normalize_text(raw.get("chunk_id")) or None
                severity = _normalize_text(raw.get("severity")) or None
                message = _normalize_text(raw.get("message")) or None
                evidence = _normalize_text(raw.get("evidence")) or None
                page_num = raw.get("page_num")
                bbox = raw.get("bbox")
                source = _normalize_text(raw.get("source")) or None

                existing = None
                stmt = select(GroundTruthIssue).where(GroundTruthIssue.issue_type == issue_type).limit(1)
                if sample_id:
                    stmt = stmt.where(GroundTruthIssue.sample_id == sample_id)
                if paper_id:
                    stmt = stmt.where(GroundTruthIssue.paper_id == paper_id)
                if chunk_id:
                    stmt = stmt.where(GroundTruthIssue.chunk_id == chunk_id)
                if evidence:
                    stmt = stmt.where(GroundTruthIssue.evidence == evidence)
                if message:
                    stmt = stmt.where(GroundTruthIssue.message == message)
                existing = (await session.execute(stmt)).scalars().first()

                if existing:
                    existing.severity = severity or existing.severity
                    existing.page_num = page_num if page_num is not None else existing.page_num
                    existing.bbox = bbox if bbox is not None else existing.bbox
                    existing.source = source or existing.source
                    existing.updated_at = now
                else:
                    obj = GroundTruthIssue(
                        sample_id=sample_id,
                        paper_id=paper_id,
                        chunk_id=chunk_id,
                        issue_type=issue_type,
                        severity=severity,
                        message=message,
                        evidence=evidence,
                        page_num=page_num,
                        bbox=bbox,
                        source=source,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(obj)
                    inserted += 1

            await session.commit()

        return {"ok": True, "inserted": inserted, "skipped": skipped}

    @router.get("/eval/latest")
    async def eval_latest(
        x_admin_token: str | None = Header(default=None),
        paper_id: Optional[str] = None,
        chunk_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        _require_admin(x_admin_token)
        if not task_id and not (paper_id and chunk_id):
            raise HTTPException(status_code=400, detail="provide task_id or (paper_id, chunk_id)")

        async with db_manager.session() as session:
            task_stmt = select(ReviewTask).where(ReviewTask.status == TaskStatus.SUCCESS).order_by(ReviewTask.created_at.desc())
            if task_id:
                task_stmt = task_stmt.where(ReviewTask.task_id == task_id)
            paper_uuid = None
            if paper_id:
                try:
                    paper_uuid = UUID(paper_id)
                except Exception:
                    paper_uuid = None
            if paper_uuid:
                task_stmt = task_stmt.where(ReviewTask.paper_id == paper_uuid)
            if chunk_id:
                task_stmt = task_stmt.where(ReviewTask.chunk_id == chunk_id)
            task = (await session.execute(task_stmt.limit(1))).scalars().first()
            if not task or not isinstance(task.result_json, dict):
                raise HTTPException(status_code=404, detail="no task result_json found")

            pred_items = []
            dbg = task.result_json.get("debug") if isinstance(task.result_json.get("debug"), dict) else {}
            raw_pred = dbg.get("issues") if isinstance(dbg, dict) else None
            if isinstance(raw_pred, list):
                pred_items = [x for x in raw_pred if isinstance(x, dict)]

            gt_stmt = select(GroundTruthIssue)
            if paper_uuid and chunk_id:
                gt_stmt = gt_stmt.where(GroundTruthIssue.paper_id == paper_uuid, GroundTruthIssue.chunk_id == chunk_id)
            elif task.paper_id and task.chunk_id:
                gt_stmt = gt_stmt.where(GroundTruthIssue.paper_id == task.paper_id, GroundTruthIssue.chunk_id == task.chunk_id)
            gt_rows = (await session.execute(gt_stmt)).scalars().all()

        gt_items = []
        for r in gt_rows:
            gt_items.append(
                {
                    "issue_type": r.issue_type,
                    "severity": r.severity,
                    "page_num": r.page_num,
                    "evidence": r.evidence,
                    "message": r.message,
                    "bbox": r.bbox,
                }
            )

        pred_keys = {_issue_key(i) for i in pred_items}
        gt_keys = {_issue_key(i) for i in gt_items}

        tp = len(pred_keys & gt_keys)
        fp = len(pred_keys - gt_keys)
        fn = len(gt_keys - pred_keys)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        return {
            "task_id": task.task_id if task else None,
            "paper_id": str(task.paper_id) if task else None,
            "chunk_id": task.chunk_id if task else None,
            "pred_count": len(pred_keys),
            "gt_count": len(gt_keys),
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }

    return router
