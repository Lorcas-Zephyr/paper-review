#!/usr/bin/env python3
# 文献真实性审计 Agent（组6）- 真实性校验、关联性核查、时效性评估
# 可选：BACKEND_URL 启用 RAG；SERPAPI_KEY 用 Google Scholar 做真实性校验；LLM 做关联性核查（方案 A）

import os
import re
import asyncio
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from llm_config import get_deepseek_config

DEEPSEEK_CONFIG = get_deepseek_config()
import httpx
from datetime import datetime
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
import json
import uuid
import random

app = FastAPI(title="Citation Auditor Agent", version="v1.0")

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

def save_audit_result_to_db(task_id: str, paper_id: str, chunk_id: str, result_dict: dict):
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
            user=DB_USER, password=DB_PASSWORD
        )
        cur = conn.cursor()

        score = result_dict.get("result", {}).get("score", 85)
        level = result_dict.get("result", {}).get("audit_level", "Info")

        random_id = random.getrandbits(63)
        def _safe_uuid(val):
            import uuid
            try:
                return str(uuid.UUID(str(val)))
            except:
                return str(uuid.uuid4())

        safe_task_id = _safe_uuid(task_id)
        safe_paper_id = _safe_uuid(paper_id)

        cur.execute("""
            INSERT INTO agent_audits (
                id, task_id, paper_id, chunk_id, agent_name, agent_version, status,
                score, audit_level, result_json, error_msg, usage_tokens, latency_ms,
                created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
        """, (
            random_id, safe_task_id, safe_paper_id, chunk_id or "full_paper", "Citation_Agent", "1.0", 'SUCCESS',
            score, level, json.dumps(result_dict, ensure_ascii=False), None, 0, 0
        ))
        conn.commit()
        cur.close()
        print(f"文献审计结果已保存到 agent_audits，task_id: {safe_task_id}", flush=True)
    except Exception as e:
        print(f"保存文献审计结果到数据库失败: {e}", flush=True)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有源
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
RAG_TOP_K = 3
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "").strip()
# LLM：用于关联性核查（方案 A），支持单模型或多模型轮询
DEEPSEEK_API_KEY = DEEPSEEK_CONFIG.api_key
LLM_BASE_URL = DEEPSEEK_CONFIG.base_url
LLM_API_KEY = DEEPSEEK_CONFIG.api_key
LLM_MODEL = DEEPSEEK_CONFIG.model
LLM_MODE = os.environ.get("LLM_MODE", "single").strip().lower()  # single | multi
LLM_MODELS_RAW = os.environ.get("LLM_MODELS", "").strip()
LLM_MODEL_LIST = [m.strip() for m in LLM_MODELS_RAW.split(",") if m.strip()] or [LLM_MODEL]

# 参考文献段落内最多校验条数，避免 API 超频与超时
try:
    MAX_REFERENCES_TO_CHECK = max(1, min(500, int(os.getenv("CITATION_MAX_REFERENCES", "200"))))
except ValueError:
    MAX_REFERENCES_TO_CHECK = 200
# 每条 Scholar 请求间隔（秒）
SERPAPI_DELAY_SEC = 0.4
# 每条参考文献用于 Scholar 检索的文本长度
REF_SEARCH_LEN = 120
# 至少几个「检索词中的关键词」出现在第一条结果里才判为相关（避免无关结果也算通过）
MIN_KEYWORDS_IN_TOP_RESULT = 5
# 是否要求第一条结果的标题中出现「作者区」关键词（参考文献格式通常为 作者. 标题...，作者区更可靠）
REQUIRE_AUTHOR_IN_TITLE = True
# 若参考文献中含出版年份（19xx/20xx），是否要求第一条结果的标题或摘要中出现该年份（同一条文献年份一致）
REQUIRE_YEAR_IN_RESULT = True
# 时效性：近三年/近五年文献占比阈值（低于则提示）
RECENCY_MIN_RATIO_3Y = 0.20   # 近三年文献占比至少 20%
RECENCY_MIN_RATIO_5Y = 0.35   # 近五年文献占比至少 35%
RECENCY_MIN_REFS_FOR_CHECK = 5  # 参考文献总数不少于该值才做时效性检查
# 关联性：最多对多少条引用做 LLM 核查（避免超时与费用）
MAX_RELEVANCE_CHECKS = 10


