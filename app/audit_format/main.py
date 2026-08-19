import json
import os
import sys
import time
import asyncio
import re
import uuid
from typing import Any, Dict
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

if __name__ == "__main__":
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    if not os.getenv("LLM_PROVIDER"):
        os.environ["LLM_PROVIDER"] = "deepseek"

from models import AuditRequest, AuditResponse, AgentInfo, AuditResult, ResourceUsage, AuditLevel
from core.layout_analysis import LayoutAnalyzer
from api.layout_routes import router as layout_router
from api.admin_routes import build_admin_router
from core.semantic_check import SemanticChecker
from core.pdf_utils import open_pdf
from core.database import db_manager, AgentAudit, TaskStatus
from core.rule_engine import RuleEngine
from utils.logger import setup_logger, set_request_id, reset_request_id
from config import AGENT_NAME, AGENT_VERSION, AGENT_CODE, AuditTag, LAYOUT_ANALYSIS_TIMEOUT, LLM_PROVIDER, DATABASE_URL, mask_database_url
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

# 初始化日志
logger = setup_logger(AGENT_NAME)

# 初始化规则引擎
rule_engine = RuleEngine()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时：连接数据库
    logger.info("==================================================")
    logger.info(f"   {AGENT_NAME} {AGENT_VERSION} Starting up...")
    logger.info("==================================================")
    logger.info(f"LLM Provider: {LLM_PROVIDER}")
    logger.info(f"DB Connection: {mask_database_url(DATABASE_URL)}")
    logger.info(f"Layout Timeout: {LAYOUT_ANALYSIS_TIMEOUT}s")

    logger.info("Starting up: Connecting to database...")
    # await db_manager.engine.connect() # SQLAlchemy async engine is lazy

    # Load rules from DB (Task A: Dynamic Rule Loading)
    loaded = await rule_engine.load_rules_from_db()
    if loaded:
        logger.info("Rules loaded from DB successfully.")
    else:
        logger.warning("Rules not loaded from DB; using YAML rules.")

    # Inject rules into core components
    layout_analyzer.update_rules(rule_engine.rules)
    semantic_checker.update_rules(rule_engine.rules)

    logger.info("Rules loaded: %s", list(rule_engine.rules.keys()))
    yield
    # 关闭时：断开数据库
    logger.info("Shutting down: Closing database connection...")
    await db_manager.close()

app = FastAPI(title=AGENT_NAME, version=AGENT_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有源
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(layout_router)

# 初始化核心组件
layout_analyzer = LayoutAnalyzer()
semantic_checker = SemanticChecker()
app.include_router(build_admin_router(rule_engine, layout_analyzer, semantic_checker))

# 自定义异常处理，符合规范要求的HTTP状态码
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.error(f"Validation error: {exc.errors()}")
    return JSONResponse(
        status_code=400,
        content={"detail": exc.errors(), "message": "Parameters validation failed"}
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "message": "HTTP error"},
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Internal error: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "message": "Internal server error"}
    )

def _collect_tags(issues):
    tag_set = set()
    for issue in issues:
        issue_type = ""
        if isinstance(issue, dict):
            issue_type = issue.get("issue_type", "") or ""
        else:
            issue_type = getattr(issue, "issue_type", "") or ""
        if not issue_type:
            continue
        issue_type_norm = str(issue_type).strip()
        issue_type_lower = issue_type_norm.lower()
        if ("citation" in issue_type_lower) or ("reference" in issue_type_lower):
            tag_set.add(AuditTag.CITATION_INCONSISTENCY.value)
        if issue_type_norm == "Label_Missing":
            tag_set.add(AuditTag.LABEL_MISSING.value)
        if issue_type_norm == "Hierarchy_Fault":
            tag_set.add(AuditTag.HIERARCHY_FAULT.value)
        if "punctuation" in issue_type_lower:
            tag_set.add(AuditTag.PUNCTUATION_ERROR.value)
    return list(tag_set)


def _compact_issue(issue: Any) -> Dict[str, Any]:
    if not isinstance(issue, dict):
        try:
            issue = issue.model_dump()  # type: ignore[attr-defined]
        except Exception:
            issue = {}
    out: Dict[str, Any] = {}
    for k in ("issue_type", "severity", "message", "suggestion", "evidence"):
        v = issue.get(k)
        if v is not None and v != "":
            out[k] = v
    page_num = issue.get("page_num")
    if page_num is not None and page_num != "":
        try:
            out["page_num"] = int(page_num)
        except Exception:
            out["page_num"] = str(page_num)
    bbox = issue.get("bbox")
    if bbox is not None:
        out["bbox"] = bbox
    return out


