# Live Auction Agent

Track gifts, rank leaders, calculate the final price, and identify the
user entitled to purchase under Bimo Bond's rules and policies
(project doc requirement **AG-08**).

## Agent contract

| Field | Value |
|---|---|
| Objective | Track gifts, rank leaders, calculate the final price, and identify the user entitled to purchase under Bimo Bond's rules and policies. |
| Inputs / sources | `auctionUpdated`, `liveGiftCombo`, `liveEnded` (NestJS fan-out) |
| Outputs | `auction.leaderboard.updated`, `auction.disputed`, `auction.settled` |
| Tools available | Durable ledger store (Redis-backed), `foundation.auction_rules` (deterministic), NestJS `BackendClient` HTTP bridge |
| Permission scopes | `auction.read`, `auction.audit.write` |
| Subscribed topics | `auctionUpdated`, `liveGiftCombo`, `liveEnded` |
| Published topics | `auction.leaderboard.updated`, `auction.disputed`, `auction.settled` |
| Failure cases | `winner_mismatch_vs_backend`, `duplicate_gift_event`, `malformed_auction_payload` |
| Accountable owner | platform-commerce |
| Acceptance criteria | Every contribution ledgered exactly once (dedup on `event_id`); winner cross-check runs on every completion; no single component (this agent included) is the sole source of truth for the payout decision |
| Automatic actions | Ledger contribution, recompute leaderboard, cross-check winner on completion |
| Human review required | Explicit-tie resolution, winner-mismatch resolution |

## Important design note — who decides the winner?

**The NestJS backend is the system of record.** `gifts.service.ts`
already assigns `winnerId` inside a DB transaction the moment
`currentTotalCoins` crosses `targetPriceCoins`. This agent does **not**
duplicate or override that decision.

What it adds instead:

1. An **independent ledger** of every gift contribution it observes,
   replayed through `foundation.auction_rules.resolve_purchase_eligibility`
   (deterministic — no LLM, no agent-side auction math the backend
   doesn't also do).
2. A **cross-check** on every `auctionUpdated` with `status=COMPLETED`:
   does this agent's independently-computed winner match the backend's
   reported `winnerId`? A mismatch publishes `auction.disputed` and
   writes an `auction.winner_mismatch` audit record — exactly the
   auditable discrepancy signal a dispute/fraud review needs (project
   doc AG-08a).
3. A **durable leaderboard** per auction, queryable independent of the
   backend's DB.

A mismatch does not necessarily mean the backend is wrong — this
agent's ledger can be incomplete if it started listening mid-auction.
Treat `auction.disputed` as "needs human review," not "backend is broken."

## Environment variables

See `.env.example` for the full list. The ones you must set per environment:

| Variable | Where the matching value lives | Notes |
|---|---|---|
| `EVENT_BUS_URL` | Same Redis the NestJS backend uses (`backend/.env` → `REDIS_URL`) | `redis://<host>:<port>/<db>`. Use `memory://local` for offline/unit-test runs. |
| `BACKEND_BASE_URL` | The running NestJS backend's base URL | e.g. `http://localhost:3000` |
| `BACKEND_API_TOKEN` | Must equal `backend/.env` → `AGENT_API_TOKEN` | Shared secret for the agent→Nest HTTP bridge. |
| `AUCTION_TIE_BREAK_POLICY` | Product decision (project doc PD-03) | `earliest` (auto-resolve) or `explicit` (flag for human review) — see `foundation/auction_rules.py`. |

The backend also needs `AGENT_EVENTS_ENABLED=true` for NestJS to fan
`auctionUpdated` onto the shared Redis Stream this agent consumes from.

## Run locally

```bash
pip install -e ../../foundation
pip install -r requirements.txt
cp .env.example .env
python -m src.live_auction_agent.main
```

Health/contract/metrics/audit/acceptance endpoints on `HEALTH_PORT`
(default 8085): `GET /health`, `/contract`, `/metrics`, `/audit`, `/acceptance`.

## Test

```bash
pytest tests/
```
