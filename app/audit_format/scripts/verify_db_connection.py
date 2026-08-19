import argparse
import asyncio
import os
import socket
import sys
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import quote_plus, urlsplit, unquote


AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


def _mask_db_url(url: str) -> str:
    try:
        normalized = url.replace("postgresql+asyncpg://", "postgresql://", 1)
        s = urlsplit(normalized)
        netloc = s.hostname or ""
        if s.port:
            netloc = f"{netloc}:{s.port}"
        if s.username:
            netloc = f"{s.username}:***@{netloc}"
        return f"postgresql+asyncpg://{netloc}{s.path}"
    except Exception:
        return "postgresql+asyncpg://***"


def _parse_db_url(url: str) -> Tuple[str, str, str, int, str]:
    normalized = (url or "").strip().replace("postgresql+asyncpg://", "postgresql://", 1)
    s = urlsplit(normalized)
    user = unquote(s.username or "")
    password = unquote(s.password or "")
    host = s.hostname or ""
    port = int(s.port or 5432)
    name = (s.path or "").lstrip("/")
    return user, password, host, port, name


def _build_db_url_from_env() -> Optional[str]:
    host = os.getenv("DB_HOST", "").strip()
    port = os.getenv("DB_PORT", "").strip()
    name = os.getenv("DB_NAME", "").strip()
    user = os.getenv("DB_USER", "").strip()
    password = os.getenv("DB_PASSWORD", "")

    if not (host and name and user and password):
        return None

    port_val = port or "5432"
    return (
        "postgresql+asyncpg://"
        + quote_plus(user)
        + ":"
        + quote_plus(password)
        + "@"
        + host
        + ":"
        + port_val
        + "/"
        + name
    )


def _tcp_probe(host: str, port: int, timeout_sec: float = 2.5) -> Optional[str]:
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            return None
    except Exception as e:
        return f"{type(e).__name__}: {e}"


