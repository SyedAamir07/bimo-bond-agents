"""
Live Auction Agent

Track gifts, rank leaders, calculate the final price, and identify the
user entitled to purchase under Bimo Bond's rules and policies
(project doc: AG-08).

Design notes -- read before changing the winner logic:

The NestJS backend (`gifts.service.ts`) is the system of record: it
already holds the authoritative auction row, applies the coin totals
inside a DB transaction, and assigns `winnerId` the moment
`currentTotalCoins` crosses `targetPriceCoins`. This agent does NOT
duplicate or override that decision -- per the project doc, "Do not
assign the final decision solely to a language model" and, by the same
principle, no *single* component (agent included) should be the sole
source of truth for money-moving decisions without independent
cross-checking.

What this agent adds:
  1. An independent ledger of every gift contribution it observes,
     replayed through foundation.auction_rules (deterministic, no LLM)
     to compute what the winner *should* be under the documented rules.
  2. A comparison against the backend's own reported winner/status on
     every `auctionUpdated` event -- any mismatch is a
     `auction.winner_mismatch` audit record + `auction.disputed`
     publish, which is exactly the auditable-discrepancy signal a
     dispute/fraud review process needs (project doc AG-08a: "Clarify
     how gifts relate to bids and purchase eligibility").
  3. A durable leaderboard so ranking is queryable independent of the
     backend's DB (ops/analytics use, and survives this agent's own
     restarts).
"""
from __future__ import annotations

from foundation import (
    AgentContract,
    BaseAgent,
    EventEnvelope,
    GiftContribution,
    resolve_purchase_eligibility,
)

from .config import AUCTION_TIE_BREAK_POLICY
from .ledger_store import AuctionLedger, build_ledger_store


def _coerce_coins(value: object) -> int:
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


