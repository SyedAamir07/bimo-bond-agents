"""
Shared foundation library for all Bimo Bond agents.

Every agent imports this package instead of re-implementing config loading,
logging, the event envelope, the event-bus client, permissions, contracts,
or the NestJS backend bridge. This is the "standardized channel" referenced
in the technical foundation document.
"""
from .acceptance import AcceptanceTarget, AcceptanceTracker, PILOT_TARGETS
from .auction_rules import (
    AuctionDecision,
    EligibilityResult,
    GiftContribution,
    resolve_purchase_eligibility,
)
from .audit import AuditLog, AuditRecord
from .backend import BackendClient, BackendClientDisabled
from .config import AgentSettings
from .contracts import AgentContract, EventEnvelope, validate_event_payload
from .event_bus import (
    EventBus,
    InMemoryEventBus,
    RedisStreamsEventBus,
    build_event_bus,
    should_dead_letter,
)
from .event_catalog import (
    AGENT_INTERNAL_EVENTS,
    NEST_FANOUT_EVENTS,
    CatalogEntry,
    all_known_event_types,
    nest_pilot_event_types,
)
from .base_agent import BaseAgent
from .health import HealthStatus, Status
from .metrics import AgentMetrics
from .permissions import PermissionDenied, assert_scope, has_scope
from .reliability import EventDeduplicator, RetryPolicy
from .retention import (
    DEFAULT_RETENTION,
    RetentionPolicy,
    filter_expired,
    get_retention,
    parse_occurred_at,
)

__all__ = [
    "AgentSettings",
    "AgentContract",
    "EventEnvelope",
    "validate_event_payload",
    "EventBus",
    "InMemoryEventBus",
    "RedisStreamsEventBus",
    "build_event_bus",
    "should_dead_letter",
    "CatalogEntry",
    "NEST_FANOUT_EVENTS",
    "AGENT_INTERNAL_EVENTS",
    "nest_pilot_event_types",
    "all_known_event_types",
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
    "AuditLog",
    "AuditRecord",
    "AcceptanceTarget",
    "AcceptanceTracker",
    "PILOT_TARGETS",
    "RetentionPolicy",
    "DEFAULT_RETENTION",
    "get_retention",
    "parse_occurred_at",
    "filter_expired",
    "AuctionDecision",
    "EligibilityResult",
    "GiftContribution",
    "resolve_purchase_eligibility",
]
