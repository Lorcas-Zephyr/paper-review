"""DAG runtime that executes planner tasks and enables agent consultation."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional

from agent_registry import AgentRegistry
from blackboard import SharedBlackboard
from planner import PlanTask, PlannerPlan

AgentCaller = Callable[..., Awaitable[Dict[str, Any]]]


def _status(value: Any) -> str:
    return str(getattr(value, "value", value)).upper()


class AgentRuntime:
    """Execute a validated plan while keeping collaboration auditable."""

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        event_bus: Any,
        call_agent: AgentCaller,
        max_events: int = 500,
        max_retries: int = 1,
    ) -> None:
        self.registry = registry
        self.event_bus = event_bus
        self.call_agent = call_agent
        self.max_events = max_events
        self.max_retries = max(0, int(max_retries))

    async def run(
        self,
        *,
        plan: PlannerPlan,
        paper_title: str,
        paper_content: str,
        paper_id: str,
        request_id: str,
        submission_config: Optional[Mapping[str, Any]],
        blackboard: SharedBlackboard,
    ) -> Dict[str, Any]:
        cfg = dict(submission_config or {})
        task_by_id = {task.task_id: task for task in plan.tasks}
        completed: Dict[str, Dict[str, Any]] = {}
        remaining = set(task_by_id)
        results: List[Dict[str, Any]] = []
        rounds = 0
        consultation_count = 0
        processed_consultations = set()

        while remaining:
            ready = [
                task_by_id[task_id]
                for task_id in remaining
                if all(dep in completed for dep in task_by_id[task_id].depends_on)
            ]
            if not ready:
                raise RuntimeError("validated plan became unschedulable")
            ready.sort(key=lambda task: task.task_id)
            batch = await asyncio.gather(
                *[
                    self._execute_task(
                        task,
                        paper_title=paper_title,
                        paper_content=paper_content,
                        paper_id=paper_id,
                        request_id=request_id,
                        base_config=cfg,
                        blackboard=blackboard,
                    )
                    for task in ready
                ]
            )
            for task, result in zip(ready, batch):
                result = dict(result or {})
                result.setdefault("agent_name", task.agent)
                result["task_id"] = task.task_id
                result["objective"] = task.objective
                completed[task.task_id] = result
                results.append(result)
                remaining.remove(task.task_id)
                self._publish_result(task, result, paper_id, request_id, blackboard)

            # Consultation is opt-in through a structured field in an Agent result.
            # This keeps the default path bounded while allowing genuine dialogue.
            if rounds < plan.consultation_rounds:
                requests = self._new_consultation_requests(blackboard, processed_consultations)
                for request in requests:
                    processed_consultations.add(request.get("request_id"))
                    consultation_count += 1
                    if consultation_count > self.max_events:
                        break
                    target = request.get("to_agent")
                    if not target or not self.registry.contains(target):
                        continue
                    consultation_task = PlanTask(
                        task_id=f"consult-{consultation_count}",
                        agent=target,
                        objective=str(request.get("question") or request.get("topic") or "Answer the consultation request"),
                        depends_on=[],
                        required=False,
                        max_retries=1,
                    )
                    answer = await self._execute_task(
                        consultation_task,
                        paper_title=paper_title,
                        paper_content=paper_content,
                        paper_id=paper_id,
                        request_id=request_id,
                        base_config={**cfg, "_consultation_request": request},
                        blackboard=blackboard,
                    )
                    answer = dict(answer or {})
                    answer.update({"task_id": consultation_task.task_id, "is_consultation": True, "consultation_request_id": request.get("request_id")})
                    results.append(answer)
                    blackboard.respond_consultation(
                        request_id=str(request.get("request_id")),
                        from_agent=target,
                        answer=answer,
                    )
                    self._publish_result(consultation_task, answer, paper_id, request_id, blackboard, event_type="agent.consultation_response")
                rounds += 1

        return {
            "results": results,
            "rounds": rounds,
            "consultation_count": consultation_count,
            "completed_tasks": list(completed),
            "blackboard": blackboard.snapshot(),
        }

    async def _execute_task(
        self,
        task: PlanTask,
        *,
        paper_title: str,
        paper_content: str,
        paper_id: str,
        request_id: str,
        base_config: Mapping[str, Any],
        blackboard: SharedBlackboard,
    ) -> Dict[str, Any]:
        spec = self.registry.get(task.agent)
        config = dict(spec.config)
        config.setdefault("timeout", spec.timeout)
        attempts = 0
        last: Dict[str, Any] = {
            "agent_name": task.agent,
            "status": "FAILED",
            "error": "agent not executed",
        }
        retry_limit = min(task.max_retries, self.max_retries)
        while attempts <= retry_limit:
            runtime_config = dict(base_config)
            runtime_config["_planner_task"] = task.as_dict()
            runtime_config["_blackboard_context"] = blackboard.context_for_agent(task.agent)
            events = self.event_bus.get_events_for_agent(task.agent, request_id)
            try:
                last = await self.call_agent(
                    task.agent,
                    config,
                    paper_title,
                    paper_content,
                    paper_id,
                    request_id,
                    runtime_config,
                    events,
                )
            except Exception as exc:
                last = {"agent_name": task.agent, "status": "FAILED", "error": str(exc)}
            if _status(last.get("status")) == "SUCCESS":
                return last
            if attempts >= retry_limit:
                break
            attempts += 1
            await asyncio.sleep(min(0.5 * attempts, 2.0))
        return last

    def _publish_result(
        self,
        task: PlanTask,
        result: Dict[str, Any],
        paper_id: str,
        request_id: str,
        blackboard: SharedBlackboard,
        *,
        event_type: Optional[str] = None,
    ) -> None:
        status = _status(result.get("status"))
        blackboard.add_finding(
            agent=task.agent,
            task_id=task.task_id,
            finding={
                "status": status,
                "score": result.get("score"),
                "audit_level": result.get("audit_level"),
                "comment": result.get("comment"),
                "suggestion": result.get("suggestion"),
                "error": result.get("error"),
            },
            evidence=result.get("evidence") if isinstance(result.get("evidence"), list) else None,
        )
        for request in result.get("consultation_requests", []) if isinstance(result.get("consultation_requests"), list) else []:
            if not isinstance(request, Mapping):
                continue
            target = str(request.get("to_agent") or "").strip()
            if target and self.registry.contains(target):
                blackboard.request_consultation(
                    from_agent=task.agent,
                    to_agent=target,
                    topic=str(request.get("topic") or "peer review"),
                    question=str(request.get("question") or "Please review this finding."),
                    payload=dict(request.get("payload") or {}),
                    task_id=task.task_id,
                )
        self.event_bus.publish(
            event_type=event_type or f"agent.findings.{task.agent}",
            request_id=request_id,
            paper_id=paper_id,
            producer_agent=task.agent,
            payload={
                "task_id": task.task_id,
                "status": status,
                "score": result.get("score"),
                "audit_level": result.get("audit_level"),
                "comment": result.get("comment"),
                "suggestion": result.get("suggestion"),
                "consultation_requests": result.get("consultation_requests", []),
            },
            trace_id=request_id,
            idempotency_key=f"{request_id}:{task.task_id}:{status}",
        )

    @staticmethod
    def _new_consultation_requests(blackboard: SharedBlackboard, processed: set) -> List[Dict[str, Any]]:
        return [
            dict(request)
            for request in blackboard.snapshot().get("consultation_requests", [])
            if request.get("status") == "open" and request.get("request_id") not in processed
        ]
