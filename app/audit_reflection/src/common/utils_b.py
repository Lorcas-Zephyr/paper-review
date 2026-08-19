import math
import re
from difflib import SequenceMatcher
from typing import Any, List, Tuple

# 与前端 App.js 一致：去掉开头的 [xxx] 审计点标签后再比较，避免多 Agent 同一条问题因前缀不同而无法去重
_ISSUE_BRACKET_PREFIX_RE = re.compile(r"^(\[.*?\]\s*)+")

_PUNCT_RE = re.compile(r"[\s\u3000]+")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def normalize_text(text: str) -> str:
    """统一文本格式，提升精确匹配鲁棒性。"""
    text = text.strip().lower()
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = _PUNCT_RE.sub(" ", text)
    return text


def similarity_ratio(a: str, b: str) -> float:
    """文本相似度（编辑距离比例）。"""
    return SequenceMatcher(None, a, b).ratio()


def extract_numbers(text: str) -> List[str]:
    """抽取数字，用于数字一致性校验。"""
    return _NUM_RE.findall(text)


def extract_terms(text: str) -> List[str]:
    """抽取术语，用于关键术语一致性校验。"""
    terms = re.findall(r"[A-Za-z][A-Za-z0-9\-_/]{2,}", text)
    terms += re.findall(r"[\u4e00-\u9fff]{2,}", text)
    return list({t for t in terms if len(t) >= 3})


def cosine_sim(a: List[float], b: List[float]) -> float:
    """计算余弦相似度。"""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def argmax_topk(values: List[float], k: int) -> List[int]:
    """取前K个最大值的索引。"""
    indexed = list(enumerate(values))
    indexed.sort(key=lambda x: x[1], reverse=True)
    return [i for i, _ in indexed[:k]]


def find_substring_span(haystack: str, needle: str) -> Tuple[int, int]:
    """返回子串在原文中的位置区间。"""
    idx = haystack.find(needle)
    if idx == -1:
        return (0, max(0, len(haystack)))
    return (idx, idx + len(needle))


def issue_description_dedup_key(description: str) -> str:
    """用于问题列表去重：去掉 [审计点] 前缀并规范化空白与引号等。"""
    if not description:
        return ""
    s = str(description).strip()
    s = _ISSUE_BRACKET_PREFIX_RE.sub("", s)
    return normalize_text(s)


def dedupe_prioritized_issues(issues_list: List[Any]) -> List[Any]:
    """
    按规范化后的正文去重；合并重复项的 agents 列表。
    支持 PrioritizedIssue（Pydantic）与 dict。
    """
    if not issues_list:
        return []

    by_key: dict = {}
    order: List[str] = []

    for iss in issues_list:
        if isinstance(iss, dict):
            desc = iss.get("description") or ""
            agents = list(iss.get("agents") or [])
        else:
            desc = getattr(iss, "description", "") or ""
            agents = list(getattr(iss, "agents", None) or [])

        key = issue_description_dedup_key(desc)
        if not key:
            key = desc.strip() or "__empty_issue__"

        if key not in by_key:
            by_key[key] = iss
            order.append(key)
            continue

        existing = by_key[key]
        if isinstance(existing, dict) and isinstance(iss, dict):
            ea = list(existing.get("agents") or [])
            merged = list(dict.fromkeys([a for a in ea + agents if a]))
            by_key[key] = {**existing, "agents": merged}
        elif hasattr(existing, "model_copy"):
            ea = list(getattr(existing, "agents", None) or [])
            merged = list(dict.fromkeys([a for a in ea + agents if a]))
            by_key[key] = existing.model_copy(update={"agents": merged})

    return [by_key[k] for k in order]
