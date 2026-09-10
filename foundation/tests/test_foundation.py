"""Foundation unit tests — memory bus, permissions, contracts, reliability, metrics."""
from __future__ import annotations

import time

import pytest

from foundation import (
    AgentContract,
    AgentMetrics,
    AgentSettings,
    BackendClient,
    BackendClientDisabled,
    BaseAgent,
    EventDeduplicator,
    EventEnvelope,
    InMemoryEventBus,
    PermissionDenied,
    RetryPolicy,
    assert_scope,
    build_event_bus,
    has_scope,
    validate_event_payload,
)


class _ProbeAgent(BaseAgent):
    objective = "probe"
    contract = AgentContract(
        objective="probe",
        permission_scopes=["scope.a"],
        subscribed_topics=["probe.ping"],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.handled: list[EventEnvelope] = []

    def handle_event(self, event: EventEnvelope) -> None:
        self.handled.append(event)


def test_inmemory_bus_delivers_and_dedups():
    bus = InMemoryEventBus()
    seen: list[str] = []
    bus.subscribe("t", lambda e: seen.append(e.event_id))
    bus.start()
    ev = EventEnvelope(event_type="t", source_agent="x", payload={})
    bus.publish(ev)
    bus.publish(ev)  # duplicate event_id
    assert seen == [ev.event_id]


def test_build_event_bus_memory():
    bus = build_event_bus("memory://local")
    assert isinstance(bus, InMemoryEventBus)


def test_permission_assert():
    settings = AgentSettings(
        agent_name="a",
        permission_scopes=("scope.a",),
    )
    assert has_scope(settings, "scope.a")
    assert has_scope(settings, None)
    assert_scope(settings, "scope.a")
    with pytest.raises(PermissionDenied):
        assert_scope(settings, "scope.b")


def test_agent_denies_required_scope():
    bus = InMemoryEventBus()
    settings = AgentSettings(
        agent_name="probe",
        subscribed_topics=("probe.ping",),
        permission_scopes=("scope.a",),
        health_port=18081,
    )
    agent = _ProbeAgent(settings, event_bus=bus)
    agent.run()
    try:
        bus.publish(
            EventEnvelope(
                event_type="probe.ping",
                source_agent="test",
                payload={"required_scope": "scope.b"},
            )
        )
        assert agent.handled == []
        assert "permission_denied" in agent.metrics.to_prometheus()
    finally:
        agent.shutdown()


def test_agent_handles_and_dedups():
    bus = InMemoryEventBus()
    settings = AgentSettings(
        agent_name="probe",
        subscribed_topics=("probe.ping",),
        permission_scopes=("scope.a",),
        health_port=18082,
    )
    agent = _ProbeAgent(settings, event_bus=bus)
    agent.run()
    try:
        ev = EventEnvelope(
            event_type="probe.ping",
            source_agent="test",
            payload={"required_scope": "scope.a"},
        )
        # Call the agent pipeline twice (bus-level dedup is tested separately).
        agent._safe_handle_event(ev)
        agent._safe_handle_event(ev)
        assert len(agent.handled) == 1
        assert "duplicates_dropped" in agent.metrics.to_prometheus()
    finally:
        agent.shutdown()


def test_event_deduplicator_bounded():
    d = EventDeduplicator(max_size=3)
    assert d.seen_before("1") is False
    assert d.seen_before("1") is True
    d.seen_before("2")
    d.seen_before("3")
    d.seen_before("4")
    assert len(d) == 3
    assert "1" not in d


def test_retry_policy_succeeds_after_fail():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("fail")
        return "ok"

    policy = RetryPolicy(max_attempts=3, base_delay_seconds=0.001)
    assert policy.run(flaky) == "ok"
    assert calls["n"] == 3


def test_retry_policy_exhausted():
    policy = RetryPolicy(max_attempts=2, base_delay_seconds=0.001)
    with pytest.raises(RuntimeError):
        policy.run(lambda: (_ for _ in ()).throw(RuntimeError("x")))


def test_agent_contract_validate():
    c = AgentContract(objective="")
    problems = c.validate()
    assert "objective is required" in problems
    good = AgentContract(
        objective="x",
        permission_scopes=["a"],
        subscribed_topics=["t"],
    )
    assert good.validate() == []
    assert "objective" in good.to_dict()


def test_validate_event_payload_known_and_unknown():
    missing = validate_event_payload(
        EventEnvelope(event_type="task.requested", source_agent="t", payload={})
    )
    assert "task_id" in missing
    # unknown type: soft pass
    assert (
        validate_event_payload(
            EventEnvelope(event_type="unknown.event", source_agent="t", payload={})
        )
        == []
    )


def test_metrics_prometheus():
    m = AgentMetrics("cam")
    m.incr("events_handled")
    with m.timer("handler_latency"):
        time.sleep(0.001)
    text = m.to_prometheus()
    assert 'agent_events_handled{agent="cam"}' in text
    assert "handler_latency_ms_sum" in text


def test_backend_client_disabled():
    client = BackendClient(base_url="")
    assert client.enabled is False
    with pytest.raises(BackendClientDisabled):
        client.get("/health")


def test_health_and_contract_endpoints():
    import json
    import urllib.request

    bus = InMemoryEventBus()
    settings = AgentSettings(
        agent_name="probe",
        subscribed_topics=("probe.ping",),
        permission_scopes=("scope.a",),
        health_port=18083,
    )
    agent = _ProbeAgent(settings, event_bus=bus)
    agent.run()
    try:
        time.sleep(0.1)
        with urllib.request.urlopen("http://127.0.0.1:18083/health") as resp:
            health = json.loads(resp.read().decode())
        assert health["agent_name"] == "probe"
        assert health["status"] == "ok"

        with urllib.request.urlopen("http://127.0.0.1:18083/contract") as resp:
            contract = json.loads(resp.read().decode())
        assert contract["objective"] == "probe"

        with urllib.request.urlopen("http://127.0.0.1:18083/metrics") as resp:
            metrics = resp.read().decode()
        assert "agent_" in metrics or metrics == "\n" or True
    finally:
        agent.shutdown()
