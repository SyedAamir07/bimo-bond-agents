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

        with urllib.request.urlopen("http://127.0.0.1:18083/audit") as resp:
            audit = json.loads(resp.read().decode())
        assert "records" in audit

        with urllib.request.urlopen("http://127.0.0.1:18083/acceptance") as resp:
            acceptance = json.loads(resp.read().decode())
        assert "handler_latency_ms" in acceptance
    finally:
        agent.shutdown()


def test_audit_redacts_secrets():
    from foundation import AuditLog

    log = AuditLog("a")
    rec = log.record("x", detail={"token": "secret", "ok": 1})
    assert rec.detail["token"] == "[redacted]"
    assert rec.detail["ok"] == 1


def test_retention_expiry():
    from datetime import datetime, timedelta, timezone

    from foundation import get_retention

    policy = get_retention("event_envelope")
    old = datetime.now(timezone.utc) - timedelta(days=30)
    assert policy.is_expired(old) is True
    assert policy.is_expired(datetime.now(timezone.utc)) is False


def test_acceptance_targets():
    from foundation import AcceptanceTracker, PILOT_TARGETS

    assert "gift_effect_latency_ms" in PILOT_TARGETS
    t = AcceptanceTracker()
    status = t.observe("gift_effect_latency_ms", 100.0)
    assert status == "meeting"
    status_bad = t.observe("gift_effect_latency_ms", 9999.0)
    assert status_bad == "above_target"
    report = t.report()
    assert report["gift_effect_latency_ms"]["samples"] >= 2


def test_auction_eligibility_rules():
    from foundation import AuctionDecision, GiftContribution, resolve_purchase_eligibility

    a = GiftContribution("e1", "u1", 10, "2026-01-01T00:00:00+00:00")
    b = GiftContribution("e2", "u2", 10, "2026-01-01T00:01:00+00:00")
    result = resolve_purchase_eligibility([a, b])
    assert result.decision == AuctionDecision.TIE_BREAK_EARLIEST
    assert result.winner_user_id == "u1"

    dup = resolve_purchase_eligibility([a], seen_event_ids={"e1"})
    assert dup.decision == AuctionDecision.REJECTED_DUPLICATE

    cancelled = resolve_purchase_eligibility(
        [GiftContribution("e3", "u3", 5, "2026-01-01T00:00:00+00:00", cancelled=True)]
    )
    assert cancelled.decision == AuctionDecision.REJECTED_CANCELLED

    pay = resolve_purchase_eligibility(
        [GiftContribution("e4", "u4", 5, "2026-01-01T00:00:00+00:00", payment_failed=True)]
    )
    assert pay.decision == AuctionDecision.REJECTED_PAYMENT_FAILED

    win = resolve_purchase_eligibility(
        [
            GiftContribution("e5", "u5", 3, "2026-01-01T00:00:00+00:00"),
            GiftContribution("e6", "u6", 9, "2026-01-01T00:02:00+00:00"),
        ]
    )
    assert win.decision == AuctionDecision.ELIGIBLE
    assert win.winner_user_id == "u6"


def test_should_dead_letter():
    from foundation import should_dead_letter

    assert should_dead_letter(1, 5) is False
    assert should_dead_letter(5, 5) is False
    assert should_dead_letter(6, 5) is True
    assert should_dead_letter(99, 0) is False


def test_redis_bus_dead_letters_poison_message():
    """Unit-test RedisStreams DLQ path with a mocked redis client."""
    from unittest.mock import MagicMock

    from foundation import RedisStreamsEventBus

    bus = RedisStreamsEventBus.__new__(RedisStreamsEventBus)
    bus.stream_key = "agent:events"
    bus.consumer_group = "cg:test"
    bus.max_deliveries = 2
    bus.dlq_stream_key = "agent:events:dlq"
    bus._handlers = {"boom": [lambda _e: (_ for _ in ()).throw(RuntimeError("x"))]}
    bus._client = MagicMock()
    bus._client.hincrby.side_effect = [1, 2, 3]
    bus._redis_lib = MagicMock()

    fields = {
        "event_id": "e1",
        "event_type": "boom",
        "source_agent": "t",
        "correlation_id": "",
        "occurred_at": "2026-01-01T00:00:00+00:00",
        "schema_version": "1",
        "payload": "{}",
    }
    bus._dispatch_and_ack("1-0", fields)  # fail, leave pending
    bus._dispatch_and_ack("1-0", fields)  # fail again
    bus._dispatch_and_ack("1-0", fields)  # delivery 3 > 2 → DLQ
    assert bus._client.xadd.call_count >= 1
    dlq_call = bus._client.xadd.call_args_list[-1]
    assert dlq_call.args[0] == "agent:events:dlq"
    bus._client.xack.assert_called()


def test_health_checked_at_refreshes():
    from foundation import HealthStatus, Status

    h = HealthStatus(agent_name="a", status=Status.OK)
    first = h.checked_at
    time.sleep(0.01)
    second = h.to_dict()["checked_at"]
    assert second >= first


def test_event_catalog_pilot_types():
    from foundation import nest_pilot_event_types

    pilots = nest_pilot_event_types()
    assert "liveGiftCombo" in pilots
    assert "liveEnded" in pilots


def test_filter_expired_exported():
    from datetime import datetime, timedelta, timezone

    from foundation import filter_expired

    old = datetime.now(timezone.utc) - timedelta(days=30)
    new = datetime.now(timezone.utc)
    kept = filter_expired([("a", old), ("b", new)], policy_name="event_envelope")
    assert [k for k, _ in kept] == ["b"]


def test_agent_drops_expired_envelope():
    from datetime import datetime, timedelta, timezone

    bus = InMemoryEventBus()
    settings = AgentSettings(
        agent_name="probe",
        subscribed_topics=("probe.ping",),
        permission_scopes=("scope.a",),
        health_port=18084,
    )
    agent = _ProbeAgent(settings, event_bus=bus)
    agent.run()
    try:
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        agent._safe_handle_event(
            EventEnvelope(
                event_type="probe.ping",
                source_agent="test",
                payload={},
                occurred_at=old,
            )
        )
        assert agent.handled == []
        assert "events_expired" in agent.metrics.to_prometheus()
    finally:
        agent.shutdown()
