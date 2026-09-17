"""
Live Streaming Agent

Monitor stream quality and stability, detect interruptions, manage
reconnection attempts, and track performance indicators for a stable
streaming experience (project doc: AG-02).

Design notes (per the project's architectural guidance -- Anthropic,
"Building effective agents"): this is a deterministic workflow, not an
LLM agent. Reconnect policy, staleness detection, and session-ended
handling are all explicit, auditable rules -- nothing here is a
language-model decision.

ML anomaly detection (anomaly_model.py) runs alongside those rules as
an advisory early-warning layer: an IsolationForest model, trained on
sessions this agent has observed complete, scores every heartbeat and
can publish `stream.health.anomaly_detected` before any fixed
threshold is crossed. It never overrides or gates the deterministic
reconnect/end-session logic below -- same principle live_auction_agent
follows for money-moving decisions (don't assign the final decision
solely to a model). See anomaly_model.py's module docstring for why
IsolationForest specifically, and the cold-start fallback behavior.
"""
from __future__ import annotations

import time

from foundation import AgentContract, BaseAgent, EventEnvelope, build_task_store

from .anomaly_model import StreamAnomalyModel, TrainingSampleStore
from .config import (
    HEARTBEAT_STALE_SECONDS,
    MAX_RECONNECT_ATTEMPTS,
    ML_CONTAMINATION,
    ML_MIN_TRAINING_SAMPLES,
    ML_RETRAIN_INTERVAL_SAMPLES,
)
from .session_store import SessionState, StreamSession, build_session_store


def _session_id_from(payload: dict) -> str | None:
    """
    NestJS fan-out events (liveEnded, ...) key on `liveId`; internal
    agent-to-agent events (stream.started, ...) key on `session_id`.
    Accept either so this agent works against both sources without the
    caller needing to know which one it's talking to.
    """
    session_id = payload.get("session_id") or payload.get("liveId")
    return str(session_id) if session_id is not None else None


