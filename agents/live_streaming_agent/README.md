# Live Streaming Agent

Monitor stream quality and stability, detect interruptions, manage
reconnection attempts, and track performance indicators (project doc
requirement **AG-02**).

Deterministic workflow, not an LLM agent — reconnect policy and
staleness detection are explicit auditable rules, per the project's
architectural guidance (Anthropic, "Building effective agents").

## Agent contract

| Field | Value |
|---|---|
| Objective | Monitor stream quality and stability, detect interruptions, manage reconnection attempts, and track performance indicators. |
| Inputs / sources | `stream.started`, `stream.heartbeat`, `stream.interrupted`, `liveEnded` (NestJS fan-out) |
| Outputs | `stream.reconnect.attempted`, `stream.ended`, `stream.monitor.armed` |
| Tools available | Durable session store (Redis-backed), NestJS `BackendClient` HTTP bridge |
| Permission scopes | `stream.session.read`, `stream.session.write` |
| Subscribed topics | `stream.started`, `stream.heartbeat`, `stream.interrupted`, `stream.reconnect.attempted`, `liveEnded` |
| Published topics | `stream.reconnect.attempted`, `stream.ended`, `stream.monitor.armed` |
| Failure cases | `reconnect_attempts_exhausted`, `heartbeat_stale_timeout`, `infra_unavailable` |
| Accountable owner | platform-live |
| Acceptance criteria | `stream_startup_ms`, `stream_interruption_rate`, `stream_reconnect_success_rate` (see `foundation/acceptance.py` for baseline/target); session state survives an agent restart |
| Automatic actions | Arm monitor on start, reconnect within policy, end session on reconnect-exhausted or heartbeat-stale timeout |
| Human review required | Raising `MAX_RECONNECT_ATTEMPTS` above its default |

## How it works

- **Session key**: accepts either `session_id` (internal `stream.*`
  events) or `liveId` (NestJS `liveEnded` fan-out) so it works against
  both event sources without callers needing to know which one applies.
- **Durable state**: session records (state, reconnect attempts, last
  heartbeat) live in a `SessionStore` backed by `foundation.task_store`
  — Redis in production, in-memory for tests — so a restart doesn't
  reset every session to zero.
- **Stale-session watchdog**: a background sweep (`check_stale_sessions`,
  driven from `main.py` every `STALE_SWEEP_INTERVAL_SECONDS`) catches
  sessions whose client went silent without ever sending
  `stream.interrupted` or `liveEnded` — a hard crash has no chance to
  signal either.
- **Reconnect policy**: capped at `MAX_RECONNECT_ATTEMPTS` (default 3);
  every attempt and every session-end is written to the audit log with
  a reason.

## Environment variables

See `.env.example` for the full list. The ones you must set per environment:

| Variable | Where the matching value lives | Notes |
|---|---|---|
| `EVENT_BUS_URL` | Same Redis the NestJS backend uses (`backend/.env` → `REDIS_URL`) | `redis://<host>:<port>/<db>`. Use `memory://local` for offline/unit-test runs. |
| `BACKEND_BASE_URL` | The running NestJS backend's base URL | e.g. `http://localhost:3000` |
| `BACKEND_API_TOKEN` | Must equal `backend/.env` → `AGENT_API_TOKEN` | Shared secret for the agent→Nest HTTP bridge (`GET /agent/ping`). Not currently set in `backend/.env` — add it on both sides with the same value. |

The backend also needs `AGENT_EVENTS_ENABLED=true` (currently `false` in
`backend/.env`) for NestJS to fan `liveEnded` / `liveGiftCombo` / etc.
onto the shared Redis Stream this agent consumes from.

## Run locally

```bash
pip install -e ../../foundation
pip install -r requirements.txt
cp .env.example .env
python -m src.live_streaming_agent.main
```

Health/contract/metrics/audit/acceptance endpoints on `HEALTH_PORT`
(default 8082): `GET /health`, `/contract`, `/metrics`, `/audit`, `/acceptance`.

## Test

```bash
pytest tests/
```
