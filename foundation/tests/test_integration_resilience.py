"""
Integration-style resilience tests: duplicate events, handler failures,
load (many events), and recovery -- the scenarios the project doc calls
out explicitly ("Test interruptions, load, duplicate events, and
recovery") that pure unit tests of individual modules don't exercise
together.

These run against InMemoryEventBus + a real BaseAgent subclass, so no
Redis is required, but the same request/response pipeline
(_safe_handle_event: dedup -> permission -> validate -> retry -> metrics)
that runs in production is exercised end-to-end.
"""
from __future__ import annotations

from foundation import (
    AgentContract,
    AgentSettings,
    BaseAgent,
    EventEnvelope,
    InMemoryEventBus,
    Status,
)


class _CountingAgent(BaseAgent):
    objective = "test agent counting handled events"
    contract = AgentContract(
        objective="test agent counting handled events",
        subscribed_topics=["probe.event"],
    )

    def __init__(self, *args, fail_first_n: int = 0, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.handled_ids: list[str] = []
        self._fail_first_n = fail_first_n
        self._fail_count = 0

    def handle_event(self, event: EventEnvelope) -> None:
        if self._fail_count < self._fail_first_n:
            self._fail_count += 1
            raise RuntimeError("simulated transient failure")
        self.handled_ids.append(event.event_id)


def _settings(agent_name: str = "counting-agent", **overrides) -> AgentSettings:
    return AgentSettings.from_env(
        agent_name,
        subscribed_topics=("probe.event",),
        health_port=0,
        **overrides,
    )


def test_load_many_events_all_handled_exactly_once():
    bus = InMemoryEventBus()
    agent = _CountingAgent(_settings(), event_bus=bus)
    agent.run()

    events = [
        EventEnvelope(event_type="probe.event", source_agent="load-test", payload={"i": i})
        for i in range(500)
    ]
    for event in events:
        bus.publish(event)

    assert len(agent.handled_ids) == 500
    assert len(set(agent.handled_ids)) == 500  # no duplicates processed
    assert agent.metrics.snapshot()["events_handled"] == 500


def test_duplicate_events_are_dropped_not_reprocessed():
    """
    InMemoryEventBus itself de-dups by event_id before an event ever
    reaches a handler (mirrors RedisStreamsEventBus's at-least-once
    redelivery + consumer-group semantics). BaseAgent._safe_handle_event
    has its own second dedup layer for buses that don't dedup upstream --
    exercised separately below by publishing the *same envelope object*
    bypassing the bus (as a redelivered message would).
    """
    bus = InMemoryEventBus()
    agent = _CountingAgent(_settings(), event_bus=bus)
    agent.run()

    event = EventEnvelope(event_type="probe.event", source_agent="dup-test", payload={})
    bus.publish(event)
    bus.publish(event)  # same event_id -- simulates at-least-once redelivery
    bus.publish(event)

    # Bus-level dedup means the handler only ever sees it once.
    assert len(agent.handled_ids) == 1
    assert agent.metrics.snapshot()["events_handled"] == 1


def test_agent_level_dedup_drops_redelivered_event_bypassing_bus_dedup():
    """
    Covers BaseAgent's own dedup layer directly (_safe_handle_event),
    independent of whichever bus implementation is in front of it --
    this is what protects agents on buses that guarantee at-least-once
    but not bus-level dedup.
    """
    bus = InMemoryEventBus()
    agent = _CountingAgent(_settings(), event_bus=bus)
    agent.run()

    event = EventEnvelope(event_type="probe.event", source_agent="dup-test", payload={})
    agent._safe_handle_event(event)
    agent._safe_handle_event(event)  # redelivered straight to the agent

    assert len(agent.handled_ids) == 1
    assert agent.metrics.snapshot().get("duplicates_dropped", 0) == 1


def test_handler_retries_transient_failure_then_succeeds():
    bus = InMemoryEventBus()
    agent = _CountingAgent(_settings(handler_max_attempts=3), event_bus=bus, fail_first_n=2)
    agent.run()

    event = EventEnvelope(event_type="probe.event", source_agent="retry-test", payload={})
    bus.publish(event)

    # Retried internally within _safe_handle_event -> should still succeed
    # and be recorded exactly once, not zero or multiple times.
    assert agent.handled_ids == [event.event_id]
    assert agent.metrics.snapshot()["events_handled"] == 1
    assert agent.metrics.snapshot().get("events_failed", 0) == 0


def test_handler_exhausting_retries_marks_degraded_and_audits_failure():
    bus = InMemoryEventBus()
    agent = _CountingAgent(_settings(handler_max_attempts=2), event_bus=bus, fail_first_n=99)
    agent.run()

    event = EventEnvelope(event_type="probe.event", source_agent="fail-test", payload={})
    bus.publish(event)

    assert agent.handled_ids == []
    assert agent.metrics.snapshot()["events_failed"] == 1
    assert agent.health.status == Status.DEGRADED

    failure_records = [r for r in agent.audit.recent() if r.action == "event.handle_failed"]
    assert len(failure_records) == 1
    assert failure_records[0].outcome == "failed"


def test_recovery_after_interruption_new_agent_instance_keeps_consuming():
    """
    Simulates one agent process going down and a fresh one starting in its
    place (e.g. after a crash/restart) -- the "recovery" scenario. Events
    published after the restart must still be handled by the new instance.
    """
    bus = InMemoryEventBus()
    agent_v1 = _CountingAgent(_settings(), event_bus=bus)
    agent_v1.run()

    bus.publish(EventEnvelope(event_type="probe.event", source_agent="pre-restart", payload={}))
    assert len(agent_v1.handled_ids) == 1

    agent_v1.shutdown()

    # Fresh instance, same bus -- represents the process restarting.
    agent_v2 = _CountingAgent(_settings(), event_bus=bus)
    agent_v2.run()

    bus.publish(EventEnvelope(event_type="probe.event", source_agent="post-restart", payload={}))
    assert len(agent_v2.handled_ids) == 1


def test_permission_denied_event_is_dropped_and_audited():
    bus = InMemoryEventBus()
    agent = _CountingAgent(_settings(permission_scopes=()), event_bus=bus)
    agent.run()

    event = EventEnvelope(
        event_type="probe.event",
        source_agent="perm-test",
        payload={"required_scope": "scope.that.agent.lacks"},
    )
    bus.publish(event)

    assert agent.handled_ids == []
    denied = [r for r in agent.audit.recent() if r.action == "event.permission_denied"]
    assert len(denied) == 1