class LiveAuctionAgent(BaseAgent):
    objective = (
        "Track gifts, rank leaders, calculate the final price, and "
        "identify the user entitled to purchase under Bimo Bond's "
        "rules and policies."
    )
    contract = AgentContract(
        objective=objective,
        inputs=["auctionUpdated", "liveGiftCombo", "liveEnded"],
        outputs=["auction.leaderboard.updated", "auction.disputed", "auction.settled"],
        tools=["ledger_store (durable)", "auction_rules (deterministic)", "backend_http_client"],
        permission_scopes=["auction.read", "auction.audit.write"],
        subscribed_topics=["auctionUpdated", "liveGiftCombo", "liveEnded"],
        published_topics=["auction.leaderboard.updated", "auction.disputed", "auction.settled"],
        failure_cases=[
            "winner_mismatch_vs_backend",
            "duplicate_gift_event",
            "malformed_auction_payload",
        ],
        owner="platform-commerce",
        acceptance_criteria=[
            "every gift contribution ledgered exactly once (dedup on event_id)",
            "winner cross-check runs on every auctionUpdated with status=COMPLETED",
            "no auction decision is made by this agent alone -- backend remains system of record",
        ],
        automatic_actions=[
            "ledger_gift_contribution",
            "recompute_leaderboard",
            "cross_check_winner_on_completion",
        ],
        human_review_actions=[
            "resolve_explicit_tie",
            "resolve_winner_mismatch",
        ],
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Durable by default: build_ledger_store() returns an in-memory
        # store for memory:// (local/tests) and a Redis-backed one
        # otherwise, so the contribution ledger survives an agent restart.
        self._ledgers = build_ledger_store(
            self.settings.event_bus_url,
            key_prefix=f"live_auction:{self.settings.agent_name}:ledgers",
        )

    # --- BaseAgent hook ---------------------------------------------------

    def handle_event(self, event: EventEnvelope) -> None:
        if event.event_type == "auctionUpdated":
            self._handle_auction_updated(event)
        elif event.event_type == "liveGiftCombo":
            self._handle_live_gift_combo(event)
        elif event.event_type == "liveEnded":
            self._handle_live_ended(event)
        else:
            self.logger.warning("Unhandled event_type=%s", event.event_type)

    # --- event handlers ----------------------------------------------------

    def _handle_auction_updated(self, event: EventEnvelope) -> None:
        payload = event.payload
        auction_id = payload.get("auctionId")
        if not auction_id:
            self.logger.warning(
                "auctionUpdated missing auctionId event_id=%s", event.event_id
            )
            return

        ledger = self._ledgers.get_or_create(auction_id)

        # Ledger the contribution carried on this update, if present
        # (gifts.service.ts attaches `lastGift` + `combo` on every emit
        # triggered by a gift). Dedup on event_id like every other
        # foundation-backed consumer -- at-least-once delivery must not
        # double-count a contribution.
        last_gift = payload.get("lastGift")
        if isinstance(last_gift, dict) and event.event_id not in ledger.seen_event_ids:
            sender_id = last_gift.get("senderId")
            coins = payload.get("currentTotalCoins")  # running total, not per-gift
            contribution_coins = last_gift.get("contributionCoins")
            if sender_id and contribution_coins is not None:
                ledger.contributions.append(
                    GiftContribution(
                        event_id=event.event_id,
                        user_id=str(sender_id),
                        coins=_coerce_coins(contribution_coins),
                        occurred_at=event.occurred_at,
                    )
                )
                ledger.seen_event_ids.add(event.event_id)

        ledger.last_known_backend_winner_id = payload.get("winnerId")
        ledger.last_known_backend_status = payload.get("status")
        self._ledgers.save(ledger)

        self._publish_leaderboard(auction_id, ledger, correlation_id=event.correlation_id)

        if ledger.last_known_backend_status == "COMPLETED":
            self._cross_check_winner(auction_id, ledger, event)

    def _handle_live_gift_combo(self, event: EventEnvelope) -> None:
        # Informational for now -- liveGiftCombo doesn't always carry an
        # auctionId (it's the general live-gift channel, auctions are a
        # subset). auctionUpdated is the authoritative per-auction signal
        # this agent ledgers against; this handler exists so the
        # subscription doesn't warn on every ordinary gift combo.
        auction_id = event.payload.get("auctionId")
        if auction_id:
            self.logger.debug(
                "liveGiftCombo for auction_id=%s (ledgered via auctionUpdated)",
                auction_id,
            )

    def _handle_live_ended(self, event: EventEnvelope) -> None:
        # Auctions are keyed by auctionId, not liveId, so this is a
        # no-op today. Kept as an explicit branch (not "unhandled")
        # since liveEnded is a legitimate subscribed topic once
        # per-live auction lookups are added.
        return

    # --- core logic ----------------------------------------------------

    def _publish_leaderboard(
        self, auction_id: str, ledger: AuctionLedger, *, correlation_id: str | None
    ) -> None:
        by_user: dict[str, int] = {}
        for c in ledger.contributions:
            if c.cancelled or c.payment_failed:
                continue
            by_user[c.user_id] = by_user.get(c.user_id, 0) + c.coins
        leaderboard = sorted(
            ({"user_id": uid, "coins": coins} for uid, coins in by_user.items()),
            key=lambda row: row["coins"],
            reverse=True,
        )

        self.publish(
            "auction.leaderboard.updated",
            payload={"auction_id": auction_id, "leaderboard": leaderboard},
            correlation_id=correlation_id or auction_id,
        )

    def _cross_check_winner(
        self, auction_id: str, ledger: AuctionLedger, event: EventEnvelope
    ) -> None:
        """
        Independently recompute eligibility from the ledgered
        contributions and compare against the backend's own reported
        winner. A mismatch doesn't mean the backend is wrong -- this
        agent's ledger may be incomplete (e.g. it started listening
        mid-auction) -- but it is exactly the kind of auditable signal
        a dispute/fraud reviewer needs to investigate, per AG-08a.
        """
        result = resolve_purchase_eligibility(
            ledger.contributions,
            tie_break=AUCTION_TIE_BREAK_POLICY,
        )

        backend_winner = ledger.last_known_backend_winner_id
        agent_winner = result.winner_user_id

        self.audit.record(
            "auction.winner_cross_check",
            correlation_id=auction_id,
            event_id=event.event_id,
            detail={
                "backend_winner_id": backend_winner,
                "agent_computed_winner_id": agent_winner,
                "agent_decision": result.decision.value,
                "agent_reason": result.reason,
                "match": backend_winner == agent_winner,
            },
        )

        if backend_winner is not None and agent_winner is not None and backend_winner != agent_winner:
            self.logger.warning(
                "auction_id=%s winner MISMATCH backend=%s agent=%s reason=%s",
                auction_id, backend_winner, agent_winner, result.reason,
            )
            self.audit.record(
                "auction.winner_mismatch",
                outcome="failed",
                correlation_id=auction_id,
                event_id=event.event_id,
                detail={
                    "backend_winner_id": backend_winner,
                    "agent_computed_winner_id": agent_winner,
                    "reason": result.reason,
                },
            )
            self.publish(
                "auction.disputed",
                payload={
                    "auction_id": auction_id,
                    "backend_winner_id": backend_winner,
                    "agent_computed_winner_id": agent_winner,
                    "reason": f"winner_mismatch:{result.reason}",
                },
                correlation_id=auction_id,
            )
            return

        self.logger.info(
            "auction_id=%s winner cross-check OK winner=%s decision=%s",
            auction_id, backend_winner, result.decision.value,
        )
        self.publish(
            "auction.settled",
            payload={
                "auction_id": auction_id,
                "winner_id": backend_winner,
                "decision": result.decision.value,
            },
            correlation_id=auction_id,
        )

    def get_ledger(self, auction_id: str) -> AuctionLedger | None:
        """Expose ledger state for tests / ops inspection."""
        return self._ledgers.get(auction_id)