def _extract_keywords(text: str) -> set[str]:
    if not text or len(text.strip()) < 4:
        return set()
    zh = set(re.findall(r"[\u4e00-\u9fff]{2,}", text))
    en = set(re.findall(r"[a-zA-Z0-9]{2,}", text))
    en = {w for w in en if not (len(w) <= 2 and w.isdigit())}
    return zh | en


def _extract_author_zone_keywords(text: str) -> set[str]:
    if not text or len(text.strip()) < 4:
        return set()
    head = text.strip()[:80]
    dot = head.find(". ")
    if dot == -1:
        dot = head.find(".")
    author_zone = head[:dot].strip() if dot > 0 else head
    if len(author_zone) < 2:
        return set()
    zh = set(re.findall(r"[\u4e00-\u9fff]{2,}", author_zone))
    en = set(re.findall(r"[a-zA-Z]{2,}", author_zone))
    return zh | en


def _extract_year(text: str) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"\b(19\d{2}|20\d{2})\b", text.strip()[:200])
    return int(m.group(1)) if m else None


def _top_result_is_relevant(search_q: str, organic_results: list) -> bool:
    if not organic_results:
        return False
    keywords = _extract_keywords(search_q)
    author_keywords = _extract_author_zone_keywords(search_q) if REQUIRE_AUTHOR_IN_TITLE else set()
    ref_year = _extract_year(search_q) if REQUIRE_YEAR_IN_RESULT else None
    min_need = 1 if len(keywords) < 2 else min(MIN_KEYWORDS_IN_TOP_RESULT, len(keywords))
    first = organic_results[0]
    title = (first.get("title") or "").strip()
    snippet = (first.get("snippet") or "").strip()
    combined = title + " " + snippet
    combined_lower = combined.lower()
    if not combined_lower.strip():
        return False
    matches = sum(1 for k in keywords if k.lower() in combined_lower or k in combined)
    if matches < min_need:
        return False
    if author_keywords:
        title_lower = title.lower()
        if not any(k.lower() in title_lower or k in title_lower for k in author_keywords):
            return False
    if ref_year is not None and str(ref_year) not in combined:
        return False
    return True


async def _scholar_search(search_q: str) -> tuple[bool, Optional[dict]]:
    if not SERPAPI_KEY or len(search_q.strip()) < 10:
        return True, None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                "https://serpapi.com/search",
                params={"engine": "google_scholar", "q": search_q.strip()[:200], "api_key": SERPAPI_KEY, "num": 3},
            )
            if r.status_code != 200:
                return False, None
            data = r.json()
            results = data.get("organic_results") or []
            if len(results) == 0:
                return False, None
            first = results[0]
            if not _top_result_is_relevant(search_q, results):
                return False, None
            return True, {"title": first.get("title") or "", "snippet": first.get("snippet") or ""}
    except Exception:
        return False, None


async def _scholar_has_results(search_q: str) -> bool:
    ok, _ = await _scholar_search(search_q)
    return ok


async def _fetch_paper_references(paper_id: str) -> list[tuple[int, str]]:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{BACKEND_URL.rstrip('/')}/api/papers/{paper_id}/references")
            if r.status_code != 200:
                return []
            data = r.json()
            refs = data.get("references") or []
            return [(int(x.get("index", i)), (x.get("text") or "").strip()) for i, x in enumerate(refs)]
    except Exception:
        return []


