# Orchestration Agent

Coordinate events and tasks across agents, track execution status, and
manage timeouts, retries, and duplicate execution prevention
(project doc requirement **AG-10a**, the proposed 10th agent).

> **Product gate (AG-10):** the project doc explicitly lists this agent
> as "proposed, subject to scope approval before implementation." This
> code exists as the foundation's routing/reliability reference
> implementation; get explicit product sign-off before running it
> against production traffic. See the project doc's Phase 0 product
> gates (PD-01).

## Agent contract

| Field | Value |
|---|---|
| Objective | Coordinate events and tasks across agents, track execution status, and manage timeouts, retries, and duplicate execution prevention. |
| Inputs / sources | `task.requested`, plus each target agent's own success/failure events: `camera.feature.rejected`/`camera.feature.applied`, `stream.ended`/`stream.monitor.armed`, `gift.effect.skipped`/`gift.effect.triggered` |
| Outputs | `camera.feature.toggled`, `gift.sent`, `stream.started`, `task.failed` |
| Tools available | `ROUTING_TABLE` (static, code-reviewed), durable task store (Redis-backed) |
| Permission scopes | `orchestration.route.dispatch` |
| Subscribed topics | `task.requested`, `camera.feature.rejected`, `camera.feature.applied`, `stream.ended`, `stream.monitor.armed`, `gift.effect.skipped`, `gift.effect.triggered` |
| Published topics | `camera.feature.toggled`, `gift.sent`, `stream.started`, `task.failed` |
| Failure cases | `unknown_action` (no route for the requested action), `timeout` (dispatched but never confirmed), `max_retries` (retried `MAX_RETRIES` times, still failing) |
| Accountable owner | platform-platform |
| Acceptance criteria | No duplicate `task_id` ever dispatched twice; timed-out tasks are retried then failed; task state survives an orchestrator restart; every dispatched route stays within the scope declared in `ROUTING_TABLE` — never broader, even if the request asks for more |
| Automatic actions | Route a requested task, retry on target failure, sweep for timeouts, mark completed on target success |
| Human review required | Adding a new entry to `ROUTING_TABLE` (new action → target agent mapping) |

## How it works

- **Routing table is the single source of truth.** `ROUTING_TABLE` in
  `agent.py` is the only place an `action` string maps to a target
  agent, a dispatch event, and the permission scope that dispatch is
  allowed to carry. The orchestrator never invents a route at runtime
  and never grants a dispatched event a scope beyond what the table
  says for that action — per the project doc: "route tasks within
  approved permissions... without... granting itself additional access."
- **Durable task state.** Task records (status, attempts, dispatch
  time) live in a `TaskStore` backed by `foundation.task_store` — Redis
  in production, in-memory for tests — so a restart doesn't lose track
  of in-flight tasks or accidentally re-dispatch one that was already
  running (duplicate execution prevention holds across restarts, not
  just within one process).
- **Success vs. failure is not always a fixed event type.** Most target
  events are unconditionally success or failure
  (`camera.feature.applied` = success, `gift.effect.skipped` =
  failure). `stream.ended` is the exception: a normal end-of-session
  (`reason: live_ended`) is not a task failure, but
  `reason: reconnect_attempts_exhausted` or `heartbeat_stale_timeout`
  is. `_is_stream_ended_failure()` inspects the payload instead of
  trusting the event type alone.
- **Timeout sweep.** `check_timeouts()` is driven from `main.py` on a
  background thread every `TIMEOUT_SWEEP_SECONDS` (5s) — catches tasks
  that were dispatched but never confirmed complete or failed (a target
  agent that silently dropped the event, for example). Reads from the
  durable store, so a sweep right after a restart still finds tasks
  dispatched before the crash.
- **Not every agent has a route yet.** `live_auction_agent` is
  intentionally not in `ROUTING_TABLE` — it's an event observer/audit
  cross-checker today (subscribes to `auctionUpdated` directly), not a
  target for dispatched tasks. Add a route only when a concrete
  `task.requested` action needs to reach it.

## Environment variables

See `.env.example` for the full list. The ones you must set per environment:

| Variable | Where the matching value lives | Notes |
|---|---|---|
| `EVENT_BUS_URL` | Same Redis every other agent and the NestJS backend use (`backend/.env` → `REDIS_URL`) | `redis://<host>:<port>/<db>`. Use `memory://local` for offline/unit-test runs. |
| `BACKEND_BASE_URL` | The running NestJS backend's base URL | e.g. `http://localhost:3000` |
| `BACKEND_API_TOKEN` | Must equal `backend/.env` → `AGENT_API_TOKEN` | Shared secret for the agent→Nest HTTP bridge. |

## Run locally

```bash
pip install -e ../../foundation
pip install -r requirements.txt
cp .env.example .env
python -m src.orchestration_agent.main
```

Health/contract/metrics/audit/acceptance endpoints on `HEALTH_PORT`
(default 8084): `GET /health`, `/contract`, `/metrics`, `/audit`, `/acceptance`.

## Test

```bash
pytest tests/
```
