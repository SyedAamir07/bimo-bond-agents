"""
Durable per-session streaming state.

Tracks one record per live session (keyed by liveId / session_id) so
reconnect-attempt counts and last-heartbeat timestamps survive an agent
restart -- the same durability concern foundation.task_store solves for
the orchestration agent, applied here to streaming sessions.

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

    def to_task_record(self) -> TaskRecord:
        """
        Piggyback on TaskRecord's storage shape (see module docstring).

        TaskStore.all_dispatched() (used for the stale-session sweep)
        filters on status == "dispatched", so an ACTIVE session must use
        that status; ENDED/INTERRUPTED use their own state name so they
        naturally drop out of the sweep once they stop being active.
        """
        status = "dispatched" if self.state == SessionState.ACTIVE else self.state.value
        return TaskRecord(
            task_id=self.session_id,
            action=self.state.value,
            target_agent="live_streaming_agent",
            status=status,
            attempts=self.reconnect_attempts,
            dispatched_at=self.last_heartbeat_at,
            created_at=self.started_at,
            last_error=self.end_reason,
        )

    @classmethod
    def from_task_record(cls, record: TaskRecord) -> "StreamSession":
        try:
            state = SessionState(record.action)
        except ValueError:
            state = SessionState.ACTIVE
        return cls(
            session_id=record.task_id,
            state=state,
            reconnect_attempts=record.attempts,
            started_at=record.created_at,
            last_heartbeat_at=record.dispatched_at,
            end_reason=record.last_error,
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
        """Sessions currently in ACTIVE state (TaskStore's 'dispatched' status)."""
        return [StreamSession.from_task_record(r) for r in self._store.all_dispatched()]

    def delete(self, session_id: str) -> None:
        self._store.delete(session_id)


def build_session_store(event_bus_url: str, *, key_prefix: str = "live_streaming:sessions") -> SessionStore:
    return SessionStore(build_task_store(event_bus_url, key_prefix=key_prefix))
