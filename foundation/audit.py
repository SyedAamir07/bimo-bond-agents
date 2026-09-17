"""
Structured audit trail for sensitive / privileged agent actions.

Per the foundation doc: log actions under least privilege. This is an
append-only in-process + stdout record, optionally mirrored to a durable
AuditSink (see audit_sink.py) so the trail survives a process restart —
required for the 90-day `audit_record` retention policy in retention.py.
Never log secrets or full PII payloads.
"""
from __future__ import annotations

import json
import logging
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .audit_sink import AuditSink

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditRecord:
    action: str
    agent_name: str
    outcome: str  # success | denied | failed
    scope: str | None = None
    correlation_id: str | None = None
    event_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    occurred_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AuditLog:
    """Bounded ring buffer + structured log line for each sensitive action."""

    def __init__(
        self,
        agent_name: str,
        *,
        max_records: int = 1_000,
        sink: "AuditSink | None" = None,
    ) -> None:
        self.agent_name = agent_name
        self._max = max(1, max_records)
        self._records: deque[AuditRecord] = deque(maxlen=self._max)
        self._lock = threading.Lock()
        self._sink = sink

    def record(
        self,
        action: str,
        *,
        outcome: str = "success",
        scope: str | None = None,
        correlation_id: str | None = None,
        event_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> AuditRecord:
        rec = AuditRecord(
            action=action,
            agent_name=self.agent_name,
            outcome=outcome,
            scope=scope,
            correlation_id=correlation_id,
            event_id=event_id,
            detail=self._sanitize(detail or {}),
        )
        with self._lock:
            self._records.append(rec)
        logger.info(
            "audit action=%s outcome=%s scope=%s correlation_id=%s event_id=%s detail=%s",
            rec.action,
            rec.outcome,
            rec.scope,
            rec.correlation_id,
            rec.event_id,
            json.dumps(rec.detail, default=str),
        )
        if self._sink is not None:
            self._sink.write(rec.to_dict())
        return rec

    def recent(self, limit: int = 50) -> list[AuditRecord]:
        with self._lock:
            items = list(self._records)
        return items[-max(1, limit) :]

    def to_list(self, limit: int = 50) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self.recent(limit)]

    @staticmethod
    def _sanitize(detail: dict[str, Any]) -> dict[str, Any]:
        blocked = {
            "password",
            "token",
            "api_token",
            "authorization",
            "secret",
            "card",
            "cvv",
        }
        out: dict[str, Any] = {}
        for key, value in detail.items():
            if key.lower() in blocked:
                out[key] = "[redacted]"
            else:
                out[key] = value
        return out
