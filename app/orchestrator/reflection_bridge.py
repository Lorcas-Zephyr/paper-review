"""
通过 audit_reflection HTTP 服务（默认 :8009）的 /api/evaluate/inline 执行反思评估，
与直接 import run 等价，便于独立部署与扩缩容。

环境变量：REFLECTION_API_URL（默认 http://127.0.0.1:8009）
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

REFLECTION_API_URL = os.environ.get("REFLECTION_API_URL", "http://127.0.0.1:8009").rstrip("/")


def _status_ok(r: Dict[str, Any]) -> bool:
    s = r.get("status")
    if s is None:
        return False
    v = getattr(s, "value", s)
    return v == "SUCCESS"


def build_audit_results_for_reflection(
    all_results: List[Dict[str, Any]],
    agent_endpoints: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """将调度器各 Agent 输出转为 audit_reflection.review_engine 可消费的 group 列表。"""
    groups: List[Dict[str, Any]] = []
    for r in all_results:
        if not _status_ok(r):
            continue
        gid = r.get("group_id")
        aname = r.get("agent_name", "")
        cfg = agent_endpoints.get(aname, {})
        gname = cfg.get("description", f"Group_{gid}")

        # 尝试从 raw_response 或 response_text 提取细粒度的 audit_results
        response_data = r.get("raw_response") or r.get("response_text") or {}

        audit_results = None
        if isinstance(response_data, dict):
            if "audit_results" in response_data and isinstance(response_data["audit_results"], list):
                audit_results = response_data["audit_results"]
            elif "result" in response_data and isinstance(response_data["result"], dict):
                if "audit_results" in response_data["result"] and isinstance(response_data["result"]["audit_results"], list):
                    audit_results = response_data["result"]["audit_results"]

        if audit_results is not None and len(audit_results) > 0:
            # 代理返回了细粒度结果，直接使用
            groups.append({
                "group_id": str(gid),
                "group_name": gname,
                "audit_results": audit_results,
            })
        else:
            # 代理没有细粒度结果，构造一个汇总结果
            comment = r.get("comment") or "无描述"
            item = {
                "id": f"item-{gid}-full",
                "point": f"{gname}全文审计",
                "score": r.get("score", 0),
                "level": r.get("audit_level", "Info"),
                "description": comment,
                "evidence_quote": "",  # 设置为空，避免被视为证据引用去进行幻觉检查
                "suggestion": r.get("suggestion") or "",
            }
            groups.append({
                "group_id": str(gid),
                "group_name": gname,
                "audit_results": [item],
            })
    return groups


async def run_reflection_evaluation(
    paper_id: str,
    paper_title: str,
    paper_content: str,
    all_results: List[Dict[str, Any]],
    agent_endpoints: Dict[str, Any],
    enable_dialogue: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    """POST REFLECTION_API_URL/api/evaluate/inline，返回 ReflectionResult 字典。"""
    audit_groups = build_audit_results_for_reflection(all_results, agent_endpoints)
    if not audit_groups:
        logger.info("audit_reflection: 无成功审计结果，跳过反思评估")
        return None

    url = f"{REFLECTION_API_URL}/api/evaluate/inline"
    if enable_dialogue is None:
        enable_dialogue = os.environ.get("REFLECTION_ENABLE_DIALOGUE", "true").lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

    payload = {
        "paper_id": paper_id,
        "paper_title": paper_title,
        "paper_content": paper_content,
        "audit_groups": audit_groups,
        "enable_dialogue": enable_dialogue,
    }
    timeout = float(os.environ.get("REFLECTION_API_TIMEOUT", "600"))

    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code >= 400:
            logger.error(
                "反思 API 返回错误: %s %s",
                resp.status_code,
                (resp.text or "")[:800],
            )
            return None
        body = resp.json()
    except httpx.RequestError as e:
        logger.exception("无法连接反思评估服务 %s: %s", url, e)
        return None
    except Exception:
        logger.exception("调用反思 API 失败")
        return None

    if not body.get("success"):
        logger.error("反思 API success=false: %s", body)
        return None

    data = body.get("data")
    if not isinstance(data, dict):
        logger.error("反思 API 未返回 data 对象: %s", body)
        return None
    return data


def merge_aggregate_and_reflection(
    base_report: Dict[str, Any],
    reflection_dict: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """保留各 Agent 明细，并用反思评估覆盖综合分与结论文本。"""
    merged = {**base_report}
    if not reflection_dict:
        return merged

    merged["reflection"] = reflection_dict
    plugin = reflection_dict.get("plugin_metadata") or {}
    if "error" in plugin:
        merged["reflection_error"] = plugin.get("error")
        return merged

    fs = reflection_dict.get("final_score")
    if fs is not None:
        try:
            merged["overall_score"] = float(fs)
        except (TypeError, ValueError):
            pass

    merged["verdict"] = reflection_dict.get("verdict")
    merged["critical_issues"] = reflection_dict.get("critical_issues") or []
    merged["major_issues"] = reflection_dict.get("major_issues") or []
    merged["minor_issues"] = reflection_dict.get("minor_issues") or []
    merged["needs_human_review"] = reflection_dict.get("needs_human_review")
    merged["human_review_reason"] = reflection_dict.get("human_review_reason")
    merged["markdown_report_path"] = plugin.get("markdown_report_path")
    merged["reflection_ready"] = True
    merged["reflection_via"] = "http_api"
    return merged