async def _fetch_paper_full(paper_id: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{BACKEND_URL.rstrip('/')}/api/papers/{paper_id}/full")
            if r.status_code != 200:
                return ""
            data = r.json()
            return (data.get("content") or "").strip()
    except Exception:
        return ""


def _extract_citing_sentences(full_text: str, ref_index: int, max_chars: int = 300) -> list[str]:
    if not full_text or ref_index < 1:
        return []
    pattern = r"\[\s*" + str(ref_index) + r"\s*(?:\s*,\s*\d+)*\s*\]"
    out = []
    for part in re.split(r"(?<=[。.!?；;\n])", full_text):
        if re.search(pattern, part):
            s = re.sub(r"\s+", " ", part).strip()
            if len(s) > 10:
                out.append(s[:max_chars])
    return list(dict.fromkeys(out))[:3]


def _llm_request_url_and_headers() -> tuple[Optional[str], Optional[dict]]:
    if LLM_BASE_URL:
        url = LLM_BASE_URL.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if LLM_API_KEY:
            headers["Authorization"] = f"Bearer {LLM_API_KEY}"
        return url, headers
    if DEEPSEEK_API_KEY:
        return "https://api.deepseek.com/chat/completions", {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        }
    return None, None


_llm_model_index = 0


def _choose_llm_model() -> str:
    global _llm_model_index
    if LLM_MODE != "multi" or not LLM_MODEL_LIST:
        return LLM_MODEL
    idx = _llm_model_index % len(LLM_MODEL_LIST)
    _llm_model_index += 1
    return LLM_MODEL_LIST[idx]


async def _llm_citation_relevance(citing_sentence: str, scholar_title: str, scholar_snippet: str) -> tuple[bool, str]:
    url, headers = _llm_request_url_and_headers()
    if not url or not citing_sentence.strip() or not (scholar_title or scholar_snippet):
        return True, ""
    prompt = f"""请判断：论文中的以下引用表述，是否可能出自给出的被引文献（标题与摘要片段）？若明显「挂羊头卖狗肉」或断章取义则答否。

【论文中的引用句】
{citing_sentence[:500]}

【被引文献在 Google Scholar 上的标题与摘要片段】
标题：{scholar_title[:300]}
摘要：{scholar_snippet[:500]}

请严格只输出两行：
第一行：是 或 否
第二行：若否，用一句话说明原因（若是一行可留空）。"""
    try:
        async with httpx.AsyncClient(timeout=DEEPSEEK_CONFIG.timeout_seconds) as client:
            r = await client.post(
                url,
                headers=headers,
                json={"model": _choose_llm_model(), "messages": [{"role": "user", "content": prompt}], "max_tokens": 150},
            )
            if r.status_code != 200:
                return True, ""
            data = r.json()
            text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
            lines = [t.strip() for t in text.split("\n") if t.strip()]
            first_line = (lines[0] if lines else "").strip()
            reason = lines[1] if len(lines) > 1 else ""
            if "否" in first_line or "不" in first_line:
                return False, reason[:200]
            return True, ""
    except Exception:
        return True, ""


def _is_references_section(content: str) -> bool:
    if not content or len(content.strip()) < 50:
        return False
    text = content.strip()
    if not re.search(r"#?\s*参考文献|References|REFERENCES", text, re.I):
        return False
    ref_lines = re.findall(r"^\[\s*\d+\s*\]\s*.+", text, re.MULTILINE)
    return len(ref_lines) >= 2


_REF_HEADING = re.compile(
    r"(?m)^(?:#+\s*)?(参考文献|References|REFERENCES)\s*$",
    re.I,
)


def _split_body_and_reference_tail(content: str) -> tuple[str, str]:
    """
    参考文献标题之前为正文；从标题起至文末（或致谢等）为参考文献区。
    未检出参考文献标题时返回 ("", "")，表示无法做「正文 vs 列表」编号对比。
    """
    if not content:
        return "", ""
    m = _REF_HEADING.search(content)
    if not m:
        m = re.search(r"(?:^|\n)\s*参考文献\s*\n", content)
    if not m:
        return "", ""
    body = content[: m.start()]
    tail = content[m.start() :]
    cut = re.search(
        r"(?m)^(?:#+\s*)?(致谢|附录|在学期间|攻读.{0,8}学位期间|个人简介)\s",
        tail,
    )
    if cut:
        tail = tail[: cut.start()]
    return body, tail


def _max_bracket_number_in_text(text: str) -> int:
    if not text:
        return 0
    nums = [int(x) for x in re.findall(r"\[\s*(\d+)\s*\]", text)]
    return max(nums) if nums else 0


def _citation_exceeds_reference_list(content: str) -> tuple[bool, int, int]:
    """
    仅在能切分出参考文献区，且列表中带 [n] 编号时，判断「参考文献节前正文」最大编号是否大于列表内最大编号。
    替代原先 max>30 的粗糙启发式，避免参考文献实际已够长却仍报「编号过大」。
    """
    body, tail = _split_body_and_reference_tail(content)
    if not tail:
        return False, 0, 0
    max_in_list = _max_bracket_number_in_text(tail)
    if max_in_list <= 0:
        return False, 0, 0
    max_in_body = _max_bracket_number_in_text(body)
    return (max_in_body > max_in_list, max_in_body, max_in_list)


def _parse_reference_list(content: str, max_items: int = MAX_REFERENCES_TO_CHECK) -> list[tuple[int, str]]:
    result = []
    parts = re.split(r"(\[\s*\d+\s*\])", content)
    for i in range(1, len(parts), 2):
        if i + 1 >= len(parts):
            break
        num_part = parts[i].strip()
        body = parts[i + 1].replace("\n", " ").strip()
        m = re.match(r"\[\s*(\d+)\s*\]", num_part)
        if not m or len(body) < 15:
            continue
        num = int(m.group(1))
        search_text = body[:120].strip()
        if search_text:
            result.append((num, search_text))
        if len(result) >= max_items:
            break
    return result


async def _external_citation_check(query: str) -> list:
    if not SERPAPI_KEY or len(query.strip()) < 10:
        return []
    search_q = query[:200].strip()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                "https://serpapi.com/search",
                params={"engine": "google_scholar", "q": search_q, "api_key": SERPAPI_KEY, "num": 3},
            )
            if r.status_code != 200:
                return []
            data = r.json()
            results = data.get("organic_results") or []
            if not results and ("引用" in query or "et al" in query.lower() or re.search(r"20\d{2}", query)):
                return ["未在 Google Scholar 检索到与片段中引文匹配的结果，请核对文献是否真实存在或拼写是否正确。"]
    except Exception:
        pass
    return []


