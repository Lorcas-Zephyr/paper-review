from event_bus import EventBus


def test_event_bus_subscription_filtering():
    bus = EventBus(
        subscriptions={
            "experiment_agent": {"agent.findings.logic_agent"},
            "logic_agent": set(),
        }
    )
    request_id = "req-1"
    bus.reset_request(request_id)
    bus.publish(
        event_type="agent.findings.logic_agent",
        request_id=request_id,
        paper_id="paper-1",
        producer_agent="logic_agent",
        payload={"score": 81},
    )
    bus.publish(
        event_type="agent.findings.citation_agent",
        request_id=request_id,
        paper_id="paper-1",
        producer_agent="citation_agent",
        payload={"score": 75},
    )

    exp_events = bus.get_events_for_agent("experiment_agent", request_id)
    logic_events = bus.get_events_for_agent("logic_agent", request_id)

    assert len(exp_events) == 1
    assert exp_events[0]["event_type"] == "agent.findings.logic_agent"
    assert all(evt["producer_agent"] != "logic_agent" for evt in logic_events)