def _extract_first_int(value: object) -> int | None:
    if value is None:
        return None
    m = re.search(r"\d+", str(value))
    if not m:
        return None
    try:
        return int(m.group(0))
    except Exception:
        return None


async def _fetch_content_from_db(session, paper_id: str, chunk_id: str) -> str | None:
    paper_id_str = str(paper_id)
    chunk_id_str = str(chunk_id)
    chunk_int = _extract_first_int(chunk_id_str)

    cols: set[str] = set()
    try:
        rows = await session.execute(
            text(
                """
                select column_name
                from information_schema.columns
                where table_schema = 'public' and table_name = 'paper_sections'
                """
            )
        )
        cols = {str(r[0]) for r in (rows.fetchall() or []) if r and r[0]}
    except Exception:
        cols = set()

    content_cols = [c for c in ("content", "section_content") if c in cols]
    if not content_cols:
        return None

    attempts: list[tuple[str, dict]] = []
    for content_col in content_cols:
        if "section_name" in cols:
            attempts.append(
                (
                    f"select {content_col} from paper_sections where paper_id = cast(:paper_id as uuid) and section_name = :chunk_id limit 1",
                    {"paper_id": paper_id_str, "chunk_id": chunk_id_str},
                )
            )
            attempts.append(
                (
                    f"select {content_col} from paper_sections where paper_id = cast(:paper_id as uuid) and replace(section_name, ' ', '') = replace(:chunk_id, ' ', '') limit 1",
                    {"paper_id": paper_id_str, "chunk_id": chunk_id_str},
                )
            )
        if chunk_int is not None and "section_id" in cols:
            attempts.insert(
                0,
                (
                    f"select {content_col} from paper_sections where paper_id = cast(:paper_id as uuid) and section_id = :sid limit 1",
                    {"paper_id": paper_id_str, "sid": chunk_int},
                ),
            )
    if chunk_int is not None:
        attempts.append(
            (
                "select review_content from reviews where paper_id = cast(:paper_id as uuid) and (review_id = :sid or section_id = :sid) limit 1",
                {"paper_id": paper_id_str, "sid": chunk_int},
            ),
        )

    for sql, params in attempts:
        try:
            row = (await session.execute(text(sql), params)).first()
            if row and row[0]:
                return str(row[0])
        except SQLAlchemyError:
            try:
                await session.rollback()
            except Exception:
                pass
            continue
        except Exception:
            try:
                await session.rollback()
            except Exception:
                pass
            continue
    return None


def _normalize_level(level: Any) -> str:
    s = str(level or "").strip()
    if not s:
        return "Info"
    s_low = s.lower()
    if s_low == "critical":
        return "Critical"
    if s_low == "warning":
        return "Warning"
    if s_low == "info":
        return "Info"
    if s_low in {"error", "fatal"}:
        return "Critical"
    if s_low in {"warn"}:
        return "Warning"
    return s[:1].upper() + s[1:].lower()


def _rule_id_from_issue_type(issue_type: Any) -> str:
    t = str(issue_type or "").strip()
    mapping = {
        "Citation_Inconsistency": f"{AGENT_CODE}-001",
        "Citation_Style_Inconsistent": f"{AGENT_CODE}-002",
        "Label_Missing": f"{AGENT_CODE}-003",
        "Hierarchy_Fault": f"{AGENT_CODE}-004",
        "Punctuation_Mixed": f"{AGENT_CODE}-005",
        "Punctuation_Error": f"{AGENT_CODE}-005",
        "Typo_Error": f"{AGENT_CODE}-006",
        "Typo_Limit_Exceeded": f"{AGENT_CODE}-006",
        "Formula_Readability": f"{AGENT_CODE}-007",
        "Formula_Missing": f"{AGENT_CODE}-008",
        "Formula_Ref_Missing": f"{AGENT_CODE}-009",
        "Formula_Misaligned": f"{AGENT_CODE}-010",
        "Formatting_Issue": f"{AGENT_CODE}-011",
        "Experiment_Result_Question": f"{AGENT_CODE}-012",
    }
    return mapping.get(t, f"{AGENT_CODE}-AUTO-{t or 'Other'}")


