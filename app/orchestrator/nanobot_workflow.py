"""AI-planned workflow runner.

`NanobotWorkflowRunner` keeps the existing orchestrator callback contract but
delegates task selection and execution to the constrained planner/runtime
stack.  The name is retained for API compatibility with the existing service.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Awaitable, Callable, Dict, List, Optional

from agent_registry import AgentRegistry
from agent_runtime import AgentRuntime
from blackboard import SharedBlackboard
from planner import AIPlanner

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


class NanobotWorkflowRunner:
    """Plan and execute a paper review using one collaborative runtime."""

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
        planner: Optional[AIPlanner] = None,
    ) -> None:
        self.agent_endpoints = agent_endpoints
        self.event_bus = event_bus
        self.call_agent = call_agent
        self.aggregate_results = aggregate_results
        self.run_reflection = run_reflection
        self.merge_reflection = merge_reflection
        self.max_retries = max_retries
        self.registry = AgentRegistry.from_endpoints(agent_endpoints)
        self.planner = planner or AIPlanner(
            self.registry,
            max_tasks=_env_int("AI_PLANNER_MAX_TASKS", 12),
            max_consultation_rounds=_env_int("AI_PLANNER_MAX_ROUNDS", 3),
            timeout=_env_float("AI_PLANNER_TIMEOUT", 45.0),
        )
        self.runtime = AgentRuntime(
            registry=self.registry,
            event_bus=event_bus,
            call_agent=call_agent,
            max_retries=max_retries,
        )
        # This is intentionally explicit in reports so operators can tell the
        # AI planner/runtime path from the historical fixed-stage path.
        self.backend = "ai_planner_runtime"

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

        plan = await self.planner.plan(
            paper_title=paper_title,
            paper_content=paper_content,
            paper_id=paper_id,
            request_id=request_id,
            config=cfg,
        )
        blackboard = SharedBlackboard(
            paper_id=paper_id,
            request_id=request_id,
            paper_context={"title": paper_title, "content_length": len(paper_content or "")},
        )
        runtime_result = await self.runtime.run(
            plan=plan,
            paper_title=paper_title,
            paper_content=paper_content,
            paper_id=paper_id,
            request_id=request_id,
            submission_config=cfg,
            blackboard=blackboard,
        )
        all_results = runtime_result["results"]
        # Consultation calls are useful evidence but must not count as a second
        # weighted audit for the same dimension in the legacy aggregator.
        primary_results = [result for result in all_results if not result.get("is_consultation")]
        base_report = self.aggregate_results(primary_results, paper_title, request_id, paper_id)
        raw_dialogue = cfg.get("enable_mentor_dialogue")
        reflection_enable_dialogue = None if raw_dialogue is None else bool(raw_dialogue)
        reflection_dict = await self.run_reflection(
            paper_id,
            paper_title,
            paper_content,
            primary_results,
            self.agent_endpoints,
            enable_dialogue=reflection_enable_dialogue,
        )
        merged_report = self.merge_reflection(base_report, reflection_dict)
        merged_report["agent_events"] = self.event_bus.snapshot(request_id)
        merged_report["blackboard"] = runtime_result["blackboard"]
        merged_report["plan"] = plan.as_dict()
        merged_report["planner_metadata"] = dict(self.planner.last_metadata)
        merged_report["runtime"] = {
            "rounds": runtime_result["rounds"],
            "consultation_count": runtime_result["consultation_count"],
            "completed_tasks": runtime_result["completed_tasks"],
        }
        merged_report["scheduler_backend"] = self.backend
        return {
            "all_results": all_results,
            "primary_results": primary_results,
            "aggregated_report": merged_report,
            "plan": plan.as_dict(),
            "planner_status": dict(self.planner.last_metadata),
            "round": runtime_result["rounds"],
            "consultation_count": runtime_result["consultation_count"],
            "scheduler_backend": self.backend,
        }
