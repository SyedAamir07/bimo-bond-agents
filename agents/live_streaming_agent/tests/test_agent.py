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


def test_check_stale_sessions_catches_interrupted_session_gone_silent_again():
    """
    Regression test: an INTERRUPTED session (mid-reconnect after an
    explicit stream.interrupted event) must still be caught by the
    stale-session sweep if it goes silent again -- without this, an
    INTERRUPTED session that never sends another heartbeat or
    interruption signal would be stuck forever, since it had dropped
    out of all_active()'s underlying "dispatched" bucket.
    """
    agent = _make_agent()
    agent.handle_event(
        EventEnvelope(event_type="stream.started", source_agent="test", payload={"session_id": "s1"})
    )
    agent.handle_event(
        EventEnvelope(event_type="stream.interrupted", source_agent="test", payload={"session_id": "s1"})
    )
    interrupted = agent.get_session("s1")
    assert interrupted.state == SessionState.INTERRUPTED
    assert interrupted.reconnect_attempts == 1

    # Force staleness while still INTERRUPTED (client never came back).
    interrupted.last_heartbeat_at -= 999
    agent._sessions.save(interrupted)

    agent.check_stale_sessions()

    swept = agent.get_session("s1")
    assert swept is not None
    assert swept.reconnect_attempts == 2  # sweep caught it and retried again


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


# --- ML anomaly detection (advisory layer) -----------------------------


def _run_normal_session(agent: LiveStreamingAgent, session_id: str, heartbeats: int = 5) -> None:
    """Simulate a healthy session: start, N heartbeats, clean end."""
    agent.handle_event(
        EventEnvelope(event_type="stream.started", source_agent="test", payload={"session_id": session_id})
    )
    for _ in range(heartbeats):
        agent.handle_event(
            EventEnvelope(event_type="stream.heartbeat", source_agent="test", payload={"session_id": session_id})
        )
    agent.handle_event(
        EventEnvelope(event_type="liveEnded", source_agent="test", payload={"liveId": session_id})
    )


def test_clean_sessions_are_not_falsely_flagged_as_anomalous():
    """
    Regression test for a real bug caught in manual live testing: every
    heartbeat of every session -- including perfectly healthy ones --
    was being flagged as anomalous. Root cause was raw counts
    (heartbeat_count, elapsed_seconds) in the feature vector: training
    data comes from COMPLETED sessions (final heartbeat_count, e.g. 5),
    but predictions run on every IN-FLIGHT heartbeat (heartbeat_count
    1, 2, 3, ...) -- the model had never seen a "heartbeat_count=1"
    healthy sample, so every early heartbeat looked anomalous purely
    for being early. feature_vector() now uses rates (interruptions
    per heartbeat, etc.) specifically so this can't happen. This test
    trains on many identical clean sessions and asserts a same-pattern
    session is NOT flagged at any point in its lifecycle -- not just
    at the end.
    """
    bus = InMemoryEventBus()
    anomalies: list[EventEnvelope] = []
    bus.subscribe("stream.health.anomaly_detected", anomalies.append)

    agent = LiveStreamingAgent(SETTINGS, event_bus=bus)
    agent.model._min_training_samples = 8
    agent.model._retrain_interval_samples = 4
    agent.run()

    # Train on 8 identical-pattern clean sessions (3 heartbeats, 0 interruptions).
    for i in range(8):
        _run_normal_session(agent, f"clean-{i}", heartbeats=3)
    assert agent.model.is_trained is True

    # A fresh session with the SAME clean pattern must not be flagged
    # at ANY heartbeat -- not just heartbeat 1 (early/partial), and not
    # heartbeat 3 (matches the completed-session shape).
    anomalies.clear()
    _run_normal_session(agent, "clean-test", heartbeats=3)

    assert anomalies == [], (
        f"Expected no anomalies for a clean session matching the training "
        f"pattern, got {len(anomalies)}: {[a.payload for a in anomalies]}"
    )


def test_heartbeat_does_not_crash_before_model_trained():
    """With zero training samples, predict() must fall back gracefully,
    not raise or flag every heartbeat as anomalous."""
    agent = _make_agent()
    agent.handle_event(
        EventEnvelope(event_type="stream.started", source_agent="test", payload={"session_id": "s1"})
    )
    agent.handle_event(
        EventEnvelope(event_type="stream.heartbeat", source_agent="test", payload={"session_id": "s1"})
    )

    session = agent.get_session("s1")
    assert session.heartbeat_count == 1
    assert agent.model.is_trained is False


def test_session_completion_feeds_ml_training_set():
    agent = _make_agent()
    assert agent.model.sample_count == 0

    _run_normal_session(agent, "s1")

    assert agent.model.sample_count == 1


def test_model_trains_after_min_samples_reached():
    agent = _make_agent()
    agent.model._min_training_samples = 3
    agent.model._retrain_interval_samples = 1

    for i in range(3):
        _run_normal_session(agent, f"session-{i}")

    assert agent.model.is_trained is True


