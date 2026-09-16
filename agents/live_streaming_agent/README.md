# Live Streaming Agent

Monitor stream quality and stability, detect interruptions, manage
reconnection attempts, and track performance indicators (project doc
requirement **AG-02**).

Deterministic workflow, not an LLM agent — reconnect policy and
staleness detection are explicit auditable rules, per the project's
architectural guidance (Anthropic, "Building effective agents"). An
ML model runs alongside those rules as an **advisory early-warning
layer** — see below.

## Agent contract

| Field | Value |
|---|---|
| Objective | Monitor stream quality and stability, detect interruptions, manage reconnection attempts, and track performance indicators, with an ML anomaly-detection model providing advisory early-warning signals. |
| Inputs / sources | `stream.started`, `stream.heartbeat`, `stream.interrupted`, `liveEnded` (NestJS fan-out) |
| Outputs | `stream.reconnect.attempted`, `stream.ended`, `stream.monitor.armed`, `stream.health.anomaly_detected` |
| Tools available | Durable session store (Redis-backed), NestJS `BackendClient` HTTP bridge, in-process `IsolationForest` anomaly model |
| Permission scopes | `stream.session.read`, `stream.session.write` |
| Subscribed topics | `stream.started`, `stream.heartbeat`, `stream.interrupted`, `stream.reconnect.attempted`, `liveEnded` |
| Published topics | `stream.reconnect.attempted`, `stream.ended`, `stream.monitor.armed`, `stream.health.anomaly_detected` |
| Failure cases | `reconnect_attempts_exhausted`, `heartbeat_stale_timeout`, `infra_unavailable`, `model_not_trained` (not an error — falls back to never flagging anomalies until enough data exists) |
| Accountable owner | platform-live |
| Acceptance criteria | `stream_startup_ms`, `stream_interruption_rate`, `stream_reconnect_success_rate` (see `foundation/acceptance.py` for baseline/target); session state survives an agent restart; anomaly score is **advisory only** — never solely ends a session or blocks a reconnect |
| Automatic actions | Arm monitor on start, reconnect within policy, end session on reconnect-exhausted or heartbeat-stale timeout, score every heartbeat for anomaly, retrain model on session completion |
| Human review required | Raising `MAX_RECONNECT_ATTEMPTS` above its default, tuning `ML_CONTAMINATION` |

## How it works

- **Session key**: accepts either `session_id` (internal `stream.*`
  events) or `liveId` (NestJS `liveEnded` fan-out) so it works against
  both event sources without callers needing to know which one applies.
- **Durable state**: session records (state, reconnect attempts, last
  heartbeat, heartbeat/interruption counts) live in a `SessionStore`
  backed by `foundation.task_store` — Redis in production, in-memory
  for tests — so a restart doesn't reset every session to zero.
- **Stale-session watchdog**: a background sweep (`check_stale_sessions`,
  driven from `main.py` every `STALE_SWEEP_INTERVAL_SECONDS`) catches
  sessions whose client went silent without ever sending
  `stream.interrupted` or `liveEnded` — a hard crash has no chance to
  signal either.
- **Reconnect policy**: capped at `MAX_RECONNECT_ATTEMPTS` (default 3);
  every attempt and every session-end is written to the audit log with
  a reason.

## ML anomaly detection (advisory layer)

Alongside the fixed reconnect/stale-timeout rules above, an
`sklearn.ensemble.IsolationForest` model (`anomaly_model.py`) scores
every heartbeat for how "normal" the session's behavior looks so far,
based on a **rate-based** feature vector (interruptions per heartbeat,
reconnect attempts per heartbeat, interruptions per minute, heartbeats
per minute) — deliberately not raw counts or elapsed time. Training
data comes from *completed* sessions, but predictions run on every
in-flight heartbeat; raw counts made a 1-heartbeat-old session look
anomalous purely for being early, regardless of whether it was
actually healthy. Rates stay roughly stable across a session's
lifetime, so an early heartbeat and a late one score comparably when
both are behaving normally.

- **Unsupervised, no labels needed** — trained on the feature vectors
  of sessions this agent has itself observed complete. There is no
  "this session was bad" ground truth to supply.
- **Training data**: only *completed* sessions (state `ENDED`) are
  used — an in-flight session doesn't yet have a stable feature vector.
- **Cold start / fallback**: below `ML_MIN_TRAINING_SAMPLES` (default
  30) completed sessions, the model is untrained and predictions
  return "never anomalous" rather than guessing — anomaly detection is
  silent until enough real traffic has been observed. See
  `anomaly_model.py`'s `predict()` docstring for the reasoning.
- **Retraining**: automatic every `ML_RETRAIN_INTERVAL_SAMPLES`
  (default 20) newly completed sessions once the minimum is reached.
- **Durable training samples**: the raw feature vectors (not the
  fitted model itself) are persisted via `TrainingSampleStore`
  (Redis-backed in production, same pattern as the session store) so
  an agent restart recovers accumulated history instead of climbing
  back to `ML_MIN_TRAINING_SAMPLES` from zero. If the recovered
  samples already clear that bar, the model fits immediately on
  startup rather than waiting for one more completed session.
- **Advisory only**: an anomaly detection publishes
  `stream.health.anomaly_detected` as an early-warning signal — it
  never ends the session or blocks/forces a reconnect. Those actions
  stay entirely on the deterministic rules above, which run identically
  whether or not the model is trained yet — same principle
  `live_auction_agent` follows for money-moving decisions (don't
  assign the final decision solely to a model).

## Environment variables

See `.env.example` for the full list. The ones you must set per environment:

| Variable | Where the matching value lives | Notes |
|---|---|---|
| `EVENT_BUS_URL` | Same Redis the NestJS backend uses (`backend/.env` → `REDIS_URL`) | `redis://<host>:<port>/<db>`. Use `memory://local` for offline/unit-test runs. |
| `BACKEND_BASE_URL` | The running NestJS backend's base URL | e.g. `http://localhost:3000` |
| `BACKEND_API_TOKEN` | Must equal `backend/.env` → `AGENT_API_TOKEN` | Shared secret for the agent→Nest HTTP bridge (`GET /agent/ping`). |
| `ML_MIN_TRAINING_SAMPLES` | Local tuning | Completed sessions needed before the model trains for the first time (default 30). |
| `ML_RETRAIN_INTERVAL_SAMPLES` | Local tuning | New completed sessions between retrains once trained (default 20). |
| `ML_CONTAMINATION` | Local tuning | `"auto"` (default, recommended) lets scikit-learn derive the anomaly threshold from the data's own spread; a fixed 0.0-0.5 fraction forces exactly that proportion flagged regardless of actual distribution -- see `config.py` for why that caused both false positives and false negatives in manual testing. |

The backend also needs `AGENT_EVENTS_ENABLED=true` for NestJS to fan
`liveEnded` / `liveGiftCombo` / etc. onto the shared Redis Stream this
agent consumes from.

## Run locally

```bash
pip install -e ../../foundation
pip install -r requirements.txt   # includes scikit-learn, numpy
cp .env.example .env
python -m src.live_streaming_agent.main
```

Health/contract/metrics/audit/acceptance endpoints on `HEALTH_PORT`
(default 8082): `GET /health`, `/contract`, `/metrics`, `/audit`, `/acceptance`.

## Test

```bash
pytest tests/
```
