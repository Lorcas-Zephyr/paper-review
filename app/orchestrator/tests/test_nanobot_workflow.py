import asyncio

from event_bus import EventBus
from nanobot_workflow import NanobotWorkflowRunner


def test_nanobot_workflow_publishes_events_and_retries():
    calls = {"experiment_agent": 0}

    async def call_agent(
        agent_name,
        agent_config,
        paper_title,
        paper_content,
        paper_id,
        request_id,
        submission_config,
        interaction_events,
    ):
        if agent_name == "experiment_agent":
            calls["experiment_agent"] += 1
            if calls["experiment_agent"] == 1:
                return {"agent_name": agent_name, "group_id": 5, "weight": 1.1, "status": "FAILED"}
        return {
            "agent_name": agent_name,
            "group_id": agent_config["group_id"],
            "weight": agent_config["weight"],
            "status": "SUCCESS",
            "score": 80,
            "audit_level": "Pass",
            "comment": "ok",
            "suggestion": "",
        }

    def aggregate_results(all_results, paper_title, request_id, paper_id):
        return {"request_id": request_id, "paper_id": paper_id, "all_results_count": len(all_results)}

    async def run_reflection(*args, **kwargs):
        return {"final_score": 88, "plugin_metadata": {}}

    def merge_reflection(base, reflection):
        out = dict(base)
        out["reflection"] = reflection
        return out

    endpoints = {
        "logic_agent": {"group_id": 3, "weight": 1.2},
        "citation_agent": {"group_id": 6, "weight": 1.0},
        "format_agent": {"group_id": 2, "weight": 0.8},
        "experiment_agent": {"group_id": 5, "weight": 1.1},
    }
    bus = EventBus(
        subscriptions={"experiment_agent": {"agent.findings.logic_agent", "agent.findings.citation_agent"}}
    )
    runner = NanobotWorkflowRunner(
        agent_endpoints=endpoints,
        event_bus=bus,
        call_agent=call_agent,
        aggregate_results=aggregate_results,
        run_reflection=run_reflection,
        merge_reflection=merge_reflection,
        max_retries=1,
    )

    result = asyncio.run(
        runner.run(
            paper_title="t",
            paper_content="c",
            paper_id="p1",
            request_id="r1",
            submission_config={},
        )
    )

    assert result["all_results"]
    assert calls["experiment_agent"] == 2
    assert len(result["aggregated_report"]["agent_events"]) >= 4
