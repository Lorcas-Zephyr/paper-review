"""Constrained LLM planning for paper-review workflows.

The model proposes a graph; the application remains the authority that
validates and executes it.  If the model is unavailable or produces invalid
JSON, a deterministic plan is returned so review jobs remain operable.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

import httpx

from agent_registry import AgentRegistry
APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from llm_config import get_deepseek_config

logger = logging.getLogger(__name__)


class PlanValidationError(ValueError):
    """Raised when a planner response is unsafe or structurally invalid."""


@dataclass(frozen=True)
class PlanTask:
    task_id: str
    agent: str
    objective: str
    depends_on: List[str] = field(default_factory=list)
    required: bool = True
    max_retries: int = 1

    def as_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent": self.agent,
            "objective": self.objective,
            "depends_on": list(self.depends_on),
            "required": self.required,
            "max_retries": self.max_retries,
        }


@dataclass(frozen=True)
class PlannerPlan:
    goal: str
    tasks: List[PlanTask]
    consultation_rounds: int = 2
    stop_condition: Dict[str, Any] = field(default_factory=dict)
    source: str = "fallback"
    planner_version: str = "ai-planner-v1"
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "tasks": [task.as_dict() for task in self.tasks],
            "consultation_rounds": self.consultation_rounds,
            "stop_condition": dict(self.stop_condition),
            "source": self.source,
            "planner_version": self.planner_version,
            "error": self.error,
        }


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def validate_plan(
    raw: Mapping[str, Any],
    registry: AgentRegistry,
    *,
    max_tasks: int = 12,
    max_consultation_rounds: int = 3,
) -> PlannerPlan:
    """Validate an untrusted planner object and return a typed immutable plan."""
    if not isinstance(raw, Mapping):
        raise PlanValidationError("plan must be an object")
    goal = str(raw.get("goal") or "review_paper").strip()
    if not goal or len(goal) > 200:
        raise PlanValidationError("goal must be a non-empty string of at most 200 characters")
    raw_tasks = raw.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise PlanValidationError("tasks must be a non-empty array")
    if len(raw_tasks) > max_tasks:
        raise PlanValidationError(f"too many tasks: {len(raw_tasks)} > {max_tasks}")

    tasks: List[PlanTask] = []
    ids = set()
    for item in raw_tasks:
        if not isinstance(item, Mapping):
            raise PlanValidationError("each task must be an object")
        task_id = str(item.get("task_id") or "").strip()
        agent = str(item.get("agent") or "").strip()
        objective = str(item.get("objective") or "").strip()
        if not task_id or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", task_id):
            raise PlanValidationError(f"invalid task_id: {task_id!r}")
        if task_id in ids:
            raise PlanValidationError(f"duplicate task_id: {task_id}")
        ids.add(task_id)
        if not registry.contains(agent):
            raise PlanValidationError(f"agent is not allow-listed: {agent}")
        if not objective or len(objective) > 1000:
            raise PlanValidationError(f"invalid objective for task {task_id}")
        deps = item.get("depends_on", [])
        if deps is None:
            deps = []
        if not isinstance(deps, list) or any(not isinstance(dep, str) for dep in deps):
            raise PlanValidationError(f"depends_on must be a string array for {task_id}")
        retries = item.get("max_retries", 1)
        try:
            retries = int(retries)
        except (TypeError, ValueError) as exc:
            raise PlanValidationError(f"invalid max_retries for {task_id}") from exc
        if retries < 0 or retries > 3:
            raise PlanValidationError(f"max_retries out of range for {task_id}")
        tasks.append(
            PlanTask(
                task_id=task_id,
                agent=agent,
                objective=objective,
                depends_on=list(dict.fromkeys(deps)),
                required=_as_bool(item.get("required"), True),
                max_retries=retries,
            )
        )

    for task in tasks:
        unknown = set(task.depends_on) - ids
        if unknown:
            raise PlanValidationError(f"unknown dependency for {task.task_id}: {sorted(unknown)}")
        if task.task_id in task.depends_on:
            raise PlanValidationError(f"task cannot depend on itself: {task.task_id}")

    # Kahn's algorithm catches cycles before execution.
    remaining = {task.task_id: set(task.depends_on) for task in tasks}
    resolved = set()
    while remaining:
        ready = [task_id for task_id, deps in remaining.items() if not deps]
        if not ready:
            raise PlanValidationError("task dependency graph contains a cycle")
        for task_id in ready:
            resolved.add(task_id)
            remaining.pop(task_id)
        for deps in remaining.values():
            deps.difference_update(ready)

    try:
        consultation_rounds = int(raw.get("consultation_rounds", 2))
    except (TypeError, ValueError) as exc:
        raise PlanValidationError("consultation_rounds must be an integer") from exc
    if consultation_rounds < 0 or consultation_rounds > max_consultation_rounds:
        raise PlanValidationError("consultation_rounds is outside the configured limit")
    stop_condition = raw.get("stop_condition") or {}
    if not isinstance(stop_condition, Mapping):
        raise PlanValidationError("stop_condition must be an object")

    return PlannerPlan(
        goal=goal,
        tasks=tasks,
        consultation_rounds=consultation_rounds,
        stop_condition=dict(stop_condition),
        source=str(raw.get("source") or "llm"),
        planner_version=str(raw.get("planner_version") or "ai-planner-v1"),
    )


def default_plan(registry: AgentRegistry, *, reason: Optional[str] = None) -> PlannerPlan:
    """Deterministic safe plan used when no valid LLM plan is available."""
    preferred = [name for name in ("logic_agent", "citation_agent", "format_agent", "experiment_agent") if registry.contains(name)]
    if not preferred:
        preferred = registry.names()
    tasks: List[PlanTask] = []
    first = [name for name in preferred if name != "experiment_agent"]
    for name in first:
        tasks.append(
            PlanTask(
                task_id=f"{name}-1",
                agent=name,
                objective=f"Perform the {name.replace('_agent', '')} review and cite concrete evidence.",
                depends_on=[],
            )
        )
    if registry.contains("experiment_agent"):
        tasks.append(
            PlanTask(
                task_id="experiment_agent-1",
                agent="experiment_agent",
                objective="Review experiment design, statistical validity, and reproducibility using prior findings.",
                depends_on=[task.task_id for task in tasks],
            )
        )
    return PlannerPlan(
        goal="review_paper",
        tasks=tasks,
        consultation_rounds=2,
        stop_condition={"min_required_agents": len(tasks), "max_rounds": 3},
        source="fallback",
        error=reason,
    )


class AIPlanner:
    """OpenAI-compatible planner with strict validation and deterministic fallback."""

    def __init__(
        self,
        registry: AgentRegistry,
        *,
        llm_client: Optional[Callable[[str, str], Any]] = None,
        max_tasks: int = 12,
        max_consultation_rounds: int = 3,
        timeout: float = 45.0,
    ) -> None:
        self.registry = registry
        self.llm_client = llm_client
        self.max_tasks = max_tasks
        self.max_consultation_rounds = max_consultation_rounds
        self.timeout = timeout
        self.last_metadata: Dict[str, Any] = {}

    async def plan(
        self,
        *,
        paper_title: str,
        paper_content: str,
        paper_id: str,
        request_id: str,
        config: Optional[Mapping[str, Any]] = None,
    ) -> PlannerPlan:
        cfg = dict(config or {})
        ai_enabled = cfg.get("enable_ai_scheduler", os.getenv("ENABLE_AI_SCHEDULER", "true"))
        if cfg.get("scheduler_backend") == "fixed" or not _as_bool(ai_enabled, True):
            plan = default_plan(self.registry, reason="AI scheduler disabled by request")
            self.last_metadata = {"status": "disabled", "source": plan.source}
            return plan

        prompt = self._build_prompt(paper_title, paper_content, paper_id, request_id)
        try:
            response = await self._complete(prompt)
            raw = self._parse_json(response)
            raw = dict(raw)
            raw.setdefault("source", "llm")
            plan = validate_plan(
                raw,
                self.registry,
                max_tasks=self.max_tasks,
                max_consultation_rounds=self.max_consultation_rounds,
            )
            self.last_metadata = {
                "status": "success",
                "source": "llm",
                "planner_version": plan.planner_version,
                "raw_response": response[:12000],
                "prompt": prompt,
            }
            return plan
        except Exception as exc:  # planner failure must not block an audit
            logger.warning("AI planner failed; using fallback plan: %s", exc)
            plan = default_plan(self.registry, reason=str(exc))
            self.last_metadata = {
                "status": "fallback",
                "source": "fallback",
                "error": str(exc),
                "prompt": prompt,
            }
            return plan

    def _build_prompt(self, title: str, content: str, paper_id: str, request_id: str) -> str:
        excerpt = (content or "")[:8000]
        schema = {
            "goal": "review_paper",
            "tasks": [
                {
                    "task_id": "logic-1",
                    "agent": "logic_agent",
                    "objective": "...",
                    "depends_on": [],
                    "required": True,
                    "max_retries": 1,
                }
            ],
            "consultation_rounds": 2,
            "stop_condition": {"min_required_agents": 3, "max_rounds": 3},
        }
        return (
            "You are a constrained workflow planner for an academic paper review. "
            "Return JSON only. Select only registered agents, create a DAG, and keep "
            "the number of tasks and consultation rounds small. Agents communicate "
            "through a shared blackboard; never return code or shell commands.\n"
            f"Registered agents:\n{json.dumps(self.registry.describe(), ensure_ascii=False)}\n"
            f"JSON schema example:\n{json.dumps(schema, ensure_ascii=False)}\n"
            f"paper_id={paper_id}; request_id={request_id}; title={title!r}\n"
            f"paper excerpt:\n{excerpt}"
        )

    async def _complete(self, prompt: str) -> str:
        if self.llm_client is not None:
            try:
                value = self.llm_client(prompt, "Return a valid JSON workflow plan.")
            except TypeError:
                # Small test doubles and local adapters often expose a
                # single-prompt callable.
                value = self.llm_client(prompt)
            if inspect.isawaitable(value):
                value = await value
            if isinstance(value, Mapping):
                return json.dumps(value, ensure_ascii=False)
            return str(value)

        llm = get_deepseek_config(require_key=True)
        payload = {
            "model": llm.model,
            "temperature": llm.temperature,
            "messages": [
                {"role": "system", "content": "You output strict JSON and nothing else."},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": llm.max_tokens,
        }
        if llm.json_mode:
            payload["response_format"] = {"type": "json_object"}
        if llm.thinking_enabled:
            payload["thinking"] = {"type": "enabled"}
        async with httpx.AsyncClient(timeout=llm.timeout_seconds, trust_env=False) as client:
            response = await client.post(
                llm.chat_completions_url,
                json=payload,
                headers={"Authorization": f"Bearer {llm.api_key}", "Content-Type": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], Mapping):
            raise RuntimeError("LLM response has no choices")
        message = choices[0].get("message") or {}
        return str(message.get("content") or "")

    @staticmethod
    def _parse_json(value: str) -> Mapping[str, Any]:
        text = value.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.S)
            if not match:
                raise PlanValidationError("LLM response is not valid JSON")
            parsed = json.loads(match.group(0))
        if not isinstance(parsed, Mapping):
            raise PlanValidationError("LLM response root must be an object")
        return parsed
