"""
Standard event envelope and machine-readable agent contract.

Every event that crosses the "standardized channel" between agents uses
EventEnvelope. AgentContract documents each agent's I/O, permissions,
and acceptance criteria in a shape that can be served over HTTP.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Minimal event_type -> required payload keys. Unknown types pass through.
EVENT_PAYLOAD_KEYS: dict[str, tuple[str, ...]] = {
    "task.requested": ("task_id", "action"),
    "camera.feature.toggled": ("feature", "enabled"),
    "gift.sent": ("gift_id",),
    "stream.started": (),
    "liveGiftCombo": ("liveId",),
    "liveEnded": ("liveId",),
    "liveComment": ("liveId",),
}


@dataclass
class EventEnvelope:
    """
    A single event on the bus.

    event_type:      e.g. "gift.sent", "stream.started", "stream.interrupted"
    source_agent:    which agent produced this event (for tracing/audit)
    payload:         event-specific data (kept as a plain dict; each agent
                      validates the shape it expects on read)
    event_id:        unique id -> lets consumers de-duplicate on retry
    correlation_id:  ties related events together (e.g. one live session)
    occurred_at:     when the source agent says the event happened
    schema_version:  bump when payload shape changes, so old consumers
                      can detect and reject/ignore events they don't understand
    """

    event_type: str
    source_agent: str
    payload: dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str | None = None
    occurred_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source_agent": self.source_agent,
            "correlation_id": self.correlation_id,
            "occurred_at": self.occurred_at,
            "schema_version": self.schema_version,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EventEnvelope":
        payload = data.get("payload", {})
        if isinstance(payload, str):
            import json

            payload = json.loads(payload) if payload else {}
        return cls(
            event_type=data["event_type"],
            source_agent=data["source_agent"],
            payload=payload if isinstance(payload, dict) else {},
            event_id=data.get("event_id", str(uuid.uuid4())),
            correlation_id=data.get("correlation_id"),
            occurred_at=data.get("occurred_at", datetime.now(timezone.utc).isoformat()),
            schema_version=int(data.get("schema_version", 1)),
        )


def validate_event_payload(event: EventEnvelope, *, strict: bool = False) -> list[str]:
    """
    Light validation against EVENT_PAYLOAD_KEYS.

    Returns a list of missing required keys. Unknown event types yield a
    warning log and an empty list (pass-through) unless strict=True.
    """
    required = EVENT_PAYLOAD_KEYS.get(event.event_type)
    if required is None:
        msg = f"No payload contract registered for event_type={event.event_type}"
        if strict:
            return [msg]
        logger.warning(msg)
        return []
    missing = [key for key in required if key not in event.payload]
    return missing


@dataclass
class AgentContract:
    """
    Machine-readable agent contract (doc: Define a contract for each agent).
    Served at GET /contract so ops and orchestration can audit uniformly.
    """

    objective: str
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    permission_scopes: list[str] = field(default_factory=list)
    subscribed_topics: list[str] = field(default_factory=list)
    published_topics: list[str] = field(default_factory=list)
    failure_cases: list[str] = field(default_factory=list)
    owner: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    automatic_actions: list[str] = field(default_factory=list)
    human_review_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentContract":
        return cls(
            objective=data.get("objective", ""),
            inputs=list(data.get("inputs", [])),
            outputs=list(data.get("outputs", [])),
            tools=list(data.get("tools", [])),
            permission_scopes=list(data.get("permission_scopes", [])),
            subscribed_topics=list(data.get("subscribed_topics", [])),
            published_topics=list(data.get("published_topics", [])),
            failure_cases=list(data.get("failure_cases", [])),
            owner=data.get("owner", ""),
            acceptance_criteria=list(data.get("acceptance_criteria", [])),
            automatic_actions=list(data.get("automatic_actions", [])),
            human_review_actions=list(data.get("human_review_actions", [])),
        )

    def validate(self) -> list[str]:
        """Return human-readable problems; empty list means OK for scaffolding."""
        problems: list[str] = []
        if not self.objective.strip():
            problems.append("objective is required")
        if not self.permission_scopes:
            problems.append("permission_scopes should not be empty")
        if not self.subscribed_topics and not self.published_topics:
            problems.append("subscribed_topics or published_topics should be set")
        return problems
