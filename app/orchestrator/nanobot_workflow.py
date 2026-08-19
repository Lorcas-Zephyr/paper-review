from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

try:
    import nanobot as _nanobot  # type: ignore
except Exception:
    _nanobot = None


logger = logging.getLogger(__name__)


class NanobotWorkflowRunner:
    """
    Nanobot-style workflow runner.

    It preserves existing HTTP contracts while moving orchestration into a
    workflow abstraction that supports event-driven agent collaboration.
    """

    def __init__(
        self,
        *,
        agent_endpoints: Dict[str, Dict[str, Any]],
        event_bus: Any,
        call_agent: Callable[..., Awaitable[Dict[str, Any]]],
        aggregate_results: Callable[[List[Dict[str, Any]], str, str, str], Dict[str, Any]],
        run_reflection: Callable[..., Awaitable[Optional[Dict[str, Any]]]],
        merge_reflection: Callable[[Dict[str, Any], Optional[Dict[str, Any]]], Dict[str, Any]],
        max_retries: int = 1,
    ) -> None:
        self.agent_endpoints = agent_endpoints
        self.event_bus = event_bus
        self.call_agent = call_agent
        self.aggregate_results = aggregate_results
        self.run_reflection = run_reflection
        self.merge_reflection = merge_reflection
        self.max_retries = max_retries
        self.backend = "nanobot" if _nanobot is not None else "builtin_compatible"

        self.stage_order = [
            ["logic_agent", "citation_agent", "format_agent"],
            ["experiment_agent"],
        ]

    async def run(
        self,
        *,
        paper_title: str,
        paper_content: str,
        paper_id: str,
        request_id: str,
        submission_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        cfg = submission_config if isinstance(submission_config, dict) else {}
        self.event_bus.reset_request(request_id)
        all_results: List[Dict[str, Any]] = []

        for stage_idx, stage_agents in enumerate(self.stage_order):
            logger.info(
                "nanobot stage start request_id=%s stage=%s agents=%s",
                request_id,
                stage_idx,
                ",".join(stage_agents),
            )
            stage_results = await self._run_stage(
                stage_agents,
                paper_title=paper_title,
                paper_content=paper_content,
                paper_id=paper_id,
                request_id=request_id,
                submission_config=cfg,
            )
            all_results.extend(stage_results)

        base_report = self.aggregate_results(all_results, paper_title, request_id, paper_id)
        raw_dialogue = cfg.get("enable_mentor_dialogue")
        reflection_enable_dialogue = None if raw_dialogue is None else bool(raw_dialogue)
        reflection_dict = await self.run_reflection(
            paper_id,
            paper_title,
            paper_content,
            all_results,
            self.agent_endpoints,
            enable_dialogue=reflection_enable_dialogue,
        )
        merged_report = self.merge_reflection(base_report, reflection_dict)
        merged_report["agent_events"] = self.event_bus.snapshot(request_id)
        merged_report["scheduler_backend"] = self.backend
        return {
            "all_results": all_results,
            "aggregated_report": merged_report,
        }

    async def _run_stage(
        self,
        stage_agents: List[str],
        *,
        paper_title: str,
        paper_content: str,
        paper_id: str,
        request_id: str,
        submission_config: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        tasks = [
            self._run_single_agent_with_retry(
                agent_name,
                paper_title=paper_title,
                paper_content=paper_content,
                paper_id=paper_id,
                request_id=request_id,
                submission_config=submission_config,
            )
            for agent_name in stage_agents
        ]
        return await asyncio.gather(*tasks)

    async def _run_single_agent_with_retry(
        self,
        agent_name: str,
        *,
        paper_title: str,
        paper_content: str,
        paper_id: str,
        request_id: str,
        submission_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        agent_config = self.agent_endpoints[agent_name]
        attempt = 0
        last_result: Dict[str, Any] = {
            "agent_name": agent_name,
            "group_id": agent_config.get("group_id"),
            "weight": agent_config.get("weight", 1.0),
            "status": "FAILED",
            "error": "agent not executed",
        }

        while attempt <= self.max_retries:
            interaction_events = self.event_bus.get_events_for_agent(agent_name, request_id)
            result = await self.call_agent(
                agent_name,
                agent_config,
                paper_title,
                paper_content,
                paper_id,
                request_id,
                submission_config,
                interaction_events,
            )
            last_result = result
            status_val = getattr(result.get("status"), "value", result.get("status"))
            if status_val == "SUCCESS":
                self.event_bus.publish(
                    event_type=f"agent.findings.{agent_name}",
                    request_id=request_id,
                    paper_id=paper_id,
                    producer_agent=agent_name,
                    payload={
                        "score": result.get("score"),
                        "audit_level": result.get("audit_level"),
                        "comment": result.get("comment"),
                        "suggestion": result.get("suggestion"),
                    },
                    trace_id=request_id,
                    idempotency_key=f"{request_id}:{agent_name}:success",
                )
                return result

            if attempt >= self.max_retries:
                break
            attempt += 1
            logger.warning(
                "nanobot agent retry request_id=%s agent=%s attempt=%s",
                request_id,
                agent_name,
                attempt,
            )
            await asyncio.sleep(0.5)
        return last_result