async def _check_references_section(content: str) -> list:
    if not SERPAPI_KEY:
        return []
    refs = _parse_reference_list(content)
    if not refs:
        return []
    not_found = []
    for i, (num, search_text) in enumerate(refs):
        if i > 0:
            await asyncio.sleep(SERPAPI_DELAY_SEC)
        has = await _scholar_has_results(search_text)
        if not has:
            not_found.append(num)
    if not not_found:
        return []
    if len(not_found) <= 10:
        nums_str = "、".join(str(n) for n in not_found)
        return [f"以下编号的文献在 Google Scholar 未检索到，请核对是否真实存在或拼写正确：[{nums_str}]"]
    nums_str = "、".join(str(n) for n in not_found[:10])
    return [f"以下编号的文献在 Google Scholar 未检索到，请核对是否真实存在或拼写正确：[{nums_str}] 等共 {len(not_found)} 条"]


async def _check_references_from_db(paper_id: str) -> list:
    if not SERPAPI_KEY:
        return ["未配置 SerpAPI（SERPAPI_KEY），无法校验文献真实性。请在环境变量中配置后重启 Citation_Agent。"]
    refs = await _fetch_paper_references(paper_id)
    if not refs:
        return ["未找到该论文的参考文献列表。请确认论文目录下存在 paper/hybrid_auto/references.json，并重新运行导入脚本后再发起审计。"]
    refs = refs[:MAX_REFERENCES_TO_CHECK]
    issues = []
    not_found = []
    refs_with_result = []
    for i, (num, text) in enumerate(refs):
        if i > 0:
            await asyncio.sleep(SERPAPI_DELAY_SEC)
        search_text = (text or "")[:REF_SEARCH_LEN].strip()
        if len(search_text) < 15:
            continue
        has, first_result = await _scholar_search(search_text)
        if not has:
            not_found.append(num)
        elif first_result:
            refs_with_result.append((num, text, first_result))
    if not_found:
        if len(not_found) <= 10:
            nums_str = "、".join(str(n) for n in not_found)
            issues.append(f"真实性校验：以下编号的文献在 Google Scholar 未检索到或作者/年份/篇名与检索结果不一致，请核对是否真实存在或拼写正确：[{nums_str}]")
        else:
            nums_str = "、".join(str(n) for n in not_found[:10])
            issues.append(f"真实性校验：以下编号的文献在 Google Scholar 未检索到或与检索结果不一致，请核对：[{nums_str}] 等共 {len(not_found)} 条")
    current_year = datetime.now().year
    year_3y = current_year - 3
    year_5y = current_year - 5
    years = [_extract_year(text) for _, text in refs]
    years = [y for y in years if y is not None]
    total_with_year = len(years)
    if total_with_year >= RECENCY_MIN_REFS_FOR_CHECK:
        count_3y = sum(1 for y in years if y >= year_3y)
        count_5y = sum(1 for y in years if y >= year_5y)
        ratio_3y = count_3y / total_with_year
        ratio_5y = count_5y / total_with_year
        if ratio_3y < RECENCY_MIN_RATIO_3Y:
            issues.append(f"时效性评估：近三年文献占比偏低（{count_3y}/{total_with_year}，约 {ratio_3y*100:.0f}%），建议补充近 3–5 年相关研究。")
        if ratio_5y < RECENCY_MIN_RATIO_5Y:
            issues.append(f"时效性评估：近五年文献占比偏低（{count_5y}/{total_with_year}，约 {ratio_5y*100:.0f}%），建议补充近期文献。")
    url, _ = _llm_request_url_and_headers()
    if url and refs_with_result:
        full_text = await _fetch_paper_full(paper_id)
        if full_text:
            checked = 0
            for num, text, first_result in refs_with_result:
                if checked >= MAX_RELEVANCE_CHECKS:
                    break
                sentences = _extract_citing_sentences(full_text, num)
                if not sentences:
                    continue
                scholar_title = first_result.get("title") or ""
                scholar_snippet = first_result.get("snippet") or ""
                ok, reason = await _llm_citation_relevance(sentences[0], scholar_title, scholar_snippet)
                checked += 1
                if not ok:
                    issues.append(f"关联性核查：文献 [{num}] 的引用可能与原文表述不符，建议核对。{reason}")
                await asyncio.sleep(0.3)
    return issues


