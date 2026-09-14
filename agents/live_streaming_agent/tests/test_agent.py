"""Live Streaming Agent tests: lifecycle, reconnect policy, stale-session
sweep, restart durability, and both liveId/session_id payload shapes."""
from __future__ import annotations

from foundation import EventEnvelope, InMemoryEventBus

from live_streaming_agent.agent import LiveStreamingAgent
from live_streaming_agent.config import MAX_RECONNECT_ATTEMPTS, SETTINGS
from live_streaming_agent.session_store import SessionState, build_session_store


def _make_agent(store=None) -> LiveStreamingAgent:
    bus = InMemoryEventBus()
    agent = LiveStreamingAgent(SETTINGS, event_bus=bus)
    if store is not None:
        agent._sessions = store
    agent.run()
    return agent


def test_agent_handles_event_without_crashing():
    bus = InMemoryEventBus()
    agent = LiveStreamingAgent(SETTINGS, event_bus=bus)
    agent.run()

    event = EventEnvelope(
        event_type=SETTINGS.subscribed_topics[0] if SETTINGS.subscribed_topics else "test.event",
        source_agent="test",
        payload={"example": True},
    )
    agent.handle_event(event)  # should not raise


def test_stream_started_creates_active_session_and_arms_monitor():
    agent = _make_agent()

    agent.handle_event(
        EventEnvelope(
            event_type="stream.started",
            source_agent="test",
            payload={"session_id": "s1", "startup_ms": 1800},
        )
    )

    session = agent.get_session("s1")
    assert session is not None
    assert session.state == SessionState.ACTIVE
    assert session.reconnect_attempts == 0


def test_live_ended_event_uses_liveid_key_not_session_id():
    """NestJS fan-out events key on liveId, not session_id -- must be
    accepted the same way as internal stream.* events."""
    agent = _make_agent()
    agent.handle_event(
        EventEnvelope(
            event_type="stream.started",
            source_agent="test",
            payload={"session_id": "live-42"},
        )
    )

    agent.handle_event(
        EventEnvelope(
            event_type="liveEnded",
            source_agent="nestjs-backend",
            payload={"liveId": "live-42"},
        )
    )

    session = agent.get_session("live-42")
    assert session is not None
    assert session.state == SessionState.ENDED
    assert session.end_reason == "live_ended"


def test_heartbeat_updates_last_heartbeat_and_keeps_session_active():
    agent = _make_agent()
    agent.handle_event(
        EventEnvelope(event_type="stream.started", source_agent="test", payload={"session_id": "s1"})
    )
    before = agent.get_session("s1").last_heartbeat_at

    agent.handle_event(
        EventEnvelope(event_type="stream.heartbeat", source_agent="test", payload={"session_id": "s1"})
    )

    after = agent.get_session("s1")
    assert after.state == SessionState.ACTIVE
    assert after.last_heartbeat_at >= before


def test_heartbeat_without_prior_started_adopts_session():
    """Covers an agent restart where stream.started was missed but
    heartbeats are still arriving -- must not silently drop monitoring."""
    agent = _make_agent()
    agent.handle_event(
        EventEnvelope(event_type="stream.heartbeat", source_agent="test", payload={"session_id": "orphan"})
    )

    session = agent.get_session("orphan")
    assert session is not None
    assert session.state == SessionState.ACTIVE


def test_interruption_triggers_reconnect_within_policy():
    agent = _make_agent()
    agent.handle_event(
        EventEnvelope(event_type="stream.started", source_agent="test", payload={"session_id": "s1"})
    )

    agent.handle_event(
        EventEnvelope(event_type="stream.interrupted", source_agent="test", payload={"session_id": "s1"})
    )

    session = agent.get_session("s1")
    assert session.state == SessionState.INTERRUPTED
    assert session.reconnect_attempts == 1


def test_interruption_exhausting_retries_ends_session():
    """
    MAX_RECONNECT_ATTEMPTS interruptions are each retried (attempts 1..N);
    the guard check runs before incrementing, so it's the (N+1)th
    interruption that finds attempts already at the cap and ends the
    session instead of retrying again.
    """
    agent = _make_agent()
    agent.handle_event(
        EventEnvelope(event_type="stream.started", source_agent="test", payload={"session_id": "s1"})
    )

    for _ in range(MAX_RECONNECT_ATTEMPTS):
        agent.handle_event(
            EventEnvelope(event_type="stream.interrupted", source_agent="test", payload={"session_id": "s1"})
        )
    session_after_n = agent.get_session("s1")
    assert session_after_n.state == SessionState.INTERRUPTED
    assert session_after_n.reconnect_attempts == MAX_RECONNECT_ATTEMPTS

    agent.handle_event(
        EventEnvelope(event_type="stream.interrupted", source_agent="test", payload={"session_id": "s1"})
    )

    session = agent.get_session("s1")
    assert session.state == SessionState.ENDED
    assert session.end_reason == "reconnect_attempts_exhausted"


def test_interruption_without_prior_started_is_handled_gracefully():
    agent = _make_agent()
    # No stream.started was ever seen for this session -- must not raise.
    agent.handle_event(
        EventEnvelope(event_type="stream.interrupted", source_agent="test", payload={"session_id": "ghost"})
    )
    session = agent.get_session("ghost")
    assert session is not None
    assert session.reconnect_attempts == 1


def test_check_stale_sessions_reconnects_silent_session():
    agent = _make_agent()
    agent.handle_event(
        EventEnvelope(event_type="stream.started", source_agent="test", payload={"session_id": "s1"})
    )
    session = agent.get_session("s1")
    session.last_heartbeat_at -= 999  # force staleness
    agent._sessions.save(session)

    agent.check_stale_sessions()

    updated = agent.get_session("s1")
    assert updated.reconnect_attempts == 1
    assert updated.state == SessionState.ACTIVE  # sweep keeps it active while retrying


def test_check_stale_sessions_ends_session_after_max_retries():
    agent = _make_agent()
    agent.handle_event(
        EventEnvelope(event_type="stream.started", source_agent="test", payload={"session_id": "s1"})
    )
    session = agent.get_session("s1")
    session.reconnect_attempts = MAX_RECONNECT_ATTEMPTS
    session.last_heartbeat_at -= 999
    agent._sessions.save(session)

    agent.check_stale_sessions()

    ended = agent.get_session("s1")
    assert ended.state == SessionState.ENDED
    assert ended.end_reason == "heartbeat_stale_timeout"


def test_check_stale_sessions_ignores_fresh_sessions():
    agent = _make_agent()
    agent.handle_event(
        EventEnvelope(event_type="stream.started", source_agent="test", payload={"session_id": "s1"})
    )

    agent.check_stale_sessions()

    session = agent.get_session("s1")
    assert session.state == SessionState.ACTIVE
    assert session.reconnect_attempts == 0


def test_session_state_survives_agent_restart():
    """The whole point of the durable SessionStore: a fresh agent
    instance sharing the same store must see sessions from a prior
    (now-gone) instance -- simulating a process restart."""
    shared_store = build_session_store("memory://local")
    agent1 = _make_agent(store=shared_store)
    agent1.handle_event(
        EventEnvelope(event_type="stream.started", source_agent="test", payload={"session_id": "s1"})
    )
    assert agent1.get_session("s1").state == SessionState.ACTIVE

    agent2 = _make_agent(store=shared_store)
    restarted_view = agent2.get_session("s1")

    assert restarted_view is not None
    assert restarted_view.state == SessionState.ACTIVE
