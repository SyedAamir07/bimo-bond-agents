"""
Standard health/status reporting.

The orchestration agent (and any monitoring dashboard) polls every
agent the same way, so each agent must expose health the same shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Status(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass
class HealthStatus:
    agent_name: str
    status: Status = Status.OK
    detail: str = ""
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Live counters surfaced to /health so a monitoring dashboard (or
    # AgentsAdminService's fleet probe) doesn't need a second endpoint just
    # to show "how much work is this agent doing right now". Each agent
    # updates these via its own callback (see active_sessions_provider /
    # error_count_provider on BaseAgent) -- foundation has no notion of what
    # a "session" is, so it can't compute them itself.
    active_sessions: int = 0
    error_count: int = 0

    def refresh(self) -> None:
        """Stamp checked_at on each poll so /health is not a stale construct time."""
        self.checked_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        self.refresh()
        return {
            "agent_name": self.agent_name,
            "status": self.status.value,
            "detail": self.detail,
            "checked_at": self.checked_at,
            "active_sessions": self.active_sessions,
            "error_count": self.error_count,
        }