async def _check_single_reference(paper_id: str, ref_index_1based: int) -> dict:
    if not SERPAPI_KEY:
        return {
            "score": 60,
            "audit_level": "Warning",
            "comment": f"第 {ref_index_1based} 条：未配置 SERPAPI_KEY，无法校验。",
            "suggestion": "请在环境变量中配置 SERPAPI_KEY 后重启 Citation_Agent。",
            "tags": ["Citation_NotChecked"],
        }
    refs = await _fetch_paper_references(paper_id)
    ref_at = next((r for r in refs if r[0] == ref_index_1based), None)
    if not ref_at:
        return {
            "score": 60,
            "audit_level": "Warning",
            "comment": f"第 {ref_index_1based} 条：未找到该编号的参考文献。",
            "suggestion": None,
            "tags": ["Citation_NotFound"],
        }
    num, text = ref_at
    search_text = (text or "")[:REF_SEARCH_LEN].strip()
    if len(search_text) < 15:
        return {
            "score": 83,
            "audit_level": "Info",
            "comment": f"第 {num} 条：正文过短，已跳过 Scholar 校验。",
            "suggestion": None,
            "tags": ["Citation_Skipped"],
        }
    has, _ = await _scholar_search(search_text)
    if not has:
        return {
            "score": 60,
            "audit_level": "Warning",
            "comment": f"第 {num} 条：在 Google Scholar 未检索到或第一条结果不相关，请核对是否真实存在或拼写正确。",
            "suggestion": "可补充 DOI 或规范引用格式后再试。",
            "tags": ["Fake_Reference"],
        }
    return {
        "score": 83,
        "audit_level": "Info",
        "comment": f"第 {num} 条：通过。",
        "suggestion": None,
        "tags": ["Citation_OK"],
    }


async def _fetch_rag_comments(query: str) -> list:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"{BACKEND_URL.rstrip('/')}/api/rag/expert-comments",
                json={"query": query[:2000], "top_k": RAG_TOP_K},
            )
            if r.status_code == 200:
                return (r.json() or {}).get("comments") or []
    except Exception:
        pass
    return []


class RequestMetadata(BaseModel):
    paper_id: str
    paper_title: Optional[str] = None
    chunk_id: str