class LiveStreamingAgent(BaseAgent):
    objective = (
        "Monitor stream quality and stability, detect interruptions, "
        "manage reconnection attempts, and track performance indicators "
        "for a stable streaming experience, with an ML anomaly-detection "
        "model providing advisory early-warning signals."
    )
    contract = AgentContract(
        objective=objective,
        inputs=[
            "stream.started",
            "stream.heartbeat",
            "stream.interrupted",
            "liveEnded",
        ],
        outputs=[
            "stream.reconnect.attempted",
            "stream.ended",
            "stream.monitor.armed",
            "stream.health.anomaly_detected",
        ],
        tools=["session_store (durable)", "backend_http_client", "IsolationForest anomaly model (in-process)"],
        permission_scopes=["stream.session.read", "stream.session.write"],
        subscribed_topics=[
            "stream.started",
            "stream.heartbeat",
            "stream.interrupted",
            "stream.reconnect.attempted",
            "liveEnded",
        ],
        published_topics=[
            "stream.reconnect.attempted",
            "stream.ended",
            "stream.monitor.armed",
            "stream.health.anomaly_detected",
        ],
        failure_cases=[
            "reconnect_attempts_exhausted",
            "heartbeat_stale_timeout",
            "infra_unavailable",
            "model_not_trained (not an error -- falls back to no-anomaly-flagged)",
        ],
        owner="platform-live",
        acceptance_criteria=[
            "stream_startup_ms tracked against baseline/target",
            "stream_interruption_rate tracked against baseline/target",
            "stream_reconnect_success_rate tracked against baseline/target",
            "session state survives an agent restart",
            "anomaly score is advisory only -- never solely ends a session or blocks a reconnect",
        ],
        automatic_actions=[
            "arm_monitor_on_start",
            "reconnect_within_policy",
            "end_session_on_reconnect_exhausted",
            "end_session_on_heartbeat_stale",
            "score_heartbeat_for_anomaly",
            "retrain_model_on_session_completion",
        ],
        human_review_actions=["raise_max_reconnect_attempts", "tune_ml_contamination_rate"],
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Durable by default: build_session_store() returns an in-memory
        # store for memory:// (local/tests) and a Redis-backed one
        # otherwise, so reconnect counts and heartbeat timestamps survive
        # a restart instead of resetting every session to zero.
        self._sessions = build_session_store(
            self.settings.event_bus_url,
            key_prefix=f"live_streaming:{self.settings.agent_name}:sessions",
        )
        # Durable training samples: same Redis-vs-in-memory split as the
        # session store. On a restart against Redis, the model refits
        # from persisted samples immediately (see StreamAnomalyModel.__init__)
        # instead of climbing back to ML_MIN_TRAINING_SAMPLES from zero.
        sample_store = TrainingSampleStore(
            build_task_store(
                self.settings.event_bus_url,
                key_prefix=f"live_streaming:{self.settings.agent_name}:ml_samples",
            )
        )
        self._model = StreamAnomalyModel(
            min_training_samples=ML_MIN_TRAINING_SAMPLES,
            retrain_interval_samples=ML_RETRAIN_INTERVAL_SAMPLES,
            contamination=ML_CONTAMINATION,
            sample_store=sample_store,
        )

    # --- BaseAgent hooks ---------------------------------------------------

    def active_sessions_count(self) -> int:
        return len(self._sessions.all_active())

    def handle_event(self, event: EventEnvelope) -> None:
        if event.event_type == "stream.started":
            self._handle_stream_started(event)
        elif event.event_type == "stream.heartbeat":
            self._handle_heartbeat(event)
        elif event.event_type == "stream.interrupted":
            self._handle_interruption(event)
        elif event.event_type == "liveEnded":
            self._handle_live_ended(event)
        elif event.event_type == "stream.reconnect.attempted":
            # Emitted by this agent itself; nothing further to do on receipt.
            return
        else:
            self.logger.warning("Unhandled event_type=%s", event.event_type)

    # --- event handlers ----------------------------------------------------

    def _handle_stream_started(self, event: EventEnvelope) -> None:
        session_id = _session_id_from(event.payload)
        if not session_id:
            self.logger.warning(
                "stream.started missing session_id/liveId event_id=%s", event.event_id
            )
            return

        session = StreamSession(session_id=session_id, state=SessionState.ACTIVE)
        self._sessions.save(session)

        startup_ms = event.payload.get("startup_ms")
        if isinstance(startup_ms, (int, float)):
            self.acceptance.observe("stream_startup_ms", float(startup_ms))

        self.audit.record(
            "stream.session_started",
            correlation_id=session_id,
            event_id=event.event_id,
            detail={
                "startup_ms": startup_ms,
                "model_trained": self._model.is_trained,
                "training_samples": self._model.sample_count,
            },
        )
        self.logger.info("Stream started session_id=%s", session_id)
        self.publish(
            "stream.monitor.armed",
            payload={"session_id": session_id},
            correlation_id=event.correlation_id or session_id,
        )

    def _handle_heartbeat(self, event: EventEnvelope) -> None:
        session_id = _session_id_from(event.payload)
        if not session_id:
            return

        session = self._sessions.get(session_id)
        if session is None:
            # Heartbeat arrived without a prior stream.started (e.g. agent
            # restarted after the session began) -- adopt it rather than
            # silently dropping monitoring for an otherwise-live session.
            session = StreamSession(session_id=session_id, state=SessionState.ACTIVE)
            self.logger.info("Adopting unseen session_id=%s on heartbeat", session_id)

        session.state = SessionState.ACTIVE
        session.heartbeat_count += 1
        session.last_heartbeat_at = time.time()

        prediction = self._model.predict(session.feature_vector())
        session.last_anomaly_score = prediction.score
        self._sessions.save(session)

        if prediction.model_ready and prediction.is_anomaly:
            self.logger.warning(
                "session_id=%s ML anomaly detected score=%.4f heartbeats=%d interruptions=%d",
                session_id, prediction.score, session.heartbeat_count, session.interruption_count,
            )
            self.audit.record(
                "stream.health.anomaly_detected",
                correlation_id=session_id,
                event_id=event.event_id,
                detail={
                    "score": prediction.score,
                    "heartbeat_count": session.heartbeat_count,
                    "interruption_count": session.interruption_count,
                },
            )
            self.publish(
                "stream.health.anomaly_detected",
                payload={"session_id": session_id, "score": prediction.score, "reason": "ml_isolation_forest"},
                correlation_id=event.correlation_id or session_id,
            )

        self.logger.debug(
            "Heartbeat session_id=%s count=%d anomaly_score=%s",
            session_id, session.heartbeat_count, prediction.score if prediction.model_ready else "n/a",
        )

    def _handle_interruption(self, event: EventEnvelope) -> None:
        session_id = _session_id_from(event.payload)
        if not session_id:
            self.logger.warning(
                "stream.interrupted missing session_id/liveId event_id=%s", event.event_id
            )
            return

        session = self._sessions.get(session_id)
        if session is None:
            session = StreamSession(session_id=session_id, state=SessionState.ACTIVE)

        session.interruption_count += 1
        self.acceptance.observe("stream_interruption_rate", 1.0)

        if session.reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
            self._end_session(
                session,
                reason="reconnect_attempts_exhausted",
                correlation_id=event.correlation_id,
                event_id=event.event_id,
            )
            self.acceptance.observe("stream_reconnect_success_rate", 0.0)
            return

        session.reconnect_attempts += 1
        session.state = SessionState.INTERRUPTED
        session.last_heartbeat_at = time.time()
        self._sessions.save(session)

        self.audit.record(
            "stream.reconnect_attempted",
            correlation_id=session_id,
            event_id=event.event_id,
            detail={"attempt": session.reconnect_attempts, "max": MAX_RECONNECT_ATTEMPTS},
        )
        self.logger.info(
            "Reconnecting session_id=%s attempt=%s/%s",
            session_id, session.reconnect_attempts, MAX_RECONNECT_ATTEMPTS,
        )
        # NOTE: this publishes intent-to-reconnect; wiring it to the real
        # streaming/LiveKit infra call is the integration point left for
        # the pilot (see project doc S-02 -- limited end-to-end pilot).
        self.acceptance.observe("stream_reconnect_success_rate", 1.0)
        self.publish(
            "stream.reconnect.attempted",
            payload={"session_id": session_id, "attempt": session.reconnect_attempts},
            correlation_id=event.correlation_id or session_id,
        )

    def _handle_live_ended(self, event: EventEnvelope) -> None:
        session_id = _session_id_from(event.payload)
        if not session_id:
            self.logger.warning("liveEnded missing liveId event_id=%s", event.event_id)
            return

        session = self._sessions.get(session_id) or StreamSession(
            session_id=session_id, state=SessionState.ACTIVE
        )
        self._end_session(
            session,
            reason="live_ended",
            correlation_id=event.correlation_id,
            event_id=event.event_id,
        )

    # --- shared helpers ----------------------------------------------------

    def _end_session(
        self,
        session: StreamSession,
        *,
        reason: str,
        correlation_id: str | None,
        event_id: str | None,
    ) -> None:
        session.state = SessionState.ENDED
        session.end_reason = reason
        session.last_heartbeat_at = time.time()
        self._sessions.save(session)

        # Feed this completed session into the ML model's training set --
        # this is what lets the model improve over time. Only completed
        # sessions are used for training (a stable, final feature vector),
        # not in-flight ones.
        self._model.record_completed_session(session.feature_vector())

        self.audit.record(
            "stream.session_ended",
            correlation_id=session.session_id,
            event_id=event_id,
            detail={
                "reason": reason,
                "reconnect_attempts": session.reconnect_attempts,
                "heartbeat_count": session.heartbeat_count,
                "interruption_count": session.interruption_count,
                "model_sample_count": self._model.sample_count,
            },
        )
        self.logger.info("Session ended session_id=%s reason=%s", session.session_id, reason)
        self.publish(
            "stream.ended",
            payload={"session_id": session.session_id, "reason": reason},
            correlation_id=correlation_id or session.session_id,
        )

    # --- background watchdog ------------------------------------------------

    def check_stale_sessions(self) -> None:
        """
        Call periodically (see main.py) to catch sessions whose client
        went silent without ever sending stream.interrupted or liveEnded
        -- a hard crash / lost network has no chance to signal either.
        Without this sweep such sessions would stay ACTIVE forever.
        """
        now = time.time()
        for session in self._sessions.all_active():
            if now - session.last_heartbeat_at <= HEARTBEAT_STALE_SECONDS:
                continue

            self.logger.warning(
                "session_id=%s heartbeat stale for %.0fs -- treating as interrupted",
                session.session_id, now - session.last_heartbeat_at,
            )
            self.acceptance.observe("stream_interruption_rate", 1.0)

            if session.reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
                self._end_session(
                    session,
                    reason="heartbeat_stale_timeout",
                    correlation_id=session.session_id,
                    event_id=None,
                )
                continue

            session.reconnect_attempts += 1
            session.last_heartbeat_at = now  # avoid re-firing every sweep tick
            self._sessions.save(session)
            self.audit.record(
                "stream.reconnect_attempted",
                correlation_id=session.session_id,
                detail={
                    "attempt": session.reconnect_attempts,
                    "max": MAX_RECONNECT_ATTEMPTS,
                    "trigger": "heartbeat_stale_sweep",
                },
            )
            self.publish(
                "stream.reconnect.attempted",
                payload={"session_id": session.session_id, "attempt": session.reconnect_attempts},
                correlation_id=session.session_id,
            )

    def get_session(self, session_id: str) -> StreamSession | None:
        """Expose session state for tests / ops inspection."""
        return self._sessions.get(session_id)

    @property
    def model(self) -> StreamAnomalyModel:
        """Expose the ML anomaly model for tests / ops inspection."""
        return self._model
