"""Gift Effects Agent tests: animation resolution, dedup, missing-catalog
handling, combo counts, and restart durability."""
from __future__ import annotations

from foundation import EventEnvelope, InMemoryEventBus

from gift_effects_agent.agent import GiftEffectsAgent
from gift_effects_agent.config import SETTINGS
from gift_effects_agent.trigger_store import build_trigger_store


def _make_agent(store=None) -> GiftEffectsAgent:
    bus = InMemoryEventBus()
    agent = GiftEffectsAgent(SETTINGS, event_bus=bus)
    if store is not None:
        agent._triggers = store
    agent.run()
    return agent


def _gift_event(
    *,
    event_id: str,
    live_id: str = "live-1",
    animation_url: str | None = "https://cdn.example.com/rose.json",
    combo: int = 1,
) -> EventEnvelope:
    gift: dict = {"id": "gift-rose", "name": "Rose"}
    if animation_url is not None:
        gift["animationUrl"] = animation_url
    return EventEnvelope(
        event_type="liveGiftCombo",
        source_agent="nestjs-backend",
        event_id=event_id,
        payload={
            "liveId": live_id,
            "transactionId": f"tx-{event_id}",
            "gift": gift,
            "giftId": "gift-rose",
            "senderId": "user-1",
            "combo": combo,
        },
    )


def test_agent_handles_event_without_crashing():
    bus = InMemoryEventBus()
    agent = GiftEffectsAgent(SETTINGS, event_bus=bus)
    agent.run()

    event = EventEnvelope(
        event_type=SETTINGS.subscribed_topics[0] if SETTINGS.subscribed_topics else "test.event",
        source_agent="test",
        payload={"example": True},
    )
    agent.handle_event(event)  # should not raise


def test_gift_with_animation_url_triggers_effect():
    bus = InMemoryEventBus()
    published: list[EventEnvelope] = []
    bus.subscribe("gift.effect.triggered", published.append)

    agent = GiftEffectsAgent(SETTINGS, event_bus=bus)
    agent.run()

    agent.handle_event(_gift_event(event_id="e1"))

    assert len(published) == 1
    assert published[0].payload["animation"] == "https://cdn.example.com/rose.json"
    assert published[0].payload["gift_id"] == "gift-rose"


def test_gift_without_gift_object_is_skipped():
    bus = InMemoryEventBus()
    skipped: list[EventEnvelope] = []
    bus.subscribe("gift.effect.skipped", skipped.append)

    agent = GiftEffectsAgent(SETTINGS, event_bus=bus)
    agent.run()

    event = EventEnvelope(
        event_type="gift.sent",
        source_agent="test",
        payload={"giftId": "unknown-gift", "liveId": "live-1"},
    )
    agent.handle_event(event)

    assert len(skipped) == 1
    assert skipped[0].payload["reason"] == "no_animation_on_payload"


def test_gift_with_name_but_no_animation_url_falls_back_to_generic_marker():
    bus = InMemoryEventBus()
    published: list[EventEnvelope] = []
    bus.subscribe("gift.effect.triggered", published.append)

    agent = GiftEffectsAgent(SETTINGS, event_bus=bus)
    agent.run()

    agent.handle_event(_gift_event(event_id="e1", animation_url=None))

    assert len(published) == 1
    assert published[0].payload["animation"] == "effect_generic:Rose"


def test_each_combo_tap_triggers_its_own_effect():
    """
    Each tap in a combo burst is its own event with its own
    transactionId and must trigger its own effect -- combo count is a
    rendering hint, not a suppression signal.
    """
    bus = InMemoryEventBus()
    published: list[EventEnvelope] = []
    bus.subscribe("gift.effect.triggered", published.append)

    agent = GiftEffectsAgent(SETTINGS, event_bus=bus)
    agent.run()

    agent.handle_event(_gift_event(event_id="e1", combo=1))
    agent.handle_event(_gift_event(event_id="e2", combo=2))
    agent.handle_event(_gift_event(event_id="e3", combo=3))

    assert len(published) == 3
    assert [p.payload["combo"] for p in published] == [1, 2, 3]


def test_same_event_id_redelivered_is_not_retriggered():
    """
    Covers at-least-once redelivery of the *same* event -- must not
    fire a second animation for an event already handled.
    """
    bus = InMemoryEventBus()
    published: list[EventEnvelope] = []
    bus.subscribe("gift.effect.triggered", published.append)

    agent = GiftEffectsAgent(SETTINGS, event_bus=bus)
    agent.run()

    event = _gift_event(event_id="e1")
    agent.handle_event(event)
    agent.handle_event(event)  # redelivered

    assert len(published) == 1
    assert agent.was_triggered("e1") is True


def test_duplicate_redelivery_is_audited():
    agent = _make_agent()
    event = _gift_event(event_id="e1")
    agent.handle_event(event)
    agent.handle_event(event)

    dup_records = [r for r in agent.audit.recent() if r.action == "gift.effect.duplicate_skipped"]
    assert len(dup_records) == 1


def test_trigger_history_survives_agent_restart():
    """The whole point of the durable TriggerStore: a fresh agent
    instance sharing the same store must see an event already
    triggered by a prior (now-gone) instance -- simulating a restart."""
    shared_store = build_trigger_store("memory://local")
    agent1 = _make_agent(store=shared_store)
    agent1.handle_event(_gift_event(event_id="e1"))
    assert agent1.was_triggered("e1") is True

    agent2 = _make_agent(store=shared_store)
    assert agent2.was_triggered("e1") is True

    # Redelivering the same event_id after the "restart" must still be
    # recognized as already-triggered, not fired again.
    bus2_published: list[EventEnvelope] = []
    agent2.event_bus.subscribe("gift.effect.triggered", bus2_published.append)
    agent2.handle_event(_gift_event(event_id="e1"))
    assert bus2_published == []