class RequestPayload(BaseModel):
    content: str
    context_before: Optional[str] = ""
    context_after: Optional[str] = ""


class RequestConfig(BaseModel):
    temperature: float = 0.1
    max_tokens: int = 500
    enable_rules: bool = True


class AgentAuditRequest(BaseModel):
    request_id: str
    metadata: RequestMetadata
    payload: RequestPayload
    config: Optional[RequestConfig] = None


async def audit_citation_basic_async(content: str) -> dict:
    """关闭细则时：不做 SerpAPI/复杂正则规则，仅做引用形态速览。"""
    n = len(content or "")
    has_refs_sec = ("参考文献" in content) or (re.search(r"\bReferences\b", content or "") is not None)
    brackets = len(re.findall(r"\[\s*\d+\s*\]", content or ""))
    desc = (
        f"基本模式：正文约 {n} 字；"
        f"{'检测到参考文献相关章节标题' if has_refs_sec else '未明显检测到「参考文献」类标题'}；"
        f"编号引用形式 [n] 约 {brackets} 处。"
    )
    score = 78 if (has_refs_sec and brackets > 0) else 72
    level = "Pass" if score >= 75 else "Warning"
    audit_results = [
        {
            "point": "文献引用速览（基本模式）",
            "score": score,
            "level": level,
            "description": desc,
            "evidence_quote": (content or "")[:120].replace("\n", " "),
            "suggestion": "开启「审计细则」后可进行混排检测、外部文献校验等完整检查。",
        }
    ]
    return {
        "score": score,
        "audit_level": "Info" if level == "Pass" else "Warning",
        "comment": desc,
        "suggestion": audit_results[0]["suggestion"],
        "audit_results": audit_results,
        "tags": ["Citation_OK"],
    }


async def audit_citation_async(content: str, context_before: str, context_after: str) -> dict:
    issues = []
    level = "Info"
    score = 83
    audit_results = []

    m_mixed = re.search(r"(\[\s*\d+\s*\]|\(\s*\w+\s+et\s+al\.?\s*,?\s*20\d{2}\s*\))", content)
    if re.search(r"\[\s*\d+\s*\]", content) and re.search(r"\(\s*\w+\s+et\s+al\.?\s*,?\s*20\d{2}\s*\)", content):
        issues.append("中英文引用格式混用（[1] 与 (Author et al., 年份)）")
        ev = content[max(0, m_mixed.start()-30):m_mixed.end()+30] if m_mixed else "N/A"
        audit_results.append({
            "point": "引用格式检查",
            "score": 60,
            "level": "Warning",
            "description": "中英文引用格式混用（[1] 与 (Author et al., 年份)）",
            "evidence_quote": ev,
            "suggestion": "建议统一引用格式"
        })

    m_bracket = re.search(r"【.*?】", content)
    if "【" in content or "】" in content:
        issues.append("使用【】标注引用，建议改为 [1] 或 (Author, Year)")
        ev = content[max(0, m_bracket.start()-30):m_bracket.end()+30] if m_bracket else "N/A"
        audit_results.append({
            "point": "引用符号检查",
            "score": 60,
            "level": "Warning",
            "description": "使用【】标注引用，建议改为 [1] 或 (Author, Year)",
            "evidence_quote": ev,
            "suggestion": "替换为标准引用符号"
        })

    exceed, max_body, max_list = _citation_exceeds_reference_list(content)
    if exceed:
        msg = (
            f"正文（参考文献节前）引用编号最大为 [{max_body}]，"
            f"参考文献区内编号最大为 [{max_list}]，请核对是否漏列或章节切分有误"
        )
        issues.append(msg)
        body_only, _ = _split_body_and_reference_tail(content)
        m_ref = re.search(r"\[\s*" + str(max_body) + r"\s*\]", body_only or content)
        ev = (
            content[max(0, m_ref.start() - 30) : m_ref.end() + 30]
            if m_ref
            else "N/A"
        )
        audit_results.append({
            "point": "引用编号检查",
            "score": 60,
            "level": "Warning",
            "description": msg,
            "evidence_quote": ev,
            "suggestion": "核对参考文献列表与正文引用是否一一对应",
        })

    if SERPAPI_KEY:
        if _is_references_section(content):
            new_issues = await _check_references_section(content)
            issues.extend(new_issues)
            for iss in new_issues:
                audit_results.append({
                    "point": "参考文献校验",
                    "score": 60,
                    "level": "Warning",
                    "description": iss,
                    "evidence_quote": content[:100], # 参考文献片段取开头
                    "suggestion": "请核对参考文献"
                })
        elif "et al" in content or re.search(r"20\d{2}", content):
            new_issues = await _external_citation_check(content)
            issues.extend(new_issues)
            for iss in new_issues:
                audit_results.append({
                    "point": "外部文献校验",
                    "score": 60,
                    "level": "Warning",
                    "description": iss,
                    "evidence_quote": content[:100],
                    "suggestion": "请核对外部文献"
                })

    if issues:
        level = "Warning"
        score = max(72, score - len(issues) * 4)
    comment = "；".join(issues) if issues else "引用格式基本规范，未发现明显虚假或格式错误。"
    suggestion = "建议统一引用格式并核对参考文献列表与正文一一对应；可疑文献可提供 DOI。" if issues else None

    return {
        "score": score,
        "audit_level": level,
        "comment": comment,
        "suggestion": suggestion,
        "audit_results": audit_results,
        "tags": ["Citation_Inconsistency", "Fake_Reference"] if issues else ["Citation_OK"]
    }


