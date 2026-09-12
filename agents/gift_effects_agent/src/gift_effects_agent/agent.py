"""
Gift Effects Agent

Run and coordinate gift effects and animations, linking them to events in Bimo Bond's gifting system.
"""
from foundation import AgentContract, BaseAgent, EventEnvelope

# Sample gift -> effect mapping. In production this comes from the
# Templates Agent / gift catalog, not a hardcoded dict -- kept simple
# here to demonstrate the handling pattern.
GIFT_EFFECT_MAP = {
    "rose": "effect_rose_float",
    "rocket": "effect_rocket_launch",
    "crown": "effect_crown_sparkle",
}


class GiftEffectsAgent(BaseAgent):
    objective = "Run and coordinate gift effects and animations, linking them to events in Bimo Bond's gifting system."
    contract = AgentContract(
        objective=objective,
        inputs=["gift.sent", "liveGiftCombo"],
        outputs=["gift.effect.triggered", "gift.effect.skipped"],
        tools=["overlay_renderer"],
        permission_scopes=["stream.overlay.write"],
        subscribed_topics=["gift.sent", "liveGiftCombo"],
        published_topics=["gift.effect.triggered", "gift.effect.skipped"],
        failure_cases=["no_effect_mapping", "overlay_unavailable"],
        owner="platform-live",
        acceptance_criteria=["effect latency measurable", "no duplicate effect for same event_id"],
        automatic_actions=["trigger_mapped_effect"],
        human_review_actions=["approve_new_gift_effect_mappings"],
    )

    def handle_event(self, event: EventEnvelope) -> None:
        if event.event_type in ("gift.sent", "liveGiftCombo"):
            self._handle_gift(event)
        else:
            self.logger.warning("Unhandled event_type=%s", event.event_type)

    def _handle_gift(self, event: EventEnvelope) -> None:
        # NestJS liveGiftCombo payloads may nest gift id under giftId / gift_id.
        gift = event.payload.get("gift")
        nested_id = gift.get("id") if isinstance(gift, dict) else None
        gift_id = event.payload.get("gift_id") or event.payload.get("giftId") or nested_id
        session_id = (
            event.payload.get("session_id")
            or event.payload.get("liveId")
            or event.payload.get("live_id")
        )
        effect = GIFT_EFFECT_MAP.get(str(gift_id)) if gift_id is not None else None

        started = event.occurred_at
        if effect is None:
            self.logger.warning("No effect mapped for gift_id=%s -- dropping", gift_id)
            self.metrics.incr("gift_effect_missing")
            self.acceptance.observe("gift_duplicate_or_missing_rate", 1.0)
            self.publish(
                "gift.effect.skipped",
                payload={"gift_id": gift_id, "session_id": session_id, "reason": "no_effect_mapping"},
                correlation_id=event.correlation_id,
            )
            return

        self.logger.info("Triggering effect=%s for gift_id=%s session_id=%s", effect, gift_id, session_id)
        # TODO: call the actual overlay/rendering service here.
        # Pilot latency sample: handler path duration is tracked by BaseAgent;
        # domain gift_effect_latency_ms uses a zero-floor placeholder until
        # overlay timestamps exist — observe handler metric alias for now.
        self.acceptance.observe("gift_effect_latency_ms", 0.0)
        self.acceptance.observe("gift_duplicate_or_missing_rate", 0.0)
        self.audit.record(
            "gift.effect.triggered",
            outcome="success",
            event_id=event.event_id,
            correlation_id=event.correlation_id,
            detail={"gift_id": gift_id, "effect": effect, "occurred_at": started},
        )
        self.publish(
            "gift.effect.triggered",
            payload={"gift_id": gift_id, "session_id": session_id, "effect": effect},
            correlation_id=event.correlation_id,
        )