def test_anomaly_detected_event_published_for_outlier_session():
    """
    Train the model on a REALISTIC mix of mostly-clean sessions with a
    little natural variation (some sessions have 1 interruption), then
    feed a session with far more interruptions than anything in
    training and confirm it's actually flagged (is_anomaly=True), not
    just that a score was produced.

    Deliberately NOT trained on zero-variance data (every session
    identical): manual live testing found that a zero-variance training
    set makes IsolationForest unable to call ANYTHING anomalous
    (decision_function returns exactly 0.0 for every input, including
    wild outliers) -- there's no spread to measure a deviation against.
    Real traffic always has some natural variation, so this mirrors
    production more accurately than an all-identical training set would.

    ML anomaly detection is advisory only: it publishes a warning
    event, it never itself ends the session or blocks a reconnect
    (reconnect/end-session logic is exercised by the deterministic-
    policy tests above and is unaffected by whether the model is
    trained).
    """
    bus = InMemoryEventBus()
    agent = LiveStreamingAgent(SETTINGS, event_bus=bus)
    agent.model._min_training_samples = 20
    agent.model._retrain_interval_samples = 5
    agent.run()

    # Realistic mix: most sessions clean, a few with one minor interruption.
    for i in range(20):
        heartbeats = 3
        agent.handle_event(
            EventEnvelope(event_type="stream.started", source_agent="test", payload={"session_id": f"mix-{i}"})
        )
        if i % 6 == 0:  # ~17% of sessions have one interruption partway through
            agent.handle_event(
                EventEnvelope(
                    event_type="stream.interrupted", source_agent="test", payload={"session_id": f"mix-{i}"}
                )
            )
        for _ in range(heartbeats):
            agent.handle_event(
                EventEnvelope(
                    event_type="stream.heartbeat", source_agent="test", payload={"session_id": f"mix-{i}"}
                )
            )
        agent.handle_event(
            EventEnvelope(event_type="liveEnded", source_agent="test", payload={"liveId": f"mix-{i}"})
        )
    assert agent.model.is_trained is True

    # Far more interruptions than anything seen in training.
    agent.handle_event(
        EventEnvelope(event_type="stream.started", source_agent="test", payload={"session_id": "extreme-outlier"})
    )
    for _ in range(3):
        agent.handle_event(
            EventEnvelope(
                event_type="stream.interrupted", source_agent="test", payload={"session_id": "extreme-outlier"}
            )
        )
    agent.handle_event(
        EventEnvelope(event_type="stream.heartbeat", source_agent="test", payload={"session_id": "extreme-outlier"})
    )

    session = agent.get_session("extreme-outlier")
    assert session.last_anomaly_score is not None
    prediction = agent.model.predict(session.feature_vector())
    assert prediction.is_anomaly is True, (
        f"Expected the extreme outlier to be flagged; got score={prediction.score}"
    )


def test_feature_vector_shape_is_stable():
    """Regression guard: anomaly_model.FEATURE_NAMES order must match
    StreamSession.feature_vector()'s output order exactly, or scores
    become meaningless without any error being raised."""
    from live_streaming_agent.anomaly_model import FEATURE_NAMES
    from live_streaming_agent.session_store import StreamSession

    session = StreamSession(session_id="x", heartbeat_count=2, interruption_count=1, reconnect_attempts=1)
    vector = session.feature_vector()
    assert len(vector) == len(FEATURE_NAMES)


# --- ML training-sample durability (survives an agent restart) --------


def test_training_samples_persist_across_model_instances():
    """
    A TrainingSampleStore-backed model must not lose accumulated
    training data when a fresh StreamAnomalyModel instance is built
    against the same store -- simulating an agent restart. Without
    this, every restart reset training progress to zero regardless of
    how much real traffic had already been observed.
    """
    from foundation import InMemoryTaskStore
    from live_streaming_agent.anomaly_model import StreamAnomalyModel, TrainingSampleStore

    shared_store = TrainingSampleStore(InMemoryTaskStore())

    model1 = StreamAnomalyModel(
        min_training_samples=100,  # high enough that this instance never trains itself
        retrain_interval_samples=10,
        sample_store=shared_store,
    )
    for _ in range(5):
        model1.record_completed_session([0.0, 0.0])
    assert model1.sample_count == 5
    assert model1.is_trained is False  # below min_training_samples

    # "Restart": a fresh model instance against the SAME durable store.
    model2 = StreamAnomalyModel(
        min_training_samples=100,
        retrain_interval_samples=10,
        sample_store=shared_store,
    )
    assert model2.sample_count == 5  # recovered, not reset to 0


def test_restart_recovered_samples_above_threshold_fit_immediately():
    """
    If a restart recovers enough samples to already clear
    min_training_samples, the model must be usable (trained) right
    away -- not wait for one more completed session -- otherwise a
    restarted agent would serve zero predictions despite already
    having sufficient history.
    """
    from foundation import InMemoryTaskStore
    from live_streaming_agent.anomaly_model import StreamAnomalyModel, TrainingSampleStore

    shared_store = TrainingSampleStore(InMemoryTaskStore())

    model1 = StreamAnomalyModel(
        min_training_samples=5,
        retrain_interval_samples=2,
        sample_store=shared_store,
    )
    for _ in range(5):
        model1.record_completed_session([0.0, 0.0])
    assert model1.is_trained is True

    # "Restart": fresh instance, same store, samples already >= min.
    model2 = StreamAnomalyModel(
        min_training_samples=5,
        retrain_interval_samples=2,
        sample_store=shared_store,
    )
    assert model2.sample_count == 5
    assert model2.is_trained is True  # fit-on-load, no extra session needed


def test_model_without_sample_store_still_works_in_memory_only():
    """Backward-compat: omitting sample_store (the default) must behave
    exactly as before -- in-memory only, no persistence, no crash."""
    from live_streaming_agent.anomaly_model import StreamAnomalyModel

    model = StreamAnomalyModel(min_training_samples=3, retrain_interval_samples=1)
    for _ in range(3):
        model.record_completed_session([0.0, 0.0])
    assert model.sample_count == 3
    assert model.is_trained is True


def test_live_streaming_agent_wires_durable_sample_store_by_default():
    """The agent must construct its model with a Redis/InMemory-backed
    sample_store (via build_task_store on the shared event bus URL),
    not the in-memory-only default -- otherwise restart persistence
    silently never applies in production."""
    agent = _make_agent()
    assert agent.model._sample_store is not None
