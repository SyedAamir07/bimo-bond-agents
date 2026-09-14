"""
Durable audit sinks.

`audit.py`'s AuditLog is a bounded in-process ring buffer by design (fast,
zero-dependency, always available even if Redis is down). Its own
docstring says: "production should ship these lines to a durable store."

This module is that store. An AuditSink is an optional side-channel
AuditLog writes to *in addition to* keeping its in-memory ring buffer —
the ring buffer still powers the live /audit endpoint; the sink is what
survives a restart and satisfies the 90-day `audit_record` retention
policy in retention.py.
"""
from __future__ import annotations

import json
import logging
import threading
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class AuditSink(ABC):
    """Contract every durable audit sink must satisfy."""

    @abstractmethod
    def write(self, record: dict[str, Any]) -> None:
        """Persist one audit record. Must not raise — a broken sink must
        never take an agent down; log and drop instead."""


class NullAuditSink(AuditSink):
    """Default no-op sink — matches today's in-memory-only behavior."""

    def write(self, record: dict[str, Any]) -> None:  # noqa: D401
        return


class RedisAuditSink(AuditSink):
    """
    Append-only durable audit trail backed by a Redis Stream.

    Uses its own stream (default `agent:audit`) — separate from the
    event bus stream — trimmed to the `audit_record` retention window's
    approximate size rather than time (Redis Streams trims by count;
    ops should also run a periodic XTRIM/archival job for the 90-day
    policy in retention.py if long-term storage is required beyond
    Redis's own retention).
    """

    def __init__(
        self,
        redis_url: str,
        *,
        stream_key: str = "agent:audit",
        maxlen: int = 500_000,
    ) -> None:
        try:
            import redis as redis_lib
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "redis package is required for RedisAuditSink — pip install redis"
            ) from exc

        self._client = redis_lib.from_url(redis_url, decode_responses=True)
        self.stream_key = stream_key
        self.maxlen = maxlen
        self._lock = threading.Lock()

    def write(self, record: dict[str, Any]) -> None:
        try:
            fields = {
                "action": record.get("action", ""),
                "agent_name": record.get("agent_name", ""),
                "outcome": record.get("outcome", ""),
                "scope": record.get("scope") or "",
                "correlation_id": record.get("correlation_id") or "",
                "event_id": record.get("event_id") or "",
                "occurred_at": record.get("occurred_at", ""),
                "detail": json.dumps(record.get("detail", {}), default=str),
            }
            with self._lock:
                self._client.xadd(
                    self.stream_key,
                    fields,
                    maxlen=self.maxlen,
                    approximate=True,
                )
        except Exception:  # noqa: BLE001 - audit persistence must never crash the agent
            logger.exception("Failed to persist audit record to Redis sink")


def build_audit_sink(event_bus_url: str, *, stream_key: str = "agent:audit") -> AuditSink:
    """
    Same convention as build_event_bus/build_task_store: memory:// (or
    anything non-redis) gets a no-op sink for local/tests; a redis://
    URL gets the durable stream-backed sink.
    """
    if not event_bus_url or event_bus_url.startswith("memory://"):
        return NullAuditSink()
    if event_bus_url.startswith("redis://") or event_bus_url.startswith("rediss://"):
        return RedisAuditSink(event_bus_url, stream_key=stream_key)
    return NullAuditSink()
