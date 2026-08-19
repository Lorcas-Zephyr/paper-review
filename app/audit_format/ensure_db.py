
import asyncio
import asyncpg
import sys
import os
from urllib.parse import quote_plus, urlsplit, unquote

# Add parent directory to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

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


def _parse_db_url(url: str):
    normalized = (url or "").strip().replace("postgresql+asyncpg://", "postgresql://", 1)
    s = urlsplit(normalized)
    if not s.scheme.startswith("postgresql"):
        raise ValueError("unsupported scheme")
    user = unquote(s.username or "")
    password = unquote(s.password or "")
    host = s.hostname or ""
    port = int(s.port or 5432)
    name = (s.path or "").lstrip("/")
    if not user or not host or not name:
        raise ValueError("missing user/host/dbname")
    return user, password, host, port, name


def _build_db_url_from_env() -> str | None:
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

_CORE_TABLES = ("paper_sections", "expert_comments", "review_tasks", "agent_audit_result")
_OPTIONAL_TABLES = ("agent_rules", "ground_truth_issues")
_PUBLIC_SCHEMA = "public"
_REVIEW_STATUS_ENUM = ("PENDING", "RUNNING", "SUCCESS", "FAILED", "TIMEOUT")


async def _try_exec(conn, sql: str, params: dict | None = None):
    try:
        async with conn.begin_nested():
            await conn.execute(text(sql), params or {})
        return True, None
    except Exception as e:
        return False, e


async def _fetch_val(conn, sql: str, params: dict | None = None):
    result = await conn.execute(text(sql), params or {})
    return result.scalar_one_or_none()


async def _fetch_all(conn, sql: str, params: dict | None = None):
    result = await conn.execute(text(sql), params or {})
    return list(result.mappings().all())


async def _current_user_info(conn) -> dict:
    row = await _fetch_all(
        conn,
        """
        SELECT current_user AS user, r.rolsuper AS is_superuser
        FROM pg_roles r
        WHERE r.rolname = current_user
        """,
    )
    return dict(row[0]) if row else {"user": None, "is_superuser": False}


async def _public_tables(conn) -> set[str]:
    rows = await _fetch_all(
        conn,
        "SELECT tablename FROM pg_tables WHERE schemaname = :s",
        {"s": _PUBLIC_SCHEMA},
    )
    return {str(r.get("tablename")) for r in rows if r.get("tablename")}


async def _table_owner(conn, table: str) -> str | None:
    row = await _fetch_all(
        conn,
        """
        SELECT r.rolname AS owner
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_roles r ON r.oid = c.relowner
        WHERE n.nspname = :s AND c.relname = :t
        """,
        {"s": _PUBLIC_SCHEMA, "t": table},
    )
    return str(row[0].get("owner")) if row else None


async def _ddl_allowed(conn, table: str) -> tuple[bool, str]:
    info = await _current_user_info(conn)
    user = str(info.get("user") or "")
    is_super = bool(info.get("is_superuser"))
    owner = await _table_owner(conn, table)
    if is_super:
        return True, f"user={user} superuser=true owner={owner or '?'}"
    if owner and owner == user:
        return True, f"user={user} superuser=false owner={owner}"
    return False, f"user={user} superuser=false owner={owner or '?'}"


async def _columns_pg(conn, table: str) -> dict[str, dict]:
    rows = await _fetch_all(
        conn,
        """
        SELECT
            a.attname AS name,
            pg_catalog.format_type(a.atttypid, a.atttypmod) AS type,
            a.attnotnull AS not_null
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = :s
          AND c.relname = :t
          AND a.attnum > 0
          AND NOT a.attisdropped
        ORDER BY a.attnum
        """,
        {"s": _PUBLIC_SCHEMA, "t": table},
    )
    out: dict[str, dict] = {}
    for r in rows:
        name = str(r.get("name") or "")
        if not name:
            continue
        out[name] = {"name": name, "type": str(r.get("type") or ""), "not_null": bool(r.get("not_null"))}
    return out


async def _indexes_pg(conn, table: str) -> set[str]:
    rows = await _fetch_all(
        conn,
        """
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = :s AND tablename = :t
        """,
        {"s": _PUBLIC_SCHEMA, "t": table},
    )
    return {str(r.get("indexname")) for r in rows if r.get("indexname")}