def _point_from_issue_type(issue_type: Any) -> str:
    t = str(issue_type or "").strip()
    mapping = {
        "Citation_Inconsistency": "引用一致性",
        "Citation_Style_Inconsistent": "参考文献格式一致性",
        "Label_Missing": "图表题注与引用",
        "Hierarchy_Fault": "标题层级与编号",
        "Punctuation_Mixed": "标点符号规范",
        "Punctuation_Error": "标点符号规范",
        "Typo_Error": "错别字/书写错误",
        "Typo_Limit_Exceeded": "错别字红线",
        "Formula_Readability": "公式可读性",
        "Formula_Missing": "公式编号规范",
        "Formula_Ref_Missing": "公式引用规范",
        "Formula_Misaligned": "公式排版规范",
        "Formatting_Issue": "排版与格式",
        "Experiment_Result_Question": "实验结果疑问",
    }
    return mapping.get(t, t or "其他")


def _score_from_level(level: Any) -> int:
    lvl = _normalize_level(level)
    if lvl == "Critical":
        return 5
    if lvl == "Warning":
        return 3
    return 1


def _build_agent_audit_result_payload(request: AuditRequest, debug: Dict[str, Any] | None) -> Dict[str, Any]:
    issues = []
    if isinstance(debug, dict):
        raw = debug.get("issues")
        if isinstance(raw, list):
            issues = raw

    audit_results = []
    paper_id = str(getattr(request.metadata, "paper_id", "") or "")

    for idx, issue in enumerate(issues, start=1):
        if not isinstance(issue, dict):
            continue
        issue_type = issue.get("issue_type")
        level = _normalize_level(issue.get("severity") or issue.get("level"))
        page_num = issue.get("page_num")
        bbox = issue.get("bbox")
        section = ""
        if page_num is not None and page_num != "":
            section = f"p{page_num}"
        line_start = None
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            try:
                line_start = int(float(bbox[1]))
            except Exception:
                line_start = None

        # 从 issue 中提取 evidence，如果没有则为空字符串
        evidence_quote = str(issue.get("evidence") or "").strip()

        audit_results.append(
            {
                "result_id": f"item-{idx:03d}",
                "paper_id": paper_id,
                "point": _point_from_issue_type(issue_type),
                "rule_id": _rule_id_from_issue_type(issue_type),
                "score": _score_from_level(level),
                "level": level,
                "description": str(issue.get("message") or "").strip(),
                "evidence_quote": evidence_quote,
                "location": {"section": section, "line_start": line_start},
                "suggestion": str(issue.get("suggestion") or "").strip(),
            }
        )

    return {"agent_code": AGENT_CODE, "audit_results": audit_results}


async def _get_table_columns(session, table_name: str) -> set[str]:
    sql = """
    select column_name
    from information_schema.columns
    where table_schema = 'public' and table_name = :t
    """
    rows = await session.execute(text(sql), {"t": table_name})
    cols = {str(r[0]) for r in (rows.fetchall() or []) if r and r[0]}
    return cols


async def _upsert_agent_audit_result(
    session,
    request: AuditRequest,
    status: TaskStatus,
    error_msg: str | None,
    debug: Dict[str, Any] | None,
) -> None:
    cols = await _get_table_columns(session, "agent_audit_result")
    if not cols:
        return

    payload = _build_agent_audit_result_payload(request, debug)

    data: Dict[str, Any] = {}
    if "result_id" in cols:
        data["result_id"] = uuid.uuid4().hex
    if "rule_id" in cols:
        data["rule_id"] = "FMT-001"
    if "result_json" in cols:
        data["result_json"] = json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else payload
    if "agent_code" in cols:
        data["agent_code"] = AGENT_CODE
    if "agent_name" in cols:
        data["agent_name"] = AGENT_NAME
    if "agent_version" in cols:
        data["agent_version"] = AGENT_VERSION
    if "status" in cols:
        data["status"] = status.value
    if "error_msg" in cols:
        data["error_msg"] = error_msg
    if "request_id" in cols:
        data["request_id"] = str(request.request_id)
    if "task_id" in cols:
        data["task_id"] = str(request.request_id)
    if "paper_id" in cols:
        data["paper_id"] = str(request.metadata.paper_id).replace("-", "")
    if "chunk_id" in cols:
        data["chunk_id"] = str(request.metadata.chunk_id)

    key_cols = [c for c in ["request_id", "task_id", "paper_id", "chunk_id", "agent_code"] if c in cols and c in data]
    exists = False
    if key_cols:
        where = " and ".join([f"{c} = :{c}" for c in key_cols])
        check_sql = f"select 1 from agent_audit_result where {where} limit 1"
        exists = (await session.execute(text(check_sql), {c: data[c] for c in key_cols})).first() is not None

    if exists and key_cols:
        set_cols = [c for c in data.keys() if c not in set(key_cols)]
        if not set_cols:
            return
        sets = []
        for c in set_cols:
            if c == "result_json":
                sets.append(f"{c} = CAST(:{c} AS jsonb)")
            else:
                sets.append(f"{c} = :{c}")
        where = " and ".join([f"{c} = :{c}" for c in key_cols])
        upd_sql = f"update agent_audit_result set {', '.join(sets)} where {where}"
        await session.execute(text(upd_sql), data)
        return

    insert_cols = list(data.keys())
    if not insert_cols:
        return
    vals = []
    for c in insert_cols:
        if c == "result_json":
            vals.append(f"CAST(:{c} AS jsonb)")
        else:
            vals.append(f":{c}")
    ins_sql = f"insert into agent_audit_result ({', '.join(insert_cols)}) values ({', '.join(vals)})"
    await session.execute(text(ins_sql), data)


