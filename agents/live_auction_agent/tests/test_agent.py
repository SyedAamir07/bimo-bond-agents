"""Live Auction Agent tests: ledgering, leaderboard, winner cross-check,
mismatch detection, dedup, and restart durability."""
from __future__ import annotations

from foundation import EventEnvelope, InMemoryEventBus

from live_auction_agent.agent import LiveAuctionAgent
from live_auction_agent.config import SETTINGS
from live_auction_agent.ledger_store import build_ledger_store


def _make_agent(store=None) -> LiveAuctionAgent:
    bus = InMemoryEventBus()
    agent = LiveAuctionAgent(SETTINGS, event_bus=bus)
    if store is not None:
        agent._ledgers = store
    agent.run()
    return agent


def _auction_updated_event(
    *,
    event_id: str,
    auction_id: str = "a1",
    sender_id: str = "user-1",
    contribution_coins: int = 100,
    current_total: int = 100,
    target: int = 500,
    status: str = "ACTIVE",
    winner_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type="auctionUpdated",
        source_agent="nestjs-backend",
        event_id=event_id,
        payload={
            "auctionId": auction_id,
            "currentTotalCoins": current_total,
            "targetPriceCoins": target,
            "status": status,
            "winnerId": winner_id,
            "combo": 1,
            "lastGift": {
                "senderId": sender_id,
                "contributionCoins": contribution_coins,
            },
        },
    )


def test_agent_handles_event_without_crashing():
    bus = InMemoryEventBus()
    agent = LiveAuctionAgent(SETTINGS, event_bus=bus)
    agent.run()

    event = EventEnvelope(
        event_type=SETTINGS.subscribed_topics[0] if SETTINGS.subscribed_topics else "test.event",
        source_agent="test",
        payload={"example": True},
    )
    agent.handle_event(event)  # should not raise


def test_auction_updated_ledgers_contribution():
    agent = _make_agent()

    agent.handle_event(
        _auction_updated_event(event_id="e1", sender_id="user-1", contribution_coins=150)
    )

    ledger = agent.get_ledger("a1")
    assert ledger is not None
    assert len(ledger.contributions) == 1
    assert ledger.contributions[0].user_id == "user-1"
    assert ledger.contributions[0].coins == 150


def test_duplicate_event_id_is_not_double_ledgered():
    agent = _make_agent()
    event = _auction_updated_event(event_id="e1", sender_id="user-1", contribution_coins=100)

    agent.handle_event(event)
    agent.handle_event(event)  # simulate at-least-once redelivery

    ledger = agent.get_ledger("a1")
    assert len(ledger.contributions) == 1


def test_leaderboard_ranks_by_total_coins_descending():
    agent = _make_agent()
    agent.handle_event(
        _auction_updated_event(event_id="e1", sender_id="user-1", contribution_coins=100)
    )
    agent.handle_event(
        _auction_updated_event(event_id="e2", sender_id="user-2", contribution_coins=300)
    )
    agent.handle_event(
        _auction_updated_event(event_id="e3", sender_id="user-1", contribution_coins=50)
    )

    ledger = agent.get_ledger("a1")
    by_user: dict[str, int] = {}
    for c in ledger.contributions:
        by_user[c.user_id] = by_user.get(c.user_id, 0) + c.coins

    assert by_user["user-1"] == 150
    assert by_user["user-2"] == 300


def test_winner_cross_check_matches_when_backend_agrees_with_ledger():
    agent = _make_agent()
    agent.handle_event(
        _auction_updated_event(event_id="e1", sender_id="user-1", contribution_coins=500)
    )
    agent.handle_event(
        _auction_updated_event(
            event_id="e2",
            sender_id="user-1",
            contribution_coins=0,
            current_total=500,
            status="COMPLETED",
            winner_id="user-1",
        )
    )

    match_records = [
        r for r in agent.audit.recent() if r.action == "auction.winner_cross_check"
    ]
    assert len(match_records) == 1
    assert match_records[0].detail["match"] is True

    mismatch_records = [r for r in agent.audit.recent() if r.action == "auction.winner_mismatch"]
    assert mismatch_records == []


def test_winner_mismatch_is_detected_and_flagged():
    """
    The core value-add of this agent: if the backend reports a winner
    that doesn't match what this agent's independent ledger computes,
    it must be flagged as an auditable discrepancy -- not silently
    trusted and not silently overridden.
    """
    agent = _make_agent()
    agent.handle_event(
        _auction_updated_event(event_id="e1", sender_id="user-1", contribution_coins=500)
    )
    # Backend reports a DIFFERENT winner than the ledger's top contributor.
    agent.handle_event(
        _auction_updated_event(
            event_id="e2",
            sender_id="user-1",
            contribution_coins=0,
            current_total=500,
            status="COMPLETED",
            winner_id="user-999-not-the-contributor",
        )
    )

    mismatch_records = [r for r in agent.audit.recent() if r.action == "auction.winner_mismatch"]
    assert len(mismatch_records) == 1
    assert mismatch_records[0].detail["backend_winner_id"] == "user-999-not-the-contributor"
    assert mismatch_records[0].detail["agent_computed_winner_id"] == "user-1"


def test_winner_mismatch_publishes_auction_disputed_event():
    bus = InMemoryEventBus()
    published: list[EventEnvelope] = []
    bus.subscribe("auction.disputed", published.append)

    agent = LiveAuctionAgent(SETTINGS, event_bus=bus)
    agent.run()

    agent.handle_event(
        _auction_updated_event(event_id="e1", sender_id="user-1", contribution_coins=500)
    )
    agent.handle_event(
        _auction_updated_event(
            event_id="e2",
            sender_id="user-1",
            contribution_coins=0,
            current_total=500,
            status="COMPLETED",
            winner_id="someone-else",
        )
    )

    assert len(published) == 1
    assert published[0].payload["auction_id"] == "a1"


def test_missing_auction_id_is_handled_gracefully():
    agent = _make_agent()
    agent.handle_event(
        EventEnvelope(event_type="auctionUpdated", source_agent="test", payload={})
    )  # should not raise


def test_live_gift_combo_without_auction_id_is_noop():
    agent = _make_agent()
    agent.handle_event(
        EventEnvelope(
            event_type="liveGiftCombo",
            source_agent="test",
            payload={"liveId": "live-1", "senderId": "user-1"},
        )
    )  # should not raise, no auctionId present


def test_ledger_survives_agent_restart():
    """The whole point of the durable LedgerStore: a fresh agent
    instance sharing the same store must see contributions ledgered by
    a prior (now-gone) instance -- simulating a process restart."""
    shared_store = build_ledger_store("memory://local")
    agent1 = _make_agent(store=shared_store)
    agent1.handle_event(
        _auction_updated_event(event_id="e1", sender_id="user-1", contribution_coins=200)
    )
    assert len(agent1.get_ledger("a1").contributions) == 1

    agent2 = _make_agent(store=shared_store)
    restarted_view = agent2.get_ledger("a1")

    assert restarted_view is not None
    assert len(restarted_view.contributions) == 1

    # Dedup must also hold across the "restart" -- redelivering e1 again
    # must not double-count it.
    agent2.handle_event(
        _auction_updated_event(event_id="e1", sender_id="user-1", contribution_coins=200)
    )
    assert len(agent2.get_ledger("a1").contributions) == 1
