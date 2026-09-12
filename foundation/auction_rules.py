"""
Auditable auction / gifting eligibility rules (software, not LLM).

Doc: implement auction prices and eligibility through auditable software
rules covering ties, cancellations, duplicate events, and payment failures.
Product must still approve the numeric policy; this module encodes the
deterministic decision tree so it can be unit-tested and audited.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class AuctionDecision(str, Enum):
    ELIGIBLE = "eligible"
    REJECTED_DUPLICATE = "rejected_duplicate"
    REJECTED_CANCELLED = "rejected_cancelled"
    REJECTED_PAYMENT_FAILED = "rejected_payment_failed"
    REJECTED_BELOW_LEADER = "rejected_below_leader"
    TIE_BREAK_EARLIEST = "tie_break_earliest"
    TIE_BREAK_EXPLICIT = "tie_break_explicit"


@dataclass(frozen=True)
class GiftContribution:
    event_id: str
    user_id: str
    coins: int
    occurred_at: str  # ISO — earlier wins ties when policy is earliest
    cancelled: bool = False
    payment_failed: bool = False


@dataclass(frozen=True)
class EligibilityResult:
    decision: AuctionDecision
    winner_user_id: str | None
    winning_coins: int
    reason: str
    considered_event_ids: tuple[str, ...]


def resolve_purchase_eligibility(
    contributions: Iterable[GiftContribution],
    *,
    seen_event_ids: set[str] | None = None,
    tie_break: str = "earliest",
) -> EligibilityResult:
    """
    Deterministic eligibility from gift contributions.

    - Duplicate event_id → ignored (and counted as rejected_duplicate if alone)
    - cancelled / payment_failed → excluded
    - Highest coins wins
    - Ties: earliest occurred_at (default) or explicit multi-winner flag
    """
    seen = set(seen_event_ids or ())
    considered: list[str] = []
    valid: list[GiftContribution] = []
    duplicate_only = True

    for c in contributions:
        considered.append(c.event_id)
        if c.event_id in seen:
            continue
        seen.add(c.event_id)
        duplicate_only = False
        if c.cancelled:
            continue
        if c.payment_failed:
            continue
        if c.coins <= 0:
            continue
        valid.append(c)

    if not considered:
        return EligibilityResult(
            decision=AuctionDecision.REJECTED_BELOW_LEADER,
            winner_user_id=None,
            winning_coins=0,
            reason="no_contributions",
            considered_event_ids=(),
        )

    if not valid:
        # Prefer a specific rejection reason when every item failed the same way
        raw = list(contributions)
        if raw and all(c.event_id in (seen_event_ids or set()) for c in raw):
            decision = AuctionDecision.REJECTED_DUPLICATE
            reason = "all_duplicate_events"
        elif raw and all(c.cancelled for c in raw):
            decision = AuctionDecision.REJECTED_CANCELLED
            reason = "all_cancelled"
        elif raw and all(c.payment_failed for c in raw):
            decision = AuctionDecision.REJECTED_PAYMENT_FAILED
            reason = "all_payment_failed"
        elif duplicate_only:
            decision = AuctionDecision.REJECTED_DUPLICATE
            reason = "all_duplicate_events"
        else:
            decision = AuctionDecision.REJECTED_BELOW_LEADER
            reason = "no_valid_contributions"
        return EligibilityResult(
            decision=decision,
            winner_user_id=None,
            winning_coins=0,
            reason=reason,
            considered_event_ids=tuple(considered),
        )

    # Aggregate coins per user (sum of valid gifts)
    by_user: dict[str, int] = {}
    earliest: dict[str, str] = {}
    for c in valid:
        by_user[c.user_id] = by_user.get(c.user_id, 0) + c.coins
        prev = earliest.get(c.user_id)
        if prev is None or c.occurred_at < prev:
            earliest[c.user_id] = c.occurred_at

    top_coins = max(by_user.values())
    leaders = [uid for uid, coins in by_user.items() if coins == top_coins]

    if len(leaders) == 1:
        return EligibilityResult(
            decision=AuctionDecision.ELIGIBLE,
            winner_user_id=leaders[0],
            winning_coins=top_coins,
            reason="highest_contribution",
            considered_event_ids=tuple(considered),
        )

    if tie_break == "explicit":
        return EligibilityResult(
            decision=AuctionDecision.TIE_BREAK_EXPLICIT,
            winner_user_id=None,
            winning_coins=top_coins,
            reason="tie_requires_product_rule",
            considered_event_ids=tuple(considered),
        )

    # earliest contribution among tied leaders
    winner = min(leaders, key=lambda uid: earliest[uid])
    return EligibilityResult(
        decision=AuctionDecision.TIE_BREAK_EARLIEST,
        winner_user_id=winner,
        winning_coins=top_coins,
        reason="tie_broken_by_earliest_contribution",
        considered_event_ids=tuple(considered),
    )
