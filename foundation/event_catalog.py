"""
Shared NestJS ↔ agent event catalog.

Keep Socket.IO / Nest emit names as event_type on the Redis stream
(no rename). Agents that need Nest domain events must subscribe to
these exact strings.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogEntry:
    event_type: str
    source: str  # "nestjs" | "agent" | "both"
    pilot: bool
    description: str
    required_payload_keys: tuple[str, ...] = ()


# Events Nest may fan out when AGENT_EVENTS_ENABLED=true.
NEST_FANOUT_EVENTS: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        "liveGiftCombo",
        "nestjs",
        True,
        "Gift combo on a live — primary pilot signal for gift_effects_agent",
        ("liveId",),
    ),
    CatalogEntry(
        "liveGift",
        "nestjs",
        True,
        "Single live gift (also emitted alongside combo)",
        ("liveId",),
    ),
    CatalogEntry(
        "liveEnded",
        "nestjs",
        True,
        "Live session ended — live_streaming_agent",
        ("liveId",),
    ),
    CatalogEntry(
        "liveComment",
        "nestjs",
        True,
        "Live chat comment (optional subscribers)",
        ("liveId",),
    ),
    CatalogEntry(
        "liveModeration",
        "nestjs",
        False,
        "Live moderation action — future content_moderation_agent",
        ("liveId",),
    ),
    CatalogEntry(
        "auctionUpdated",
        "nestjs",
        False,
        "Auction state change — future live_auction_agent",
        (),
    ),
)

# Internal agent-to-agent topics (not Nest Socket.IO names).
AGENT_INTERNAL_EVENTS: tuple[CatalogEntry, ...] = (
    CatalogEntry("gift.sent", "agent", True, "Orchestration / internal gift trigger"),
    CatalogEntry("gift.effect.triggered", "agent", True, "Gift overlay triggered"),
    CatalogEntry("gift.effect.skipped", "agent", True, "Gift effect skipped"),
    CatalogEntry("stream.started", "agent", True, "Start monitoring a stream session"),
    CatalogEntry("stream.heartbeat", "agent", True, "Stream health heartbeat"),
    CatalogEntry("stream.interrupted", "agent", True, "Stream interruption detected"),
    CatalogEntry("stream.reconnect.attempted", "agent", True, "Reconnect attempt recorded"),
    CatalogEntry("stream.monitor.armed", "agent", True, "Live agent armed after stream.started"),
    CatalogEntry("stream.ended", "agent", True, "Stream / live ended for orchestration"),
    CatalogEntry("camera.feature.toggled", "agent", False, "Request camera feature toggle"),
    CatalogEntry("camera.feature.applied", "agent", False, "Camera feature applied"),
    CatalogEntry("camera.feature.rejected", "agent", False, "Camera feature rejected"),
    CatalogEntry("task.requested", "agent", False, "Orchestration task request"),
    CatalogEntry("task.failed", "agent", False, "Orchestration task failed"),
)


def nest_pilot_event_types() -> tuple[str, ...]:
    return tuple(e.event_type for e in NEST_FANOUT_EVENTS if e.pilot)


def all_known_event_types() -> frozenset[str]:
    return frozenset(
        e.event_type for e in (*NEST_FANOUT_EVENTS, *AGENT_INTERNAL_EVENTS)
    )
