"""
Standardized configuration loading.

Every agent reads its settings the same way (env vars, with sane
defaults), so ops can deploy any agent the same way without reading
that agent's source code first.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class AgentSettings:
    """Common settings every agent needs, regardless of its business logic."""

    agent_name: str
    agent_version: str = "0.1.0"

    # Event bus connection (see event_bus.py). In production this points
    # at Redis Streams; the in-memory bus is for local unit tests.
    event_bus_url: str = field(default_factory=lambda: os.getenv("EVENT_BUS_URL", "memory://local"))

    # Redis Streams key shared by NestJS fan-out and all agents.
    event_stream_key: str = field(
        default_factory=lambda: os.getenv("AGENT_EVENTS_STREAM", "agent:events")
    )

    # Consumer group for Redis Streams (defaults to cg:{agent_name} at runtime).
    consumer_group: str = field(default_factory=lambda: os.getenv("AGENT_CONSUMER_GROUP", ""))

    # Which events this agent subscribes to. Kept as config (not code)
    # so the orchestration agent's routing table and each agent's
    # subscription list can be audited together.
    subscribed_topics: tuple[str, ...] = field(default_factory=tuple)

    # Least-privilege permission scopes this agent is allowed to use.
    # The auth layer rejects any tool call outside this list.
    permission_scopes: tuple[str, ...] = field(default_factory=tuple)

    # NestJS HTTP bridge (optional — disabled when empty).
    backend_base_url: str = field(default_factory=lambda: os.getenv("BACKEND_BASE_URL", ""))
    backend_api_token: str = field(default_factory=lambda: os.getenv("BACKEND_API_TOKEN", ""))
    backend_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("BACKEND_TIMEOUT_SECONDS", "10"))
    )

    # Handler reliability
    handler_max_attempts: int = field(
        default_factory=lambda: int(os.getenv("HANDLER_MAX_ATTEMPTS", "3"))
    )
    dedup_max_size: int = field(
        default_factory=lambda: int(os.getenv("DEDUP_MAX_SIZE", "10000"))
    )
    audit_max_records: int = field(
        default_factory=lambda: int(os.getenv("AUDIT_MAX_RECORDS", "1000"))
    )

    # Redis Streams ops: approximate maxlen + poison-message DLQ
    stream_maxlen: int = field(
        default_factory=lambda: int(os.getenv("AGENT_STREAM_MAXLEN", "100000"))
    )
    max_deliveries: int = field(
        default_factory=lambda: int(os.getenv("AGENT_MAX_DELIVERIES", "5"))
    )
    dlq_stream_key: str = field(
        default_factory=lambda: os.getenv("AGENT_EVENTS_DLQ_STREAM", "agent:events:dlq")
    )

    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    health_port: int = field(default_factory=lambda: int(os.getenv("HEALTH_PORT", "8080")))

    def resolved_consumer_group(self) -> str:
        return self.consumer_group or f"cg:{self.agent_name}"

    @classmethod
    def from_env(cls, agent_name: str, **overrides) -> "AgentSettings":
        """Build settings for `agent_name`, letting callers override any field
        (used by each agent's own config.py to fill in its specific topics/scopes)."""
        return cls(agent_name=agent_name, **overrides)
