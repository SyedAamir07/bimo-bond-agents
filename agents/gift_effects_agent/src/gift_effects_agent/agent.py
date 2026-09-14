"""
Gift Effects Agent

Run and coordinate gift effects and animations, linking them to events
in Bimo Bond's gifting system (project doc: AG-04).

Design notes:

The gift catalog (name, thumbnail, animationUrl, audioUrl, color) is
already attached to every `liveGiftCombo` / `gift.sent` payload by the
backend (`giftPresentationPayload` in gifts.service.ts) -- this agent
does not maintain its own gift->effect mapping table. Its job is
coordination, not catalog ownership:

  1. Resolve which animation to play from the payload's own `gift`
     object (falls back to a generic name-derived marker if a gift
     arrives without one -- e.g. a hand-built event during a pilot/test).
  2. Trigger exactly once per event, durably: each tap in a combo burst
     is its own `gift.sent` / `liveGiftCombo` event with its own
     transactionId (the backend creates one GiftTransaction row per
     send call, not per combo) and is meant to trigger its own effect
     -- the combo's `combo` count tells the overlay how to render
     escalating visuals, it is not a signal to suppress repeated
     animations. What must be suppressed is the same event_id arriving
     twice (at-least-once redelivery), which is why dedup here is keyed
     on event_id via a durable trigger_store, not on gift/session/combo.
  3. Track latency + duplicate/missing rate against the acceptance
     targets in foundation/acceptance.py (M-02 in the project doc).

Deterministic workflow, not an LLM agent -- which animation plays is
data already on the event, not a model decision.
"""
from __future__ import annotations

import time

from foundation import AgentContract, BaseAgent, EventEnvelope

from .trigger_store import build_trigger_store


def _extract_gift_id(payload: dict) -> str | None:
    gift = payload.get("gift")
    nested_id = gift.get("id") if isinstance(gift, dict) else None
    gift_id = payload.get("gift_id") or payload.get("giftId") or nested_id
    return str(gift_id) if gift_id is not None else None


def _extract_session_id(payload: dict) -> str | None:
    session_id = (
        payload.get("session_id")
        or payload.get("liveId")
        or payload.get("live_id")
    )
    return str(session_id) if session_id is not None else None


def _extract_animation(payload: dict) -> str | None:
    """
    The animation to play comes from the gift catalog object the
    backend already attaches -- animationUrl first (the actual asset),
    falling back to a name-derived marker for events that arrive
    without a full gift object (manual/pilot testing).
    """
    gift = payload.get("gift")
    if isinstance(gift, dict):
        animation_url = gift.get("animationUrl")
        if animation_url:
            return str(animation_url)
        name = gift.get("name")
        if name:
            return f"effect_generic:{name}"
    return None


class GiftEffectsAgent(BaseAgent):
    objective = (
        "Run and coordinate gift effects and animations, linking them "
        "to events in Bimo Bond's gifting system."
    )
    contract = AgentContract(
        objective=objective,
        inputs=["gift.sent", "liveGiftCombo"],
        outputs=["gift.effect.triggered", "gift.effect.skipped"],
        tools=["trigger_store (durable)", "overlay_renderer"],
        permission_scopes=["gift.catalog.read", "stream.overlay.write"],
        subscribed_topics=["gift.sent", "liveGiftCombo"],
        published_topics=["gift.effect.triggered", "gift.effect.skipped"],
        failure_cases=["no_animation_on_payload", "overlay_unavailable", "duplicate_event_redelivered"],
        owner="platform-live",
        acceptance_criteria=[
            "gift_effect_latency_ms tracked against baseline/target",
            "gift_duplicate_or_missing_rate tracked against baseline/target",
            "exactly one trigger per event_id, even across a restart",
        ],
        automatic_actions=["trigger_effect_from_payload_catalog", "skip_on_missing_animation"],
        human_review_actions=["approve_new_gift_catalog_entries"],
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Durable by default: build_trigger_store() returns an in-memory
        # store for memory:// (local/tests) and a Redis-backed one
        # otherwise, so an event already handled before a restart is
        # not re-triggered after one (BaseAgent's own dedup is in-memory
        # only and would otherwise replay it post-restart).
        self._triggers = build_trigger_store(
            self.settings.event_bus_url,
            key_prefix=f"gift_effects:{self.settings.agent_name}:triggers",
        )

    def handle_event(self, event: EventEnvelope) -> None:
        if event.event_type in ("gift.sent", "liveGiftCombo"):
            self._handle_gift(event)
        else:
            self.logger.warning("Unhandled event_type=%s", event.event_type)

    def _handle_gift(self, event: EventEnvelope) -> None:
        payload = event.payload
        gift_id = _extract_gift_id(payload)
        session_id = _extract_session_id(payload)

        if self._triggers.was_triggered(event.event_id):
            self.logger.info(
                "event_id=%s already triggered -- skipping duplicate redelivery",
                event.event_id,
            )
            self.metrics.incr("gift_effect_duplicate_skipped")
            self.acceptance.observe("gift_duplicate_or_missing_rate", 1.0)
            self.audit.record(
                "gift.effect.duplicate_skipped",
                outcome="denied",
                event_id=event.event_id,
                correlation_id=event.correlation_id,
                detail={"gift_id": gift_id},
            )
            return

        animation = _extract_animation(payload)
        if animation is None:
            self.logger.warning(
                "No animation on payload for gift_id=%s event_id=%s -- dropping",
                gift_id, event.event_id,
            )
            self.metrics.incr("gift_effect_missing")
            self.acceptance.observe("gift_duplicate_or_missing_rate", 1.0)
            self.audit.record(
                "gift.effect.skipped",
                outcome="failed",
                event_id=event.event_id,
                correlation_id=event.correlation_id,
                detail={"gift_id": gift_id, "reason": "no_animation_on_payload"},
            )
            self.publish(
                "gift.effect.skipped",
                payload={"gift_id": gift_id, "session_id": session_id, "reason": "no_animation_on_payload"},
                correlation_id=event.correlation_id,
            )
            return

        trigger_start = time.perf_counter()
        self._triggers.mark_triggered(event.event_id, effect=animation)
        latency_ms = (time.perf_counter() - trigger_start) * 1000.0

        self.logger.info(
            "Triggering effect for gift_id=%s event_id=%s session_id=%s animation=%s",
            gift_id, event.event_id, session_id, animation,
        )
        self.acceptance.observe("gift_effect_latency_ms", latency_ms)
        self.acceptance.observe("gift_duplicate_or_missing_rate", 0.0)
        self.audit.record(
            "gift.effect.triggered",
            outcome="success",
            event_id=event.event_id,
            correlation_id=event.correlation_id,
            detail={"gift_id": gift_id, "animation": animation, "combo": payload.get("combo")},
        )
        self.publish(
            "gift.effect.triggered",
            payload={
                "gift_id": gift_id,
                "session_id": session_id,
                "animation": animation,
                "combo": payload.get("combo"),
            },
            correlation_id=event.correlation_id,
        )

    def was_triggered(self, event_id: str) -> bool:
        """Expose trigger state for tests / ops inspection."""
        return self._triggers.was_triggered(event_id)
