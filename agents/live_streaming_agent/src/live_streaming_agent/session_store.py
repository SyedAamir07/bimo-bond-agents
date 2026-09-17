"""
Durable per-session streaming state.

Tracks one record per live session (keyed by liveId / session_id) so
reconnect-attempt counts and last-heartbeat timestamps survive an agent
restart -- the same durability concern foundation.task_store solves for
the orchestration agent, applied here to streaming sessions. Also
tracks the feature counters (heartbeat_count, interruption_count) the
ML anomaly model (anomaly_model.py) scores against.

Uses foundation.task_store's Redis/InMemory split directly rather than
re-inventing it: a "session" is stored as a TaskRecord where `action`
carries the session's last known event_type and `attempts` doubles as
the reconnect-attempt counter. This keeps one durable-storage
implementation in foundation instead of two nearly-identical ones.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from foundation import TaskRecord, TaskStore, build_task_store


class SessionState(str, Enum):
    ACTIVE = "active"
    INTERRUPTED = "interrupted"
    ENDED = "ended"


@dataclass
class StreamSession:
    session_id: str
    state: SessionState = SessionState.ACTIVE
    reconnect_attempts: int = 0
    started_at: float = field(default_factory=time.time)
    last_heartbeat_at: float = field(default_factory=time.time)
    end_reason: str | None = None
    heartbeat_count: int = 0
    interruption_count: int = 0
    # Anomaly score from the most recent ML prediction for this session
    # (None until a prediction has been made -- e.g. still below the
    # minimum-training-samples threshold).
    last_anomaly_score: float | None = None

    def feature_vector(self) -> list[float]:
        """
        The numeric features fed to IsolationForest (anomaly_model.py).
        Order must match anomaly_model.FEATURE_NAMES exactly.

        Deliberately rate-based on heartbeat_count, NOT on
        elapsed/wall-clock time. Two bugs were found and fixed here via
        manual live testing (see live_streaming_agent/tests/test_agent.py's
        test_clean_sessions_are_not_falsely_flagged_as_anomalous for the
        regression test):

          1. Raw counts (heartbeat_count, elapsed_seconds) as features:
             training data comes from COMPLETED sessions (final
             heartbeat_count, e.g. 3), but predictions run on every
             IN-FLIGHT heartbeat (heartbeat_count 1, then 2, then 3).
             The model had never seen a "heartbeat_count=1" healthy
             sample, so every early heartbeat looked anomalous purely
             for being early -- fixed by switching to rates.

          2. Elapsed-time-based rates (interruptions/minute,
             heartbeats/minute): elapsed_seconds is tiny and noisy at
             the first few heartbeats (heartbeats arrive milliseconds
             apart in bursts, or seconds apart under real network
             conditions) -- dividing by a near-zero elapsed time
             produces a huge, unstable rate that swamped the signal
             from the actually-meaningful features (interruption/
             reconnect ratios). Fixed by dropping time-based rates
             entirely and keeping only heartbeat-count-based ratios,
             which are stable from the very first heartbeat (a ratio
             over a growing integer denominator, not over wall-clock
             time this agent doesn't fully control the cadence of).
        """
        return [
            # Interruptions per heartbeat so far -- the core "is this
            # session healthy" signal, independent of how far along it is.
            (self.interruption_count / self.heartbeat_count) if self.heartbeat_count > 0 else 0.0,
            # Reconnect attempts per heartbeat -- same reasoning.
            (self.reconnect_attempts / self.heartbeat_count) if self.heartbeat_count > 0 else 0.0,
        ]

    def to_task_record(self) -> TaskRecord:
        """
        Piggyback on TaskRecord's storage shape (see module docstring).

        TaskStore.all_dispatched() (used for the stale-session sweep)
        filters on status == "dispatched". Both ACTIVE and INTERRUPTED
        sessions must keep that status -- an INTERRUPTED session is
        mid-reconnect and still needs the sweep to catch it if it goes
        silent again (e.g. the client never sends another heartbeat or
        interruption signal after a failed reconnect attempt). Only
        ENDED sessions use their own state name so they drop out of the
        sweep once they're genuinely finished.
        """
        status = "dispatched" if self.state != SessionState.ENDED else self.state.value
        return TaskRecord(
            task_id=self.session_id,
            action=self.state.value,
            target_agent="live_streaming_agent",
            status=status,
            attempts=self.reconnect_attempts,
            dispatched_at=self.last_heartbeat_at,
            created_at=self.started_at,
            last_error=self.end_reason,
            payload={
                "heartbeat_count": self.heartbeat_count,
                "interruption_count": self.interruption_count,
                "last_anomaly_score": self.last_anomaly_score,
            },
        )

    @classmethod
    def from_task_record(cls, record: TaskRecord) -> "StreamSession":
        try:
            state = SessionState(record.action)
        except ValueError:
            state = SessionState.ACTIVE
        payload = record.payload or {}
        return cls(
            session_id=record.task_id,
            state=state,
            reconnect_attempts=record.attempts,
            started_at=record.created_at,
            last_heartbeat_at=record.dispatched_at,
            end_reason=record.last_error,
            heartbeat_count=int(payload.get("heartbeat_count", 0)),
            interruption_count=int(payload.get("interruption_count", 0)),
            last_anomaly_score=payload.get("last_anomaly_score"),
        )


class SessionStore:
    """Thin, session-shaped facade over a foundation TaskStore."""

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    def get(self, session_id: str) -> StreamSession | None:
        record = self._store.get(session_id)
        if record is None:
            return None
        return StreamSession.from_task_record(record)

    def save(self, session: StreamSession) -> None:
        self._store.save(session.to_task_record())

    def all_active(self) -> list[StreamSession]:
        """Sessions currently in ACTIVE or INTERRUPTED state (TaskStore's 'dispatched' status)."""
        return [StreamSession.from_task_record(r) for r in self._store.all_dispatched()]

    def delete(self, session_id: str) -> None:
        self._store.delete(session_id)


def build_session_store(event_bus_url: str, *, key_prefix: str = "live_streaming:sessions") -> SessionStore:
    return SessionStore(build_task_store(event_bus_url, key_prefix=key_prefix))
