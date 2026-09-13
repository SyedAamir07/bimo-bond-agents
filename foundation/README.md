# foundation

Shared library every Bimo Bond agent depends on. This is **not** an
agent itself — it is the "standardized channel" layer described in the
technical foundation document.

## Install (editable, for local multi-agent dev)

```bash
pip install -e "./foundation[dev]"
```

## Connectivity stage (what this package provides)

1. **Contracts** — `AgentContract` + `EventEnvelope` (+ light payload key checks)
2. **Event catalog** — `event_catalog.py` (Nest fan-out + internal topics)
3. **Permissions** — `assert_scope` / inbound `required_scope` in `BaseAgent`
4. **Shared Redis Streams bus** — `RedisStreamsEventBus` with approximate
   `MAXLEN`, per-group delivery counts, and DLQ (`agent:events:dlq`)
5. **NestJS fan-out** — backend `AgentEventsPublisher` (same envelope shape)
6. **HTTP bridge + auth** — `BackendClient` (`BACKEND_BASE_URL` /
   `BACKEND_API_TOKEN` matching Nest `AGENT_API_TOKEN` + `GET /agent/ping`)
7. **Observability** — `/health` (fresh `checked_at`), `/contract`, `/metrics`,
   `/audit`, `/acceptance`
8. **Dedup + retry + retention drop** — LRU dedup, `RetryPolicy`, expired
   envelopes dropped via retention policy
9. **Auction rules helpers** — deterministic eligibility (not LLM)

Local unit tests still use `memory://` (no Redis required).

```bash
cd foundation && pip install -e ".[dev]" && pytest
```

## What lives here vs. what lives in each agent

| Belongs in `foundation/` | Belongs in each agent |
|---|---|
| Event envelope + AgentContract shape | Contract field values |
| Event bus client (memory + Redis Streams) | Which events it emits/consumes |
| Permission enforcement | Its permission scope list |
| Backend HTTP client | Which APIs it calls |
| Health / metrics / logging | Business logic in `handle_event()` |
| Dedup + handler retry + DLQ knobs | Orchestration routing table |

If you find yourself copy-pasting code between two agents, it probably
belongs here instead.
