"""
Data retention policy helpers.

Defines permitted retention windows for event classes. Agents consult this
before persisting payloads; the bus itself is ephemeral (Redis Streams
maxlen / consumer ACK is ops-owned).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable


@dataclass(frozen=True)
class RetentionPolicy:
    """Named retention rule for a data class."""

    name: str
    max_age_seconds: int
    description: str = ""
    # When True, payloads in this class must not leave the process without
    # an explicit export approval (product decision).
    sensitive: bool = False

    def expires_at(self, occurred_at: datetime | None = None) -> datetime:
        base = occurred_at or datetime.now(timezone.utc)
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        return base + timedelta(seconds=self.max_age_seconds)

    def is_expired(self, occurred_at: datetime) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at(occurred_at)


# Default catalog — product may tighten these; do not store longer without approval.
DEFAULT_RETENTION: dict[str, RetentionPolicy] = {
    "event_envelope": RetentionPolicy(
        name="event_envelope",
        max_age_seconds=7 * 24 * 3600,
        description="Bus event payloads retained for debugging / replay windows",
    ),
    "audit_record": RetentionPolicy(
        name="audit_record",
        max_age_seconds=90 * 24 * 3600,
        description="Sensitive-action audit trail",
        sensitive=True,
    ),
    "metrics_sample": RetentionPolicy(
        name="metrics_sample",
        max_age_seconds=30 * 24 * 3600,
        description="Acceptance-criteria metric samples",
    ),
    "gift_effect_state": RetentionPolicy(
        name="gift_effect_state",
        max_age_seconds=24 * 3600,
        description="In-session gift effect coordination state",
    ),
    "stream_session_state": RetentionPolicy(
        name="stream_session_state",
        max_age_seconds=7 * 24 * 3600,
        description="Reconnect counters and session quality samples",
    ),
    "user_interaction_signal": RetentionPolicy(
        name="user_interaction_signal",
        max_age_seconds=30 * 24 * 3600,
        description="Signals used for recommendations — PII-minimized",
        sensitive=True,
    ),
}


def get_retention(name: str) -> RetentionPolicy:
    if name not in DEFAULT_RETENTION:
        raise KeyError(f"Unknown retention policy: {name}")
    return DEFAULT_RETENTION[name]


def parse_occurred_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # Support trailing Z
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def filter_expired(
    items: Iterable[tuple[str, datetime]],
    *,
    policy_name: str,
) -> list[tuple[str, datetime]]:
    """Return items that are still within the retention window."""
    policy = get_retention(policy_name)
    return [(key, when) for key, when in items if not policy.is_expired(when)]
