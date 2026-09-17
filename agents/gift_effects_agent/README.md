# Gift Effects Agent

Run and coordinate gift effects and animations, linking them to events
in Bimo Bond's gifting system (project doc requirement **AG-04**).

Deterministic workflow, not an LLM agent — which animation plays is
data already on the event (the backend attaches the gift catalog
object to every payload), not a model decision.

## Agent contract

| Field | Value |
|---|---|
| Objective | Run and coordinate gift effects and animations, linking them to events in Bimo Bond's gifting system. |
| Inputs / sources | `gift.sent`, `liveGiftCombo` (NestJS fan-out) |
| Outputs | `gift.effect.triggered`, `gift.effect.skipped` |
| Tools available | Durable trigger store (Redis-backed), NestJS `BackendClient` HTTP bridge |
| Permission scopes | `gift.catalog.read`, `stream.overlay.write` |
| Subscribed topics | `gift.sent`, `liveGiftCombo` |
| Published topics | `gift.effect.triggered`, `gift.effect.skipped` |
| Failure cases | `no_animation_on_payload`, `overlay_unavailable`, `duplicate_event_redelivered` |
| Accountable owner | platform-live |
| Acceptance criteria | `gift_effect_latency_ms`, `gift_duplicate_or_missing_rate` (see `foundation/acceptance.py` for baseline/target); exactly one trigger per `event_id`, even across a restart |
| Automatic actions | Trigger effect from the payload's own gift catalog data, skip on missing animation |
| Human review required | Approving new entries in the gift catalog (not owned by this agent) |

## How it works

- **No hardcoded gift→effect map.** The backend already attaches the
  full gift catalog object (`name`, `animationUrl`, `audioUrl`, `color`,
  ...) to every `gift.sent` / `liveGiftCombo` payload
  (`giftPresentationPayload` in `gifts.service.ts`). This agent reads
  `gift.animationUrl` directly instead of maintaining its own mapping
  table that could drift from the real catalog.
- **One trigger per event, not per combo.** Each tap in a combo burst
  is its own event with its own `transactionId` (the backend creates
  one `GiftTransaction` row per send call) and is meant to trigger its
  own animation — the `combo` count on the payload is a rendering hint
  for the overlay (e.g. "x3 combo!"), not a signal to suppress repeated
  effects.
- **Durable dedup on `event_id`.** `BaseAgent`'s built-in dedup is an
  in-memory LRU that forgets on restart. This agent adds a durable
  `TriggerStore` (Redis-backed in production) keyed on `event_id`, so a
  redelivered event after a restart is still recognized as
  already-handled instead of firing a duplicate animation.

## Environment variables

See `.env.example` for the full list. The ones you must set per environment:

| Variable | Where the matching value lives | Notes |
|---|---|---|
| `EVENT_BUS_URL` | Same Redis the NestJS backend uses (`backend/.env` → `REDIS_URL`) | `redis://<host>:<port>/<db>`. Use `memory://local` for offline/unit-test runs. |
| `BACKEND_BASE_URL` | The running NestJS backend's base URL | e.g. `http://localhost:3000` |
| `BACKEND_API_TOKEN` | Must equal `backend/.env` → `AGENT_API_TOKEN` | Shared secret for the agent→Nest HTTP bridge. |

The backend also needs `AGENT_EVENTS_ENABLED=true` for NestJS to fan
`liveGiftCombo` onto the shared Redis Stream this agent consumes from.

## Run locally

```bash
pip install -e ../../foundation
pip install -r requirements.txt
cp .env.example .env
python -m src.gift_effects_agent.main
```

Health/contract/metrics/audit/acceptance endpoints on `HEALTH_PORT`
(default 8083): `GET /health`, `/contract`, `/metrics`, `/audit`, `/acceptance`.

## Test

```bash
pytest tests/
```
