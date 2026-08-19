import asyncio
from pathlib import Path

import pytest

from agent_registry import AgentRegistry
from agent_runtime import AgentRuntime
from blackboard import SharedBlackboard
from event_bus import EventBus
from paper_dataset import build_manifest, load_manifest
from planner import AIPlanner, PlanValidationError, validate_plan


def _registry():
    return AgentRegistry.from_endpoints(
        {
            "logic_agent": {"group_id": 3, "weight": 1.0},
            "citation_agent": {"group_id": 6, "weight": 1.0},
            "experiment_agent": {"group_id": 5, "weight": 1.0},
        }
    )


def test_plan_validation_rejects_cycles_and_unknown_agents():
    registry = _registry()
    with pytest.raises(PlanValidationError):
        validate_plan(
            {
                "tasks": [
                    {"task_id": "a", "agent": "logic_agent", "objective": "a", "depends_on": ["b"]},
                    {"task_id": "b", "agent": "citation_agent", "objective": "b", "depends_on": ["a"]},
                ]
            },
            registry,
        )
    with pytest.raises(PlanValidationError):
        validate_plan(
            {"tasks": [{"task_id": "x", "agent": "shell_agent", "objective": "unsafe"}]},
            registry,
        )


def test_planner_uses_valid_llm_plan_and_falls_back(monkeypatch):
    registry = _registry()

    async def fake_llm(_prompt, *_args):
        return {
            "goal": "review_paper",
            "tasks": [
                {"task_id": "logic", "agent": "logic_agent", "objective": "check arguments"},
                {"task_id": "experiment", "agent": "experiment_agent", "objective": "check experiments", "depends_on": ["logic"]},
            ],
            "consultation_rounds": 1,
        }

    async def exercise():
        planner = AIPlanner(registry, llm_client=fake_llm)
        plan = await planner.plan(paper_title="t", paper_content="c", paper_id="p", request_id="r")
        assert plan.source == "llm"
        assert [task.task_id for task in plan.tasks] == ["logic", "experiment"]

        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        fallback = await AIPlanner(registry).plan(paper_title="t", paper_content="c", paper_id="p", request_id="r")
        assert fallback.source == "fallback"
        assert fallback.tasks

    asyncio.run(exercise())


def test_blackboard_isolates_agent_context_and_runtime_consults():
    bus = EventBus(subscriptions={name: {"*"} for name in _registry().names()})
    registry = _registry()
    board = SharedBlackboard(paper_id="p", request_id="r")

    async def call_agent(agent_name, config, title, content, paper_id, request_id, submission_config, events):
        if submission_config.get("_consultation_request"):
            return {"agent_name": agent_name, "status": "SUCCESS", "score": 77, "comment": "answered"}
        response = {"agent_name": agent_name, "status": "SUCCESS", "score": 80, "comment": agent_name}
        if agent_name == "logic_agent":
            response["consultation_requests"] = [{
                "to_agent": "citation_agent",
                "topic": "citation support",
                "question": "Does the citation support this claim?",
            }]
        return response

    from planner import PlanTask, PlannerPlan

    plan = PlannerPlan(
        goal="review_paper",
        tasks=[
            PlanTask(task_id="logic", agent="logic_agent", objective="logic"),
            PlanTask(task_id="citation", agent="citation_agent", objective="citation"),
        ],
        consultation_rounds=1,
    )

    result = asyncio.run(
        AgentRuntime(registry=registry, event_bus=bus, call_agent=call_agent).run(
            plan=plan,
            paper_title="t",
            paper_content="c",
            paper_id="p",
            request_id="r",
            submission_config={},
            blackboard=board,
        )
    )
    assert result["consultation_count"] == 1
    assert any(item.get("type") == "consultation_request" for item in board.snapshot()["consultation_requests"])
    assert all(item["agent"] != "logic_agent" for item in board.context_for_agent("logic_agent")["agent_findings"])
    assert any(event["event_type"] == "agent.consultation_response" for event in bus.snapshot("r"))


def test_manifest_builder_uses_relative_paths(tmp_path: Path):
    root = tmp_path / "papers"
    sample = root / "paper-1"
    (sample / "reviews").mkdir(parents=True)
    (sample / "paper.pdf").write_bytes(b"paper")
    (sample / "reviews" / "review-01.pdf").write_bytes(b"review")
    output = tmp_path / "manifest.jsonl"
    summary = build_manifest(root, output, include_pages=False)
    assert summary["sample_count"] == 1
    rows = load_manifest(output)
    assert rows[0]["paper_path"] == "paper-1/paper.pdf"
    assert rows[0]["review_paths"] == ["paper-1/reviews/review-01.pdf"]