async def _row_count(conn, table: str) -> int | None:
    try:
        val = await _fetch_val(conn, f"SELECT COUNT(*) FROM {_PUBLIC_SCHEMA}.{table}")
        return int(val) if val is not None else None
    except Exception:
        return None


async def _ensure_enum_task_status(conn):
    ok, err = await _try_exec(
        conn,
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'taskstatus') THEN
                CREATE TYPE taskstatus AS ENUM ('PENDING','RUNNING','SUCCESS','FAILED','TIMEOUT');
            END IF;
        END$$;
        """,
    )
    if not ok:
        print(f"⚠️ Failed to ensure enum taskstatus: {type(err).__name__}: {err}")


async def _ensure_table_review_tasks(conn):
    await _ensure_enum_task_status(conn)
    ok, err = await _try_exec(
        conn,
        """
        CREATE TABLE IF NOT EXISTS review_tasks (
            id BIGSERIAL PRIMARY KEY,
            task_id TEXT NOT NULL,
            paper_id UUID NOT NULL,
            chunk_id TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            agent_version TEXT NOT NULL,
            status taskstatus NOT NULL DEFAULT 'PENDING',
            score INTEGER,
            audit_level TEXT,
            result_json JSONB,
            error_msg TEXT,
            usage_tokens INTEGER,
            latency_ms INTEGER,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
        """,
    )
    if not ok:
        print(f"❌ Failed to create review_tasks: {type(err).__name__}: {err}")
        return

    await _try_exec(conn, "CREATE INDEX IF NOT EXISTS ix_review_tasks_task_id ON review_tasks (task_id);")
    await _try_exec(conn, "CREATE INDEX IF NOT EXISTS ix_review_tasks_paper_id ON review_tasks (paper_id);")
    await _try_exec(conn, "CREATE INDEX IF NOT EXISTS ix_review_tasks_paper_chunk ON review_tasks (paper_id, chunk_id);")
    await _try_exec(conn, "ALTER TABLE review_tasks ALTER COLUMN status SET DEFAULT 'PENDING';")
    await _try_exec(conn, "ALTER TABLE review_tasks ALTER COLUMN created_at SET DEFAULT NOW();")
    await _try_exec(conn, "ALTER TABLE review_tasks ALTER COLUMN updated_at SET DEFAULT NOW();")


async def _ensure_table_paper_sections(conn):
    ok, err = await _try_exec(
        conn,
        """
        CREATE TABLE IF NOT EXISTS paper_sections (
            section_id SERIAL PRIMARY KEY,
            paper_id UUID NOT NULL,
            section_name VARCHAR,
            content TEXT,
            section_content TEXT,
            content_vector vector(768)
        );
        """,
    )
    if not ok:
        print(f"❌ Failed to create paper_sections: {type(err).__name__}: {err}")
        return

    await _try_exec(conn, "CREATE INDEX IF NOT EXISTS ix_paper_sections_paper_id ON paper_sections (paper_id);")
    await _try_exec(conn, "CREATE INDEX IF NOT EXISTS ix_paper_sections_paper_section ON paper_sections (paper_id, section_name);")


async def _ensure_table_agent_audit_result(conn):
    ok, err = await _try_exec(
        conn,
        """
        CREATE TABLE IF NOT EXISTS agent_audit_result (
            id BIGSERIAL PRIMARY KEY,
            request_id TEXT,
            task_id UUID,
            paper_id UUID,
            chunk_id TEXT,
            agent_code TEXT,
            agent_name TEXT,
            agent_version TEXT,
            status TEXT,
            error_msg TEXT,
            result_json JSONB DEFAULT '{}'::jsonb,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
        """,
    )
    if not ok:
        print(f"❌ Failed to create agent_audit_result: {type(err).__name__}: {err}")
        return

    await _try_exec(conn, "CREATE INDEX IF NOT EXISTS ix_agent_audit_result_request_id ON agent_audit_result (request_id);")
    await _try_exec(conn, "CREATE INDEX IF NOT EXISTS ix_agent_audit_result_paper_id ON agent_audit_result (paper_id);")
    await _try_exec(conn, "CREATE INDEX IF NOT EXISTS ix_agent_audit_result_paper_chunk ON agent_audit_result (paper_id, chunk_id);")
    await _try_exec(conn, "CREATE INDEX IF NOT EXISTS ix_agent_audit_result_agent_code ON agent_audit_result (agent_code);")


async def _ensure_table_expert_comments(conn):
    ok, err = await _try_exec(
        conn,
        """
        CREATE TABLE IF NOT EXISTS expert_comments (
            comment_id BIGSERIAL PRIMARY KEY,
            rule_code VARCHAR,
            rule_category VARCHAR,
            rule_title VARCHAR,
            rule_text TEXT,
            indicator_name VARCHAR,
            operator VARCHAR,
            threshold_value DOUBLE PRECISION,
            threshold_secondary DOUBLE PRECISION,
            threshold_unit VARCHAR,
            severity VARCHAR,
            weight DOUBLE PRECISION,
            is_hard_rule BOOLEAN,
            evidence_pattern TEXT,
            embedding vector(768),
            source VARCHAR,
            active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            metric_id TEXT,
            text TEXT
        );
        """,
    )
    if not ok:
        print(f"❌ Failed to create expert_comments: {type(err).__name__}: {err}")
        return

    await _try_exec(conn, "CREATE INDEX IF NOT EXISTS ix_expert_comments_metric_id ON expert_comments (metric_id);")
    await _try_exec(
        conn,
        "CREATE INDEX IF NOT EXISTS idx_expert_comments_embedding ON expert_comments USING hnsw (embedding vector_cosine_ops);",
    )


async def _ensure_expert_comments_embedding_dim(conn) -> None:
    row = (
        await conn.execute(
            text(
                "SELECT pg_catalog.format_type(a.atttypid, a.atttypmod) AS formatted "
                "FROM pg_attribute a "
                "JOIN pg_class c ON a.attrelid=c.oid "
                "JOIN pg_namespace n ON c.relnamespace=n.oid "
                "WHERE n.nspname='public' AND c.relname='expert_comments' "
                "AND a.attname='embedding' AND a.attnum>0 AND NOT a.attisdropped "
                "LIMIT 1"
            )
        )
    ).mappings().first()
    formatted = str((row or {}).get("formatted") or "")
    if formatted == "vector(768)":
        return

    can_ddl, ctx = await _ddl_allowed(conn, "expert_comments")
    if not can_ddl:
        print(f"⚠️ expert_comments.embedding dim mismatch ({formatted}), but no DDL privilege ({ctx}).")
        return

    await _try_exec(conn, "DROP INDEX IF EXISTS idx_expert_comments_embedding;")
    ok, err = await _try_exec(
        conn,
        "ALTER TABLE expert_comments ALTER COLUMN embedding TYPE vector(768) USING "
        "(((COALESCE(embedding, (array_fill(0.0::real, ARRAY[8]))::vector(8)))::real[] "
        "|| array_fill(0.0::real, ARRAY[760]))::vector(768));",
    )
    if not ok:
        print(f"❌ Failed to alter expert_comments.embedding to vector(768): {type(err).__name__}: {err}")
        return
    await _try_exec(
        conn,
        "CREATE INDEX IF NOT EXISTS idx_expert_comments_embedding ON expert_comments USING hnsw (embedding vector_cosine_ops);",
    )


async def _ensure_table_agent_rules(conn):
    ok, err = await _try_exec(
        conn,
        """
        CREATE TABLE IF NOT EXISTS agent_rules (
            id BIGSERIAL PRIMARY KEY,
            rule_id TEXT NOT NULL,
            content TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT NOW()
        );
        """,
    )
    if not ok:
        print(f"⚠️ Failed to create agent_rules: {type(err).__name__}: {err}")
        return
    await _try_exec(conn, "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_rules_rule_id ON agent_rules (rule_id);")


async def _ensure_table_ground_truth_issues(conn):
    ok, err = await _try_exec(
        conn,
        """
        CREATE TABLE IF NOT EXISTS ground_truth_issues (
            id BIGSERIAL PRIMARY KEY,
            sample_id TEXT,
            paper_id UUID,
            chunk_id TEXT,
            issue_type TEXT NOT NULL,
            severity TEXT,
            message TEXT,
            evidence TEXT,
            page_num INTEGER,
            bbox JSONB,
            source TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
        """,
    )
    if not ok:
        print(f"⚠️ Failed to create ground_truth_issues: {type(err).__name__}: {err}")
        return
    await _try_exec(conn, "CREATE INDEX IF NOT EXISTS ix_ground_truth_issues_sample_id ON ground_truth_issues (sample_id);")
    await _try_exec(conn, "CREATE INDEX IF NOT EXISTS ix_ground_truth_issues_paper_id ON ground_truth_issues (paper_id);")
    await _try_exec(conn, "CREATE INDEX IF NOT EXISTS ix_ground_truth_issues_paper_chunk ON ground_truth_issues (paper_id, chunk_id);")
    await _try_exec(conn, "CREATE INDEX IF NOT EXISTS ix_ground_truth_issues_issue_type ON ground_truth_issues (issue_type);")


def _type_matches(expected: str, actual: str) -> bool:
    e = (expected or "").lower().strip()
    a = (actual or "").lower().strip()
    if not e:
        return True
    if e == "text":
        return ("text" in a) or ("character varying" in a) or ("varchar" in a)
    if e == "varchar":
        return ("character varying" in a) or ("varchar" in a)
    if e == "timestamp":
        return "timestamp" in a
    if e == "uuid":
        return "uuid" in a
    if e == "integer":
        return ("integer" in a) or ("int" in a)
    if e == "bigint":
        return ("bigint" in a) or ("bigint" == a)
    if e in {"double", "double precision", "float"}:
        return ("double precision" in a) or ("real" in a) or ("numeric" in a)
    if e in {"boolean", "bool"}:
        return ("boolean" in a) or ("bool" in a)
    if e == "jsonb":
        return "jsonb" in a
    if e == "vector":
        return "vector" in a
    if e == "taskstatus":
        return "taskstatus" in a
    return e in a


async def _maybe_fix_review_tasks_status(conn, can_ddl: bool):
    if not can_ddl:
        return
    cols = await _columns_pg(conn, "review_tasks")
    status = cols.get("status")
    if not status:
        return
    if _type_matches("taskstatus", str(status.get("type") or "")):
        return

    cnt = await _row_count(conn, "review_tasks")
    if cnt is None:
        return
    if cnt == 0:
        ok, err = await _try_exec(
            conn,
            """
            ALTER TABLE review_tasks
            ALTER COLUMN status TYPE taskstatus
            USING status::taskstatus
            """,
        )
        if ok:
            print("✅ Fixed review_tasks.status type to taskstatus (table empty).")
        else:
            print(f"⚠️ Failed to fix review_tasks.status type: {type(err).__name__}: {err}")
        return

    vals = await _fetch_all(
        conn,
        "SELECT DISTINCT status::text AS v FROM review_tasks WHERE status IS NOT NULL LIMIT 50",
    )
    distinct = {str(r.get("v") or "") for r in vals}
    if distinct and distinct.issubset(set(_REVIEW_STATUS_ENUM)):
        ok, err = await _try_exec(
            conn,
            """
            ALTER TABLE review_tasks
            ALTER COLUMN status TYPE taskstatus
            USING status::text::taskstatus
            """,
        )
        if ok:
            print("✅ Fixed review_tasks.status type to taskstatus (values compatible).")
        else:
            print(f"⚠️ Failed to fix review_tasks.status type: {type(err).__name__}: {err}")
    else:
        sample = ", ".join(sorted([v for v in distinct if v])[:10])
        print(f"⚠️ review_tasks.status values not compatible with enum: {sample or '?'}")


async def _check_and_patch_table(
    conn,
    table: str,
    required_cols: dict,
    required_indexes: list[tuple[str, list[str], bool]],
    can_ddl: bool,
    ddl_context: str,
):
    cols = await _columns_pg(conn, table)
    missing = [k for k in required_cols.keys() if k not in cols]
    if missing:
        if not can_ddl:
            print(f"⚠️ Skip ADD COLUMN on {table} (no DDL privilege): {ddl_context}")
        else:
            for c in missing:
                ddl = required_cols[c].get("ddl")
                if not ddl:
                    continue
                ok, err = await _try_exec(conn, ddl)
                if ok:
                    print(f"✅ Added column {table}.{c}")
                else:
                    print(f"⚠️ Failed to add column {table}.{c}: {type(err).__name__}: {err}")

    cols = await _columns_pg(conn, table)
    mismatches = []
    for c, expected in required_cols.items():
        if c not in cols:
            continue
        expected_type = expected.get("type")
        if expected_type:
            actual_type = str(cols[c].get("type") or "").lower()
            if not _type_matches(expected_type, actual_type):
                mismatches.append((c, expected_type, actual_type))
    for c, exp, act in mismatches:
        print(f"⚠️ Column type mismatch {table}.{c}: expected contains '{exp}', actual '{act}'")

    existing_names = await _indexes_pg(conn, table)

    for name, cols_list, unique in required_indexes:
        if name in existing_names:
            continue
        if not can_ddl:
            print(f"⚠️ Skip CREATE INDEX {name} on {table} (no DDL privilege): {ddl_context}")
            continue
        cols_sql = ", ".join(cols_list)
        if unique:
            ok, err = await _try_exec(conn, f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table} ({cols_sql});")
        else:
            ok, err = await _try_exec(conn, f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({cols_sql});")
        if ok:
            print(f"✅ Ensured index {name} on {table}({cols_sql})")
        else:
            print(f"⚠️ Failed to ensure index {name} on {table}: {type(err).__name__}: {err}")


async def ensure_database_exists(db_user: str, db_pass: str, db_host: str, db_port: int, db_name: str):
    """Checks if database exists, creates if not."""
    print(f"Checking database '{db_name}' on {db_host}:{db_port}...")

    # Connect to default 'postgres' database to perform admin operations
    try:
        sys_conn = await asyncpg.connect(
            user=db_user,
            password=db_pass,
            database="postgres",
            host=db_host,
            port=db_port,
        )
    except Exception as e:
        print(f"❌ Failed to connect to PostgreSQL server: {e}")
        return False

    try:
        # Check if DB exists
        exists = await sys_conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1",
            db_name,
        )

        if not exists:
            print(f"Database '{db_name}' does not exist. Creating...")
            await sys_conn.execute(f'CREATE DATABASE "{db_name}"')
            print(f"✅ Database '{db_name}' created successfully.")
        else:
            print(f"✅ Database '{db_name}' already exists.")

        print(f"Installing extensions in '{db_name}' (Vector support required)...")
        db_conn = await asyncpg.connect(
            user=db_user,
            password=db_pass,
            database=db_name,
            host=db_host,
            port=db_port,
        )
        try:
            await db_conn.execute('CREATE EXTENSION IF NOT EXISTS vector;')
            print("✅ Extension 'vector' installed/verified.")
        except Exception as e:
            print(f"❌ Failed to install 'vector' extension on DB server: {e}")
            print("HINT: Install pgvector on the PostgreSQL server (or use the pgvector docker image).")
            return False
        finally:
            await db_conn.close()

    except Exception as e:
        print(f"❌ Error checking/creating database: {e}")
        return False
    finally:
        await sys_conn.close()

    return True

async def ensure_tables_exist(database_url: str, db_name: str):
    """Checks table structure and patches schema to match 开发规范.md without modifying existing data."""
    print(f"Checking tables in '{db_name}'...")

    engine = create_async_engine(database_url, echo=False)

    try:
        async with engine.begin() as conn:
            existing_tables = await _public_tables(conn)
            for t in _CORE_TABLES:
                if t not in existing_tables:
                    print(f"⚠️ Missing core table: {t}")
            for t in _OPTIONAL_TABLES:
                if t not in existing_tables:
                    print(f"⚠️ Missing optional table: {t}")

            await _ensure_table_paper_sections(conn)
            await _ensure_table_expert_comments(conn)
            await _ensure_table_review_tasks(conn)
            await _ensure_table_agent_audit_result(conn)
            await _ensure_table_agent_rules(conn)
            await _ensure_table_ground_truth_issues(conn)

            can_paper, ctx_paper = await _ddl_allowed(conn, "paper_sections")
            can_expert, ctx_expert = await _ddl_allowed(conn, "expert_comments")
            can_review, ctx_review = await _ddl_allowed(conn, "review_tasks")
            can_audit, ctx_audit = await _ddl_allowed(conn, "agent_audit_result")
            can_rules, ctx_rules = await _ddl_allowed(conn, "agent_rules")
            can_gt, ctx_gt = await _ddl_allowed(conn, "ground_truth_issues")

            await _check_and_patch_table(
                conn,
                "paper_sections",
                required_cols={
                    "section_id": {"type": "integer", "ddl": None},
                    "paper_id": {"type": "uuid", "ddl": "ALTER TABLE paper_sections ADD COLUMN IF NOT EXISTS paper_id UUID;"},
                    "section_name": {"type": "text", "ddl": "ALTER TABLE paper_sections ADD COLUMN IF NOT EXISTS section_name VARCHAR;"},
                    "content": {"type": "text", "ddl": "ALTER TABLE paper_sections ADD COLUMN IF NOT EXISTS content TEXT;"},
                    "section_content": {"type": "text", "ddl": "ALTER TABLE paper_sections ADD COLUMN IF NOT EXISTS section_content TEXT;"},
                    "content_vector": {"type": "vector", "ddl": "ALTER TABLE paper_sections ADD COLUMN IF NOT EXISTS content_vector vector(768);"},
                },
                required_indexes=[
                    ("ix_paper_sections_paper_id", ["paper_id"], False),
                    ("ix_paper_sections_paper_section", ["paper_id", "section_name"], False),
                ],
                can_ddl=can_paper,
                ddl_context=ctx_paper,
            )

            await _check_and_patch_table(
                conn,
                "agent_audit_result",
                required_cols={
                    "id": {"type": "bigint", "ddl": None},
                    "request_id": {"type": "text", "ddl": "ALTER TABLE agent_audit_result ADD COLUMN IF NOT EXISTS request_id TEXT;"},
                    "task_id": {"type": "uuid", "ddl": "ALTER TABLE agent_audit_result ADD COLUMN IF NOT EXISTS task_id UUID;"},
                    "paper_id": {"type": "uuid", "ddl": "ALTER TABLE agent_audit_result ADD COLUMN IF NOT EXISTS paper_id UUID;"},
                    "chunk_id": {"type": "text", "ddl": "ALTER TABLE agent_audit_result ADD COLUMN IF NOT EXISTS chunk_id TEXT;"},
                    "agent_code": {"type": "text", "ddl": "ALTER TABLE agent_audit_result ADD COLUMN IF NOT EXISTS agent_code TEXT;"},
                    "agent_name": {"type": "text", "ddl": "ALTER TABLE agent_audit_result ADD COLUMN IF NOT EXISTS agent_name TEXT;"},
                    "agent_version": {"type": "text", "ddl": "ALTER TABLE agent_audit_result ADD COLUMN IF NOT EXISTS agent_version TEXT;"},
                    "status": {"type": "text", "ddl": "ALTER TABLE agent_audit_result ADD COLUMN IF NOT EXISTS status TEXT;"},
                    "error_msg": {"type": "text", "ddl": "ALTER TABLE agent_audit_result ADD COLUMN IF NOT EXISTS error_msg TEXT;"},
                    "result_json": {"type": "jsonb", "ddl": "ALTER TABLE agent_audit_result ADD COLUMN IF NOT EXISTS result_json JSONB DEFAULT '{}'::jsonb;"},
                    "created_at": {"type": "timestamp", "ddl": "ALTER TABLE agent_audit_result ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW();"},
                    "updated_at": {"type": "timestamp", "ddl": "ALTER TABLE agent_audit_result ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();"},
                },
                required_indexes=[
                    ("ix_agent_audit_result_request_id", ["request_id"], False),
                    ("ix_agent_audit_result_paper_id", ["paper_id"], False),
                    ("ix_agent_audit_result_paper_chunk", ["paper_id", "chunk_id"], False),
                    ("ix_agent_audit_result_agent_code", ["agent_code"], False),
                ],
                can_ddl=can_audit,
                ddl_context=ctx_audit,
            )

            await _check_and_patch_table(
                conn,
                "expert_comments",
                required_cols={
                    "comment_id": {"type": "bigint", "ddl": None},
                    "rule_code": {"type": "text", "ddl": "ALTER TABLE expert_comments ADD COLUMN IF NOT EXISTS rule_code VARCHAR;"},
                    "rule_category": {"type": "text", "ddl": "ALTER TABLE expert_comments ADD COLUMN IF NOT EXISTS rule_category VARCHAR;"},
                    "rule_title": {"type": "text", "ddl": "ALTER TABLE expert_comments ADD COLUMN IF NOT EXISTS rule_title VARCHAR;"},
                    "rule_text": {"type": "text", "ddl": "ALTER TABLE expert_comments ADD COLUMN IF NOT EXISTS rule_text TEXT;"},
                    "indicator_name": {"type": "text", "ddl": "ALTER TABLE expert_comments ADD COLUMN IF NOT EXISTS indicator_name VARCHAR;"},
                    "operator": {"type": "text", "ddl": "ALTER TABLE expert_comments ADD COLUMN IF NOT EXISTS operator VARCHAR;"},
                    "threshold_value": {"type": "double", "ddl": "ALTER TABLE expert_comments ADD COLUMN IF NOT EXISTS threshold_value DOUBLE PRECISION;"},
                    "threshold_secondary": {"type": "double", "ddl": "ALTER TABLE expert_comments ADD COLUMN IF NOT EXISTS threshold_secondary DOUBLE PRECISION;"},
                    "threshold_unit": {"type": "text", "ddl": "ALTER TABLE expert_comments ADD COLUMN IF NOT EXISTS threshold_unit VARCHAR;"},
                    "severity": {"type": "text", "ddl": "ALTER TABLE expert_comments ADD COLUMN IF NOT EXISTS severity VARCHAR;"},
                    "weight": {"type": "double", "ddl": "ALTER TABLE expert_comments ADD COLUMN IF NOT EXISTS weight DOUBLE PRECISION;"},
                    "is_hard_rule": {"type": "boolean", "ddl": "ALTER TABLE expert_comments ADD COLUMN IF NOT EXISTS is_hard_rule BOOLEAN;"},
                    "evidence_pattern": {"type": "text", "ddl": "ALTER TABLE expert_comments ADD COLUMN IF NOT EXISTS evidence_pattern TEXT;"},
                    "embedding": {"type": "vector", "ddl": "ALTER TABLE expert_comments ADD COLUMN IF NOT EXISTS embedding vector(768);"},
                    "source": {"type": "text", "ddl": "ALTER TABLE expert_comments ADD COLUMN IF NOT EXISTS source VARCHAR;"},
                    "active": {"type": "boolean", "ddl": "ALTER TABLE expert_comments ADD COLUMN IF NOT EXISTS active BOOLEAN;"},
                    "created_at": {"type": "timestamp", "ddl": "ALTER TABLE expert_comments ADD COLUMN IF NOT EXISTS created_at TIMESTAMP;"},
                    "updated_at": {"type": "timestamp", "ddl": "ALTER TABLE expert_comments ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP;"},
                    "metric_id": {"type": "text", "ddl": "ALTER TABLE expert_comments ADD COLUMN IF NOT EXISTS metric_id TEXT;"},
                    "text": {"type": "text", "ddl": "ALTER TABLE expert_comments ADD COLUMN IF NOT EXISTS text TEXT;"},
                },
                required_indexes=[
                    ("ix_expert_comments_metric_id", ["metric_id"], False),
                ],
                can_ddl=can_expert,
                ddl_context=ctx_expert,
            )
            await _ensure_expert_comments_embedding_dim(conn)

            await _check_and_patch_table(
                conn,
                "review_tasks",
                required_cols={
                    "task_id": {"type": "text", "ddl": "ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS task_id TEXT;"},
                    "paper_id": {"type": "uuid", "ddl": "ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS paper_id UUID;"},
                    "chunk_id": {"type": "text", "ddl": "ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS chunk_id TEXT;"},
                    "agent_name": {"type": "text", "ddl": "ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS agent_name TEXT;"},
                    "agent_version": {"type": "text", "ddl": "ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS agent_version TEXT;"},
                    "status": {"type": "taskstatus", "ddl": "ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS status taskstatus;"},
                    "score": {"type": "integer", "ddl": "ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS score INTEGER;"},
                    "audit_level": {"type": "text", "ddl": "ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS audit_level TEXT;"},
                    "result_json": {"type": "jsonb", "ddl": "ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS result_json JSONB;"},
                    "error_msg": {"type": "text", "ddl": "ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS error_msg TEXT;"},
                    "usage_tokens": {"type": "integer", "ddl": "ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS usage_tokens INTEGER;"},
                    "latency_ms": {"type": "integer", "ddl": "ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS latency_ms INTEGER;"},
                    "created_at": {"type": "timestamp", "ddl": "ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS created_at TIMESTAMP;"},
                    "updated_at": {"type": "timestamp", "ddl": "ALTER TABLE review_tasks ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP;"},
                },
                required_indexes=[
                    ("ix_review_tasks_task_id", ["task_id"], False),
                    ("ix_review_tasks_paper_id", ["paper_id"], False),
                    ("ix_review_tasks_paper_chunk", ["paper_id", "chunk_id"], False),
                ],
                can_ddl=can_review,
                ddl_context=ctx_review,
            )

            await _maybe_fix_review_tasks_status(conn, can_review)

            await _check_and_patch_table(
                conn,
                "agent_rules",
                required_cols={
                    "rule_id": {"type": "text", "ddl": "ALTER TABLE agent_rules ADD COLUMN IF NOT EXISTS rule_id TEXT;"},
                    "content": {"type": "text", "ddl": "ALTER TABLE agent_rules ADD COLUMN IF NOT EXISTS content TEXT;"},
                    "updated_at": {"type": "timestamp", "ddl": "ALTER TABLE agent_rules ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP;"},
                },
                required_indexes=[
                    ("uq_agent_rules_rule_id", ["rule_id"], True),
                ],
                can_ddl=can_rules,
                ddl_context=ctx_rules,
            )

            await _check_and_patch_table(
                conn,
                "ground_truth_issues",
                required_cols={
                    "id": {"type": "bigint", "ddl": None},
                    "sample_id": {"type": "text", "ddl": "ALTER TABLE ground_truth_issues ADD COLUMN IF NOT EXISTS sample_id TEXT;"},
                    "paper_id": {"type": "uuid", "ddl": "ALTER TABLE ground_truth_issues ADD COLUMN IF NOT EXISTS paper_id UUID;"},
                    "chunk_id": {"type": "text", "ddl": "ALTER TABLE ground_truth_issues ADD COLUMN IF NOT EXISTS chunk_id TEXT;"},
                    "issue_type": {"type": "text", "ddl": "ALTER TABLE ground_truth_issues ADD COLUMN IF NOT EXISTS issue_type TEXT;"},
                    "severity": {"type": "text", "ddl": "ALTER TABLE ground_truth_issues ADD COLUMN IF NOT EXISTS severity TEXT;"},
                    "message": {"type": "text", "ddl": "ALTER TABLE ground_truth_issues ADD COLUMN IF NOT EXISTS message TEXT;"},
                    "evidence": {"type": "text", "ddl": "ALTER TABLE ground_truth_issues ADD COLUMN IF NOT EXISTS evidence TEXT;"},
                    "page_num": {"type": "integer", "ddl": "ALTER TABLE ground_truth_issues ADD COLUMN IF NOT EXISTS page_num INTEGER;"},
                    "bbox": {"type": "jsonb", "ddl": "ALTER TABLE ground_truth_issues ADD COLUMN IF NOT EXISTS bbox JSONB;"},
                    "source": {"type": "text", "ddl": "ALTER TABLE ground_truth_issues ADD COLUMN IF NOT EXISTS source TEXT;"},
                    "created_at": {"type": "timestamp", "ddl": "ALTER TABLE ground_truth_issues ADD COLUMN IF NOT EXISTS created_at TIMESTAMP;"},
                    "updated_at": {"type": "timestamp", "ddl": "ALTER TABLE ground_truth_issues ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP;"},
                },
                required_indexes=[
                    ("ix_ground_truth_issues_sample_id", ["sample_id"], False),
                    ("ix_ground_truth_issues_paper_id", ["paper_id"], False),
                    ("ix_ground_truth_issues_paper_chunk", ["paper_id", "chunk_id"], False),
                    ("ix_ground_truth_issues_issue_type", ["issue_type"], False),
                ],
                can_ddl=can_gt,
                ddl_context=ctx_gt,
            )

            print("✅ Schema verification & patch completed.")

    except Exception as e:
        print(f"❌ Error checking tables: {e}")
    finally:
        await engine.dispose()

async def main():
    print("--- Database Verification & Initialization ---")
    explicit_url = os.getenv("DATABASE_URL", "").strip()
    env_url = _build_db_url_from_env()
    if explicit_url:
        database_url = explicit_url
        source = "env:DATABASE_URL"
    elif env_url:
        database_url = env_url
        source = "env:DB_*"
    else:
        from config import DATABASE_URL as config_url

        database_url = (config_url or "").strip()
        source = "config:DATABASE_URL"

    try:
        db_user, db_pass, db_host, db_port, db_name = _parse_db_url(database_url)
    except Exception:
        print(f"Error parsing DATABASE_URL ({source}): {_mask_db_url(database_url)}")
        raise

    print(f"DB_URL_SOURCE: {source}")
    print(f"DB_URL: {_mask_db_url(database_url)}")

    if await ensure_database_exists(db_user, db_pass, db_host, db_port, db_name):
        await ensure_tables_exist(database_url, db_name)
    print("--- Done ---")

if __name__ == "__main__":
    asyncio.run(main())
