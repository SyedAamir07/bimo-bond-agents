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
2. **Permissions** — `assert_scope` / inbound `required_scope` enforcement in `BaseAgent`
3. **Shared Redis Streams bus** — `EVENT_BUS_URL=redis://...` → `RedisStreamsEventBus` on stream `agent:events`
4. **NestJS fan-out** — backend `AgentEventsPublisher` XADDs the same envelope shape (flag: `AGENT_EVENTS_ENABLED`)
5. **HTTP bridge** — `BackendClient` via `BACKEND_BASE_URL` / `BACKEND_API_TOKEN`
6. **Observability** — `GET /health`, `GET /contract`, `GET /metrics` on `HEALTH_PORT`
7. **Dedup + retry** — bounded event-id LRU + `RetryPolicy` in `BaseAgent`

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
| Dedup + handler retry | Orchestration routing table |

If you find yourself copy-pasting code between two agents, it probably
belongs here instead.
