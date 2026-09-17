"""
Durable per-auction gift-contribution ledger.

Every gift combo observed for an auction is appended here so this
agent can independently recompute the leaderboard / winner from
foundation.auction_rules -- an audit cross-check against whatever the
NestJS backend decided, not a replacement for it (see agent.py module
docstring for why).

Reuses foundation.task_store's Redis/InMemory split the same way
live_streaming_agent's session_store does: one auction's ledger is
stored as a single TaskRecord whose `action` field JSON-encodes the
list of contributions. This keeps durable-storage logic in one place
(foundation) instead of a third near-identical implementation here.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from foundation import GiftContribution, TaskRecord, TaskStore, build_task_store


@dataclass
class AuctionLedger:
    auction_id: str
    contributions: list[GiftContribution] = field(default_factory=list)
    seen_event_ids: set[str] = field(default_factory=set)
    last_known_backend_winner_id: str | None = None
    last_known_backend_status: str | None = None
    updated_at: float = field(default_factory=time.time)

    def to_task_record(self) -> TaskRecord:
        payload = {
            "contributions": [
                {
                    "event_id": c.event_id,
                    "user_id": c.user_id,
                    "coins": c.coins,
                    "occurred_at": c.occurred_at,
                    "cancelled": c.cancelled,
                    "payment_failed": c.payment_failed,
                }
                for c in self.contributions
            ],
            "seen_event_ids": list(self.seen_event_ids),
            "last_known_backend_winner_id": self.last_known_backend_winner_id,
            "last_known_backend_status": self.last_known_backend_status,
        }
        return TaskRecord(
            task_id=self.auction_id,
            action=json.dumps(payload),
            target_agent="live_auction_agent",
            status="dispatched",  # ACTIVE auctions stay in the "dispatched" bucket
            dispatched_at=self.updated_at,
        )

    @classmethod
    def from_task_record(cls, record: TaskRecord) -> "AuctionLedger":
        try:
            payload = json.loads(record.action)
        except (json.JSONDecodeError, TypeError):
            payload = {}
        contributions = [
            GiftContribution(
                event_id=c["event_id"],
                user_id=c["user_id"],
                coins=c["coins"],
                occurred_at=c["occurred_at"],
                cancelled=c.get("cancelled", False),
                payment_failed=c.get("payment_failed", False),
            )
            for c in payload.get("contributions", [])
        ]
        return cls(
            auction_id=record.task_id,
            contributions=contributions,
            seen_event_ids=set(payload.get("seen_event_ids", [])),
            last_known_backend_winner_id=payload.get("last_known_backend_winner_id"),
            last_known_backend_status=payload.get("last_known_backend_status"),
            updated_at=record.dispatched_at,
        )


class LedgerStore:
    """Thin, ledger-shaped facade over a foundation TaskStore."""

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    def get(self, auction_id: str) -> AuctionLedger | None:
        record = self._store.get(auction_id)
        if record is None:
            return None
        return AuctionLedger.from_task_record(record)

    def get_or_create(self, auction_id: str) -> AuctionLedger:
        return self.get(auction_id) or AuctionLedger(auction_id=auction_id)

    def save(self, ledger: AuctionLedger) -> None:
        ledger.updated_at = time.time()
        self._store.save(ledger.to_task_record())

    def delete(self, auction_id: str) -> None:
        self._store.delete(auction_id)

    _TERMINAL_STATUSES = frozenset({"COMPLETED", "CANCELLED", "EXPIRED"})

    def all_active(self) -> list[AuctionLedger]:
        """
        Auctions this agent is still tracking that haven't reached a
        terminal backend status yet.

        Every ledger is stored as TaskRecord.status="dispatched" (see
        AuctionLedger.to_task_record) regardless of the auction's own
        lifecycle -- there's no separate "settled" bucket in the store, so
        this filters on last_known_backend_status instead of trusting
        all_dispatched() alone (which would otherwise count every auction
        this agent has ever seen).
        """
        return [
            ledger
            for ledger in (
                AuctionLedger.from_task_record(r) for r in self._store.all_dispatched()
            )
            if ledger.last_known_backend_status not in self._TERMINAL_STATUSES
        ]


def build_ledger_store(event_bus_url: str, *, key_prefix: str = "live_auction:ledgers") -> LedgerStore:
    return LedgerStore(build_task_store(event_bus_url, key_prefix=key_prefix))
