import asyncio
import os
import sys
from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import select

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

from core.database import ExpertComment, db_manager  # noqa: E402
from core.semantic_check import _embed_text_expert_comment  # noqa: E402


def _seed_rows() -> List[Dict[str, Any]]:
    return [
        {
            "metric_id": "CITATION_STYLE",
            "rule_code": "citation_check.style",
            "rule_category": "Citation",
            "rule_title": "引用风格一致性",
            "indicator_name": "CITATION_STYLE",
            "operator": "N/A",
            "severity": "Warning",
            "weight": 0.2,
            "text": "统一正文引用风格（如全篇使用 IEEE 的 [1] 形式），避免同一文档中混用 (Author, Year) 与 [1]。",
        },
        {
            "metric_id": "CITATION_PLACEHOLDER",
            "rule_code": "citation_check.placeholder",
            "rule_category": "Citation",
            "rule_title": "引用占位符清理",
            "indicator_name": "CITATION_PLACEHOLDER",
            "operator": "N/A",
            "severity": "Critical",
            "weight": 0.8,
            "text": "清除引用占位符（如“[?]”“Error! Reference source not found”），并补齐对应文献条目。",
        },
        {
            "metric_id": "PUNCTUATION_MIXED",
            "rule_code": "punctuation_check.allow_mixed_punctuation",
            "rule_category": "Punctuation",
            "rule_title": "中英文标点混用",
            "indicator_name": "PUNCTUATION_MIXED",
            "operator": "N/A",
            "severity": "Warning",
            "weight": 0.3,
            "text": "避免中英文标点混用：中文语境用中文逗号/句号；英文引用编号使用英文方括号 [1]。",
        },
        {
            "metric_id": "TERMINOLOGY_FIRST_MENTION",
            "rule_code": "terminology_check.first_mention",
            "rule_category": "Terminology",
            "rule_title": "缩写首次出现需释义",
            "indicator_name": "TERMINOLOGY_FIRST_MENTION",
            "operator": "N/A",
            "severity": "Info",
            "weight": 0.1,
            "text": "缩略语首次出现应给出全称，例如“Large Language Model (LLM)”。后文再使用缩写保持一致。",
        },
        {
            "metric_id": "HEADING_CONTINUITY",
            "rule_code": "heading_check.continuity_check",
            "rule_category": "Structure",
            "rule_title": "标题编号连续性",
            "indicator_name": "HEADING_CONTINUITY",
            "operator": "N/A",
            "severity": "Warning",
            "weight": 0.3,
            "text": "检查标题编号连续性：1.1 后不应直接跳到 1.3；子标题层级不要越级（例如 2 -> 2.1.1）。",
        },
        {
            "metric_id": "FIGURE_CAPTION_POS",
            "rule_code": "figure_table_check.caption_requirement",
            "rule_category": "FigureTable",
            "rule_title": "图表题位置与编号",
            "indicator_name": "FIGURE_CAPTION_POS",
            "operator": "N/A",
            "severity": "Warning",
            "weight": 0.3,
            "text": "图题应位于图下方、表题应位于表上方，并保持“图1/表1”编号连续且与正文引用一致。",
        },
        {
            "metric_id": "FORMULA_REFERENCE",
            "rule_code": "formula_check.check_reference",
            "rule_category": "Formula",
            "rule_title": "公式引用一致性",
            "indicator_name": "FORMULA_REFERENCE",
            "operator": "N/A",
            "severity": "Warning",
            "weight": 0.2,
            "text": "带编号的公式应在正文中被引用（如“由式(3)可得…”）；避免出现编号公式完全未被提及。",
        },
    ]


async def seed(overwrite: bool = False, embed: bool = True) -> Dict[str, int]:
    now = datetime.utcnow()
    rows = _seed_rows()
    inserted = 0
    updated = 0
    skipped = 0

    try:
        async with db_manager.session() as session:
            for r in rows:
                metric_id = str(r.get("metric_id") or "").strip()
                text_val = str(r.get("text") or "").strip()
                if not metric_id or not text_val:
                    skipped += 1
                    continue

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
                    if overwrite:
                        existing.rule_code = r.get("rule_code")
                        existing.rule_category = r.get("rule_category")
                        existing.rule_title = r.get("rule_title") or metric_id
                        existing.rule_text = text_val
                        existing.indicator_name = r.get("indicator_name") or metric_id
                        existing.operator = r.get("operator") or "N/A"
                        existing.severity = r.get("severity")
                        existing.weight = r.get("weight")
                        existing.is_hard_rule = False
                        existing.evidence_pattern = existing.evidence_pattern or ""
                        existing.source = "seed"
                        existing.active = True
                        existing.updated_at = now
                        if existing.created_at is None:
                            existing.created_at = now
                        if embedding is not None:
                            existing.embedding = embedding
                        updated += 1
                    else:
                        if existing.active is None:
                            existing.active = True
                        if existing.source is None:
                            existing.source = "seed"
                        if existing.updated_at is None:
                            existing.updated_at = now
                        if existing.created_at is None:
                            existing.created_at = now
                        if existing.embedding is None and embedding is not None:
                            existing.embedding = embedding
                        if existing.is_hard_rule is None:
                            existing.is_hard_rule = False
                        if existing.evidence_pattern is None:
                            existing.evidence_pattern = ""
                        updated += 1
                    continue

                obj = ExpertComment(
                    metric_id=metric_id,
                    text=text_val,
                    rule_code=r.get("rule_code"),
                    rule_category=r.get("rule_category"),
                    rule_title=r.get("rule_title") or metric_id,
                    rule_text=text_val,
                    indicator_name=r.get("indicator_name") or metric_id,
                    operator=r.get("operator") or "N/A",
                    severity=r.get("severity"),
                    weight=r.get("weight"),
                    is_hard_rule=False,
                    evidence_pattern="",
                    source="seed",
                    active=True,
                    created_at=now,
                    updated_at=now,
                    embedding=embedding,
                )
                session.add(obj)
                inserted += 1

            await session.commit()
        return {"inserted": inserted, "updated": updated, "skipped": skipped}
    finally:
        await db_manager.close()


def main() -> int:
    overwrite = os.getenv("SEED_OVERWRITE", "0") == "1"
    embed = os.getenv("SEED_EMBED", "1") != "0"
    result = asyncio.run(seed(overwrite=overwrite, embed=embed))
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