async def save_result_to_db(
    request: AuditRequest,
    response: AuditResponse | None,
    status: TaskStatus,
    error_msg: str | None = None,
    debug: Dict[str, Any] | None = None,
    *,
    raise_on_error: bool = False,
) -> bool:
    def _safe_uuid(val):
        import uuid
        try:
            return str(uuid.UUID(str(val)))
        except:
            return str(uuid.uuid4())

    try:
        async with db_manager.session() as session:
            safe_task_id = _safe_uuid(request.request_id)
            stmt = (
                select(AgentAudit)
                .where(
                    AgentAudit.task_id == safe_task_id,
                    AgentAudit.paper_id == request.metadata.paper_id,
                    AgentAudit.chunk_id == request.metadata.chunk_id,
                    AgentAudit.agent_name == "Format_Agent",
                )
                .order_by(AgentAudit.created_at.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            task = result.scalar_one_or_none()

            if task is None:
                task = AgentAudit(
                    task_id=safe_task_id,
                    paper_id=request.metadata.paper_id,
                    chunk_id=request.metadata.chunk_id,
                    agent_name="Format_Agent",
                    agent_version=AGENT_VERSION,
                )
                session.add(task)

            task.agent_version = AGENT_VERSION
            task.status = status
            task.score = response.result.score if response else None
            task.audit_level = response.result.audit_level.value if response else None
            payload = response.model_dump(mode="json") if response else None
            if isinstance(payload, dict) and debug:
                payload["debug"] = debug
                # 构建 audit_results 并存入 result_json，以满足反思引擎细粒度审计项的需求
                built_payload = _build_agent_audit_result_payload(request, debug)
                payload["audit_results"] = built_payload.get("audit_results", [])
            task.result_json = payload
            task.error_msg = error_msg
            task.usage_tokens = response.usage.tokens if response else 0
            task.latency_ms = response.usage.latency_ms if response else 0

            await session.commit()
            logger.info(f"Task {request.request_id} saved to DB (status={status}).")
            return True
    except Exception as e:
        logger.error(f"Failed to save task to DB: {type(e).__name__}: {e!r}")
        if raise_on_error:
            raise
        return False

@app.post("/audit", tags=["Audit"], summary="执行论文格式审计")
async def audit_paper(request: AuditRequest):
    """
    接收论文切片，执行视觉与语义层面的格式审计，返回符合系统协议的JSON结果。
    Ref: 开发规范 - 四、API交互规范
    """
    token = set_request_id(request.request_id)
    start_time = time.time()
    logger.info(f"Received audit request for paper {request.metadata.paper_id}")

    try:
        await save_result_to_db(request, None, TaskStatus.RUNNING)

        # Check if content is provided, if not fetch from DB
        content = request.payload.content
        if not content:
            logger.info(f"Content missing in payload. Fetching from DB for paper {request.metadata.paper_id}, chunk {request.metadata.chunk_id}")
            try:
                # Use a new session to fetch content
                async with db_manager.session() as session:
                    content = await _fetch_content_from_db(session, request.metadata.paper_id, request.metadata.chunk_id)
            except Exception as e:
                logger.error(f"Failed to fetch content from DB: {type(e).__name__}: {e!r}")

            if not content:
                raise HTTPException(status_code=400, detail=f"Missing payload.content and no matching content found in DB for paper {request.metadata.paper_id} chunk {request.metadata.chunk_id}")

            # Update request payload with fetched content
            request.payload.content = content

        detailed_rules = bool(getattr(request.config, "enable_rules", True))

        # 1. 视觉/布局分析
        logger.info("Starting layout analysis...")
        try:
            layout_data = await asyncio.wait_for(layout_analyzer.analyze(request.payload.content), timeout=LAYOUT_ANALYSIS_TIMEOUT)
        except asyncio.TimeoutError:
            logger.error(f"Layout analysis timeout (>{LAYOUT_ANALYSIS_TIMEOUT}s)")
            def _count_pages_sync(pdf_payload):
                doc = open_pdf(pdf_payload)
                try:
                    return len(doc)
                finally:
                    doc.close()

            page_count = None
            try:
                page_count = await asyncio.to_thread(_count_pages_sync, request.payload.content)
            except Exception:
                page_count = None
            layout_data = {
                "elements": [],
                "layout_result": {"layout_issues": []},
                "parse_errors": [{"error_type": "layout_timeout", "message": "layout analysis timeout"}],
                "parse_report": {"page_count": page_count} if page_count else {},
            }

        # 2. 语义校验（enable_rules=False 时为基本模式：跳过 YAML 细则，保留布局 + 限块 LLM 扫描）
        logger.info("Starting semantic check... detailed_rules=%s", detailed_rules)
        semantic_result = await semantic_checker.check(
            request.payload.content, layout_data, detailed_rules=detailed_rules
        )

        # 3. 构造返回结果
        layout_issues = layout_data.get("layout_result", {}).get("layout_issues", [])
        issues = layout_issues + semantic_result.get("semantic_issues", [])
        score = semantic_checker._calculate_score(issues)

        # 评分与评级逻辑
        audit_level = AuditLevel.INFO
        if score < 60:
            audit_level = AuditLevel.CRITICAL
        elif score < 80:
            audit_level = AuditLevel.WARNING

        comment = "格式审计完成。"
        suggestion = "请检查文中标记的格式问题。"
        if issues:
            comment = f"发现 {len(issues)} 个格式问题。"
            suggestion = "建议根据详细报告进行修改。"

        rag_comment, rag_suggestion = await semantic_checker.generate_expert_commentary(
            request.payload.content, issues
        )
        if rag_comment and rag_suggestion:
            comment = rag_comment
            suggestion = rag_suggestion

        tags = _collect_tags(issues) if issues else []

        # 计算耗时
        latency_ms = int((time.time() - start_time) * 1000)

        # 估算Token消耗
        tokens = len(request.payload.content) // 4

        response = AuditResponse(
            request_id=request.request_id,
            agent_info=AgentInfo(name=AGENT_NAME, version=AGENT_VERSION),
            result=AuditResult(
                score=score,
                audit_level=audit_level,
                comment=comment,
                suggestion=suggestion,
                tags=tags
            ),
            usage=ResourceUsage(
                tokens=tokens,
                latency_ms=latency_ms
            )
        )

        # 4. 异步写入数据库 (Fire-and-forget or await depending on requirement)
        # 规范要求"实时写入"，这里使用 await 确保数据落库
        debug = {
            "issue_count": int(len(issues)),
            "issues": [_compact_issue(i) for i in (issues or [])],
        }
        await save_result_to_db(request, response, TaskStatus.SUCCESS, debug=debug)

        # 将 response 转换为 dict 并加上 audit_results 以满足反思引擎细粒度读取需求
        response_dict = response.model_dump()
        built_payload = _build_agent_audit_result_payload(request, debug)
        response_dict["result"]["audit_results"] = built_payload.get("audit_results", [])

        return response_dict

    except Exception as e:
        # 记录失败状态
        try:
            await save_result_to_db(request, None, TaskStatus.FAILED, error_msg=str(e), debug={"error": str(e)})
        except Exception:
            pass
        raise e
    finally:
        reset_request_id(token)

@app.get("/rules")
async def get_rules():
    """获取当前加载的动态规则 (用于测试验证)"""
    return rule_engine.rules

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

@app.get("/", response_class=HTMLResponse)
async def root():
    html_content = f"""
    <!DOCTYPE html>
    <html>
        <head>
            <title>{AGENT_NAME}</title>
            <style>
                body {{ font-family: sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }}
                .status {{ padding: 10px; border-radius: 5px; background: #e0f7fa; }}
                .config {{ background: #f5f5f5; padding: 10px; border-radius: 5px; }}
            </style>
        </head>
        <body>
            <h1>{AGENT_NAME} ({AGENT_VERSION})</h1>
            <div class="status">
                <p>Status: <strong>Running</strong></p>
                <p>Docs: <a href="/docs">/docs</a> | Health: <a href="/health">/health</a> | Rules: <a href="/rules">/rules</a></p>
            </div>
            <h3>Current Configuration</h3>
            <div class="config">
                <pre>
LLM_PROVIDER: {LLM_PROVIDER}
DB_CONNECTION: {mask_database_url(DATABASE_URL)}
LAYOUT_TIMEOUT: {LAYOUT_ANALYSIS_TIMEOUT}s
                </pre>
            </div>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8007)
