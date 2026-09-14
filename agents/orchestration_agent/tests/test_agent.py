"""Orchestration agent tests: smoke test + duplicate/retry/timeout/restart coverage."""
from __future__ import annotations

from foundation import EventEnvelope, InMemoryEventBus, InMemoryTaskStore

from orchestration_agent.agent import (
    TASK_TIMEOUT_SECONDS,
    MAX_RETRIES,
    OrchestrationAgent,
    TaskStatus,
)
from orchestration_agent.config import SETTINGS


def _make_agent(store: InMemoryTaskStore | None = None) -> OrchestrationAgent:
    bus = InMemoryEventBus()
    agent = OrchestrationAgent(SETTINGS, event_bus=bus, task_store=store)
    agent.run()
    return agent


def test_agent_handles_event_without_crashing():
    bus = InMemoryEventBus()
    agent = OrchestrationAgent(SETTINGS, event_bus=bus)
    agent.run()

    event = EventEnvelope(
        event_type=SETTINGS.subscribed_topics[0] if SETTINGS.subscribed_topics else "test.event",
        source_agent="test",
        payload={"example": True},
    )
    agent.handle_event(event)  # should not raise


def test_task_requested_dispatches_to_routed_agent():
    agent = _make_agent()

    agent.handle_event(
        EventEnvelope(
            event_type="task.requested",
            source_agent="test",
            payload={"task_id": "t1", "action": "start_stream_monitor"},
        )
    )

    record = agent.get_task("t1")
    assert record is not None
    assert record.status == TaskStatus.DISPATCHED.value
    assert record.target_agent == "live_streaming_agent"
    assert record.attempts == 1


def test_unknown_action_marks_task_failed():
    agent = _make_agent()

    agent.handle_event(
        EventEnvelope(
            event_type="task.requested",
            source_agent="test",
            payload={"task_id": "t1", "action": "does_not_exist"},
        )
    )

    record = agent.get_task("t1")
    assert record is not None
    assert record.status == TaskStatus.FAILED.value
    assert record.last_error == "unknown_action"


def test_duplicate_task_id_is_not_redispatched():
    agent = _make_agent()
    request = EventEnvelope(
        event_type="task.requested",
        source_agent="test",
        payload={"task_id": "t1", "action": "start_stream_monitor"},
    )

    agent.handle_event(request)
    first = agent.get_task("t1")
    assert first is not None
    assert first.attempts == 1

    # Same task_id arrives again (at-least-once delivery replay).
    agent.handle_event(request)
    second = agent.get_task("t1")
    assert second is not None
    assert second.attempts == 1  # not re-dispatched / attempts unchanged


def test_success_event_marks_task_completed():
    agent = _make_agent()
    agent.handle_event(
        EventEnvelope(
            event_type="task.requested",
            source_agent="test",
            payload={"task_id": "t1", "action": "start_stream_monitor"},
        )
    )

    agent.handle_event(
        EventEnvelope(
            event_type="stream.monitor.armed",
            source_agent="live_streaming_agent",
            payload={"session_id": "s1"},
            correlation_id="t1",
        )
    )

    record = agent.get_task("t1")
    assert record is not None
    assert record.status == TaskStatus.COMPLETED.value


def test_failure_event_retries_until_max_then_fails():
    agent = _make_agent()
    agent.handle_event(
        EventEnvelope(
            event_type="task.requested",
            source_agent="test",
            payload={"task_id": "t1", "action": "start_stream_monitor"},
        )
    )
    assert agent.get_task("t1").attempts == 1

    # Each failure event should retry until MAX_RETRIES is reached.
    for _ in range(MAX_RETRIES):
        agent.handle_event(
            EventEnvelope(
                event_type="stream.ended",
                source_agent="live_streaming_agent",
                payload={},
                correlation_id="t1",
            )
        )

    record = agent.get_task("t1")
    assert record is not None
    assert record.status == TaskStatus.FAILED.value


def test_failure_event_for_unknown_task_id_does_not_raise():
    agent = _make_agent()
    # No task.requested was ever seen for "ghost" -- must be handled gracefully.
    agent.handle_event(
        EventEnvelope(
            event_type="stream.ended",
            source_agent="live_streaming_agent",
            payload={},
            correlation_id="ghost",
        )
    )
    assert agent.get_task("ghost") is None


def test_check_timeouts_retries_stale_dispatched_task():
    agent = _make_agent()
    agent.handle_event(
        EventEnvelope(
            event_type="task.requested",
            source_agent="test",
            payload={"task_id": "t1", "action": "start_stream_monitor"},
        )
    )
    record = agent.get_task("t1")
    assert record.attempts == 1

    # Simulate the dispatch having happened TASK_TIMEOUT_SECONDS+1 ago.
    record.dispatched_at -= (TASK_TIMEOUT_SECONDS + 1)
    agent._store.save(record)

    agent.check_timeouts()

    retried = agent.get_task("t1")
    assert retried.attempts == 2
    assert retried.status == TaskStatus.DISPATCHED.value


def test_check_timeouts_fails_task_after_max_retries():
    agent = _make_agent()
    agent.handle_event(
        EventEnvelope(
            event_type="task.requested",
            source_agent="test",
            payload={"task_id": "t1", "action": "start_stream_monitor"},
        )
    )
    record = agent.get_task("t1")
    record.attempts = MAX_RETRIES
    record.dispatched_at -= (TASK_TIMEOUT_SECONDS + 1)
    agent._store.save(record)

    agent.check_timeouts()

    final = agent.get_task("t1")
    assert final.status == TaskStatus.TIMED_OUT.value


def test_task_state_survives_orchestrator_restart():
    """
    The whole point of the durable TaskStore: a fresh OrchestrationAgent
    instance sharing the same store must see tasks dispatched by a prior
    (now-gone) instance -- simulating a process restart.
    """
    shared_store = InMemoryTaskStore()
    agent1 = _make_agent(store=shared_store)
    agent1.handle_event(
        EventEnvelope(
            event_type="task.requested",
            source_agent="test",
            payload={"task_id": "t1", "action": "start_stream_monitor"},
        )
    )
    assert agent1.get_task("t1").status == TaskStatus.DISPATCHED.value

    # "Restart": brand new agent instance, same durable store.
    agent2 = _make_agent(store=shared_store)
    restarted_view = agent2.get_task("t1")

    assert restarted_view is not None
    assert restarted_view.status == TaskStatus.DISPATCHED.value

    # Duplicate prevention must also hold across the "restart".
    agent2.handle_event(
        EventEnvelope(
            event_type="task.requested",
            source_agent="test",
            payload={"task_id": "t1", "action": "start_stream_monitor"},
        )
    )
    assert agent2.get_task("t1").attempts == 1