async def _sql_probe(database_url: str, write_check: bool) -> Dict[str, Any]:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text

    engine = create_async_engine(database_url, echo=False, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            row = (await conn.execute(text("SELECT current_user, current_database(), version()"))).first()
            current_user = row[0] if row else None
            current_db = row[1] if row else None
            version = row[2] if row else None

            can_select_review_tasks = False
            can_insert_review_tasks = False
            tables = (
                await conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname='public' ORDER BY tablename LIMIT 50"
                    )
                )
            ).scalars().all()

            schema_create = (
                await conn.execute(text("SELECT has_schema_privilege(current_user, 'public', 'CREATE')"))
            ).scalar_one()

            checks = []
            for t in ["review_tasks", "paper_sections", "expert_comments", "agent_audit_result", "agent_rules", "ground_truth_issues"]:
                has_table = (
                    await conn.execute(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename=:t)"
                        ),
                        {"t": t},
                    )
                ).scalar_one()
                if not has_table:
                    checks.append({"table": t, "exists": False})
                    continue
                priv = (
                    await conn.execute(
                        text(
                            "SELECT "
                            "has_table_privilege(current_user, :tbl, 'SELECT') AS can_select, "
                            "has_table_privilege(current_user, :tbl, 'INSERT') AS can_insert, "
                            "has_table_privilege(current_user, :tbl, 'UPDATE') AS can_update, "
                            "has_table_privilege(current_user, :tbl, 'DELETE') AS can_delete"
                        ),
                        {"tbl": t},
                    )
                ).mappings().first()
                row_priv = {"table": t, "exists": True, **dict(priv or {})}
                checks.append(row_priv)
                if t == "review_tasks":
                    can_select_review_tasks = bool(row_priv.get("can_select"))
                    can_insert_review_tasks = bool(row_priv.get("can_insert"))

            write_probe = {"attempted": False}
            if write_check:
                write_probe["attempted"] = True
                try:
                    has_review_tasks = any(c.get("table") == "review_tasks" and c.get("exists") for c in checks)
                    if has_review_tasks and can_select_review_tasks:
                        count_val = (
                            await conn.execute(text("SELECT COUNT(*) FROM review_tasks"))
                        ).scalar_one()
                        write_probe["review_tasks_count"] = int(count_val or 0)

                        if not can_insert_review_tasks:
                            write_probe["skipped_reason"] = "no_insert_privilege_on_review_tasks"
                        else:
                            try:
                                await conn.rollback()
                            except Exception:
                                pass
                            tx = await conn.begin()
                            try:
                                task_id = f"db_probe_{uuid.uuid4().hex[:12]}"
                                paper_id = str(uuid.uuid4())
                                await conn.execute(
                                    text(
                                        "INSERT INTO review_tasks (task_id, paper_id, chunk_id, agent_name, agent_version) "
                                        "VALUES (:task_id, CAST(:paper_id AS uuid), :chunk_id, :agent_name, :agent_version)"
                                    ),
                                    {
                                        "task_id": task_id,
                                        "paper_id": paper_id,
                                        "chunk_id": "db_probe",
                                        "agent_name": "db_probe",
                                        "agent_version": "db_probe",
                                    },
                                )
                                await tx.rollback()
                                write_probe["mode"] = "review_tasks"
                                write_probe["insert_rollback"] = True
                            except Exception as e:
                                try:
                                    await tx.rollback()
                                except Exception:
                                    pass
                                write_probe["insert_rollback"] = False
                                write_probe["error"] = f"{type(e).__name__}: {e}"
                    else:
                        try:
                            await conn.rollback()
                        except Exception:
                            pass
                        tx = await conn.begin()
                        try:
                            await conn.execute(text("CREATE TEMP TABLE __db_probe_tmp (id int)"))
                            await conn.execute(text("INSERT INTO __db_probe_tmp (id) VALUES (1)"))
                            await tx.rollback()
                            write_probe["mode"] = "temp_table"
                            write_probe["insert_rollback"] = True
                        except Exception as e:
                            try:
                                await tx.rollback()
                            except Exception:
                                pass
                            write_probe["mode"] = "temp_table"
                            write_probe["insert_rollback"] = False
                            write_probe["error"] = f"{type(e).__name__}: {e}"
                except Exception as e:
                    write_probe["insert_rollback"] = False
                    write_probe["error"] = f"{type(e).__name__}: {e}"

            vector_dims = {}
            try:
                targets = [
                    ("expert_comments", "embedding"),
                    ("paper_sections", "content_vector"),
                    ("papers", "abstract_vector"),
                    ("reviews", "review_vector"),
                    ("paper_paragraphs", "content_vector"),
                ]
                for tbl, col in targets:
                    row = (
                        await conn.execute(
                            text(
                                "SELECT t.typname AS typ, a.atttypmod AS typmod, "
                                "pg_catalog.format_type(a.atttypid, a.atttypmod) AS formatted "
                                "FROM pg_attribute a "
                                "JOIN pg_class c ON a.attrelid=c.oid "
                                "JOIN pg_namespace n ON c.relnamespace=n.oid "
                                "JOIN pg_type t ON a.atttypid=t.oid "
                                "WHERE n.nspname='public' AND c.relname=:tbl AND a.attname=:col "
                                "AND a.attnum>0 AND NOT a.attisdropped "
                                "LIMIT 1"
                            ),
                            {"tbl": tbl, "col": col},
                        )
                    ).mappings().first()
                    if not row:
                        continue
                    typ = row.get("typ")
                    typmod = row.get("typmod")
                    formatted = row.get("formatted")
                    dim = None
                    try:
                        s = str(formatted or "")
                        if s.startswith("vector(") and s.endswith(")"):
                            dim = int(s[len("vector(") : -1])
                    except Exception:
                        dim = None
                    vector_dims[f"{tbl}.{col}"] = {"type": typ, "typmod": typmod, "formatted": formatted, "dim": dim}
            except Exception as e:
                vector_dims = {"error": f"{type(e).__name__}: {e}"}

            return {
                "connected": True,
                "current_user": current_user,
                "current_database": current_db,
                "version": version,
                "public_tables": list(tables or []),
                "schema_create": bool(schema_create),
                "table_privileges": checks,
                "write_probe": write_probe,
                "vector_dims": vector_dims,
            }
    finally:
        await engine.dispose()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-check", action="store_true")
    args = parser.parse_args()

    explicit_url = os.getenv("DATABASE_URL", "").strip()
    env_url = _build_db_url_from_env()
    if explicit_url:
        database_url = explicit_url
        source = "env:DATABASE_URL"
    elif env_url:
        database_url = env_url
        source = "env:DB_*"
    else:
        from config import DATABASE_URL

        database_url = DATABASE_URL
        source = "config:DATABASE_URL"

    _, _, host, port, _ = _parse_db_url(database_url)
    print(f"DB_URL_SOURCE: {source}")

    tcp_err = _tcp_probe(host, port)
    if tcp_err:
        print(f"TCP_CONNECT: FAIL ({host}:{port}) {tcp_err}")
    else:
        print(f"TCP_CONNECT: OK ({host}:{port})")

    _, password, _, _, _ = _parse_db_url(database_url)
    if not password:
        print("DB_AUTH: SKIP (set DATABASE_URL or DB_PASSWORD to run SQL checks)")
        return 2 if tcp_err else 1

    masked = _mask_db_url(database_url)
    print(f"DB_URL: {masked}")

    try:
        res = await _sql_probe(database_url, bool(args.write_check))
    except Exception as e:
        print(f"DB_AUTH: FAIL {type(e).__name__}: {e}")
        return 2

    print("DB_AUTH: OK")
    print(f"USER: {res.get('current_user')}")
    print(f"DB: {res.get('current_database')}")
    print(f"SCHEMA_CREATE_PUBLIC: {res.get('schema_create')}")
    tables = res.get("public_tables") or []
    print(f"TABLES_PUBLIC({len(tables)}): " + ", ".join(tables[:20]))
    for c in res.get("table_privileges") or []:
        if not c.get("exists"):
            print(f"PRIV {c.get('table')}: MISSING")
            continue
        print(
            f"PRIV {c.get('table')}: "
            f"SELECT={c.get('can_select')} "
            f"INSERT={c.get('can_insert')} "
            f"UPDATE={c.get('can_update')} "
            f"DELETE={c.get('can_delete')}"
        )
    vd = res.get("vector_dims") or {}
    if isinstance(vd, dict) and vd:
        if "error" in vd:
            print(f"VECTOR_DIMS: ERROR ({vd.get('error')})")
        else:
            for k, info in vd.items():
                if not isinstance(info, dict):
                    continue
                formatted = info.get("formatted")
                print(f"VECTOR_DIM {k}: {formatted} dim={info.get('dim')}")
    wp = res.get("write_probe") or {}
    if wp.get("attempted"):
        if wp.get("insert_rollback") is True:
            print("WRITE_PROBE: OK (insert + rollback)")
        elif wp.get("insert_rollback") is False:
            reason = wp.get("skipped_reason") or ""
            err = wp.get("error") or ""
            tail = (reason or err).strip()
            print("WRITE_PROBE: FAIL" + (f" ({tail})" if tail else ""))
        else:
            reason = (wp.get("skipped_reason") or "skipped").strip()
            print("WRITE_PROBE: SKIP" + (f" ({reason})" if reason else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
