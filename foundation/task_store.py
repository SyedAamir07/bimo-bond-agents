"""
Durable task state for the Orchestration Agent (and anything else that
needs crash-safe, restart-safe task tracking).

Doc: "track execution status, and manage timeouts, retries, and duplicate
execution prevention." An in-memory dict cannot satisfy that once the
process restarts mid-flight — a pending task simply vanishes and can
neither be retried nor detected as lost. This module gives every agent
the same two choices every other piece of foundation offers:

  - InMemoryTaskStore  — local dev / unit tests, no infra
  - RedisTaskStore     — production, survives orchestrator restarts

Both implement the same TaskStore interface so orchestration business
logic (agent.py) never talks to Redis directly.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

logger = logging.getLogger(__name__)


@dataclass
class TaskRecord:
    task_id: str
    action: str
    target_agent: str
    status: str = "pending"
    attempts: int = 0
    dispatched_at: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskRecord":
        return cls(
            task_id=data["task_id"],
            action=data["action"],
            target_agent=data.get("target_agent", "unknown"),
            status=data.get("status", "pending"),
            attempts=int(data.get("attempts", 0)),
            dispatched_at=float(data.get("dispatched_at", 0.0)),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            last_error=data.get("last_error"),
        )


class TaskStore(ABC):
    """
    Contract every task store implementation must satisfy.

    Callers own the state-transition logic (agent.py decides *what*
    status a task moves to); the store only guarantees it's read back
    the same way it was written, and survives a restart if durable.
    """

    @abstractmethod
    def get(self, task_id: str) -> TaskRecord | None:
        """Return the record for task_id, or None if unseen."""

    @abstractmethod
    def save(self, record: TaskRecord) -> None:
        """Create or overwrite a task record."""

    @abstractmethod
    def all_dispatched(self) -> Iterable[TaskRecord]:
        """Return every record currently in 'dispatched' status (for timeout sweeps)."""

    @abstractmethod
    def delete(self, task_id: str) -> None:
        """Remove a task record (e.g. after archival)."""


class InMemoryTaskStore(TaskStore):
    """Local dev / unit-test store. Lost on process restart — same
    caveat as InMemoryEventBus."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._lock = threading.Lock()

    def get(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            return self._tasks.get(task_id)

    def save(self, record: TaskRecord) -> None:
        record.updated_at = time.time()
        with self._lock:
            self._tasks[record.task_id] = record

    def all_dispatched(self) -> list[TaskRecord]:
        with self._lock:
            return [r for r in self._tasks.values() if r.status == "dispatched"]

    def delete(self, task_id: str) -> None:
        with self._lock:
            self._tasks.pop(task_id, None)


class RedisTaskStore(TaskStore):
    """
    Production task store backed by Redis.

    Each task is a Redis hash at `{prefix}:{task_id}` holding the JSON
    record, plus membership in a `{prefix}:dispatched` SET so the
    timeout sweep doesn't need to SCAN the keyspace. Both survive an
    orchestrator restart — the whole point of this module.
    """

    def __init__(self, redis_url: str, *, key_prefix: str = "orchestration:tasks") -> None:
        try:
            import redis as redis_lib
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "redis package is required for RedisTaskStore — pip install redis"
            ) from exc

        # protocol=2: see event_bus.py's RedisStreamsEventBus for why this
        # project's Redis (5.0.x) needs RESP2 rather than redis-py's default.
        self._client = redis_lib.from_url(redis_url, decode_responses=True, protocol=2)
        self._prefix = key_prefix
        self._dispatched_set_key = f"{key_prefix}:dispatched"

    def _key(self, task_id: str) -> str:
        return f"{self._prefix}:{task_id}"

    def get(self, task_id: str) -> TaskRecord | None:
        raw = self._client.get(self._key(task_id))
        if raw is None:
            return None
        try:
            return TaskRecord.from_dict(json.loads(raw))
        except (json.JSONDecodeError, KeyError):
            logger.exception("Corrupt task record task_id=%s", task_id)
            return None

    def save(self, record: TaskRecord) -> None:
        record.updated_at = time.time()
        self._client.set(self._key(record.task_id), json.dumps(record.to_dict()))
        if record.status == "dispatched":
            self._client.sadd(self._dispatched_set_key, record.task_id)
        else:
            self._client.srem(self._dispatched_set_key, record.task_id)

    def all_dispatched(self) -> list[TaskRecord]:
        task_ids = self._client.smembers(self._dispatched_set_key)
        out: list[TaskRecord] = []
        for task_id in task_ids:
            record = self.get(task_id)
            if record is None:
                # Record expired/deleted but set membership lingered — clean up.
                self._client.srem(self._dispatched_set_key, task_id)
                continue
            if record.status != "dispatched":
                self._client.srem(self._dispatched_set_key, task_id)
                continue
            out.append(record)
        return out

    def delete(self, task_id: str) -> None:
        self._client.delete(self._key(task_id))
        self._client.srem(self._dispatched_set_key, task_id)


def build_task_store(event_bus_url: str, *, key_prefix: str = "orchestration:tasks") -> TaskStore:
    """
    Same convention as build_event_bus: memory:// for local/tests,
    anything else (a redis:// URL) gets the durable Redis-backed store.
    """
    if event_bus_url.startswith("memory://"):
        return InMemoryTaskStore()
    return RedisTaskStore(event_bus_url, key_prefix=key_prefix)