@app.post("/audit")
async def audit(request: AgentAuditRequest, background_tasks: BackgroundTasks):
    p = request.payload
    meta = request.metadata
    ref_match = re.match(r"^__ref_(\d+)__$", (meta.chunk_id or "").strip())
    if ref_match:
        ref_index = int(ref_match.group(1))
        result = await _check_single_reference(meta.paper_id, ref_index)
        resp = {"request_id": request.request_id, "agent_info": {"name": "Citation_Agent", "version": "v1.0"}, "result": result, "usage": {"tokens": None, "latency_ms": None}}
        background_tasks.add_task(save_audit_result_to_db, request.request_id, meta.paper_id, meta.chunk_id, resp)
        return resp
    if meta.chunk_id == "__references__":
        issues = await _check_references_from_db(meta.paper_id)
        level = "Warning" if issues else "Info"
        score = 83 if not issues else max(60, 83 - len(issues) * 4)
        comment = "；".join(issues) if issues else "引用格式基本规范，未发现明显虚假或格式错误。"
        suggestion = "建议统一引用格式并核对参考文献列表与正文一一对应；可疑文献可提供 DOI。" if issues else None
        result = {"score": score, "audit_level": level, "comment": comment, "suggestion": suggestion, "tags": ["Citation_Inconsistency", "Fake_Reference"] if issues else ["Citation_OK"]}
        rag = await _fetch_rag_comments("参考文献真实性")
        if rag:
            refs_txt = "；".join((c.get("text") or "")[:200] for c in rag if c.get("text"))
            if refs_txt:
                result["comment"] = result["comment"] + " 【参考专家意见】 " + refs_txt
        resp = {"request_id": request.request_id, "agent_info": {"name": "Citation_Agent", "version": "v1.0"}, "result": result, "usage": {"tokens": None, "latency_ms": None}}
        background_tasks.add_task(save_audit_result_to_db, request.request_id, meta.paper_id, meta.chunk_id, resp)
        return resp
    cfg = request.config
    use_rules = bool(getattr(cfg, "enable_rules", True)) if cfg is not None else True
    if use_rules:
        result = await audit_citation_async(p.content, p.context_before or "", p.context_after or "")
    else:
        result = await audit_citation_basic_async(p.content)
    rag = await _fetch_rag_comments(p.content)
    if rag:
        refs_txt = "；".join((c.get("text") or "")[:200] for c in rag if c.get("text"))
        if refs_txt:
            result["comment"] = result["comment"] + " 【参考专家意见】 " + refs_txt
    resp = {"request_id": request.request_id, "agent_info": {"name": "Citation_Agent", "version": "v1.0"}, "result": result, "usage": {"tokens": None, "latency_ms": None}}
    background_tasks.add_task(save_audit_result_to_db, request.request_id, meta.paper_id, meta.chunk_id, resp)
    return resp


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "Citation_Agent"}
