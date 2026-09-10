"""
Shared foundation library for all Bimo Bond agents.

Every agent imports this package instead of re-implementing config loading,
logging, the event envelope, the event-bus client, permissions, contracts,
or the NestJS backend bridge. This is the "standardized channel" referenced
in the technical foundation document.
"""
from .backend import BackendClient, BackendClientDisabled
from .config import AgentSettings
from .contracts import AgentContract, EventEnvelope, validate_event_payload
from .event_bus import EventBus, InMemoryEventBus, RedisStreamsEventBus, build_event_bus
from .base_agent import BaseAgent
from .health import HealthStatus, Status
from .metrics import AgentMetrics
from .permissions import PermissionDenied, assert_scope, has_scope
from .reliability import EventDeduplicator, RetryPolicy

__all__ = [
    "AgentSettings",
    "AgentContract",
    "EventEnvelope",
    "validate_event_payload",
    "EventBus",
    "InMemoryEventBus",
    "RedisStreamsEventBus",
    "build_event_bus",
    "BaseAgent",
    "HealthStatus",
    "Status",
    "AgentMetrics",
    "BackendClient",
    "BackendClientDisabled",
    "PermissionDenied",
    "assert_scope",
    "has_scope",
    "EventDeduplicator",
    "RetryPolicy",
]
