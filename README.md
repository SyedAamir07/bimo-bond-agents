# Bimo Bond — Agent Foundation Sample

Sample implementation of the "foundation" layer discussed for the Bimo
Bond agent architecture, plus a cookiecutter-style template and sample
agents (Camera, Live Streaming, Gift Effects, Orchestration).

This is a **starting skeleton**, not a production system: agent business
logic is still stubbed where it would call real services (camera SDK,
streaming infra, overlay rendering). The **connectivity layer** is wired
for a shared Redis Streams bus (with DLQ + maxlen), permissions,
contracts, NestJS fan-out, HTTP backend client + agent token, and
health/metrics endpoints.

## Layout

```
bimo-bond-agents/
├── foundation/              # shared library every agent depends on
├── cookiecutter-agent/      # template for scaffolding NEW agents
├── generate_agent.py        # writes new agents + wires Compose/pytest
├── agents/                  # all concrete agents
│   ├── camera_agent/
│   ├── live_streaming_agent/
│   ├── gift_effects_agent/
│   └── orchestration_agent/
└── docker-compose.yml       # redis + all agents (shared EVENT_BUS_URL)
```

## Connectivity stage

1. Contracts / permissions / event exchange (foundation) — **done**
2. Shared Redis Streams (`agent:events`) + DLQ / maxlen — **done**
3. NestJS fan-out (`AGENT_EVENTS_ENABLED=true`) for pilot emits — **done**
4. Agent HTTP auth (`AGENT_API_TOKEN` ↔ `BACKEND_API_TOKEN`) — **done**
5. Auto Compose + pytest wiring on `generate_agent.py` — **done**
6. Pilot E2E streaming + gifting against real backends — **next**
7. Remaining agents — after pilot proves out

## NestJS bridge

Backend `.env`:

```bash
REDIS_URL=redis://localhost:6379
AGENT_EVENTS_ENABLED=true
AGENT_EVENTS_STREAM=agent:events
AGENT_STREAM_MAXLEN=100000
AGENT_API_TOKEN=change-me-agent-token
```

Each agent:

```bash
BACKEND_BASE_URL=http://localhost:3000
BACKEND_API_TOKEN=change-me-agent-token
```

Catalog: `../backend/docs/agents/event-catalog.md` and
`foundation/event_catalog.py`. Connectivity: `GET /agent/ping`.

| Nest emit | Sample subscriber |
|---|---|
| `liveGiftCombo` | gift_effects_agent |
| `liveGift` | (optional) |
| `liveEnded` | live_streaming_agent |
| `liveComment` | (subscribe when needed) |
| `liveModeration` | future moderation |
| `auctionUpdated` | future auction |

## Generate a new agent

`pytest.ini` and `docker-compose.yml` are wired automatically:

```bash
python generate_agent.py \
  --agent-name "Content Moderation Agent" \
  --agent-objective "Help detect prohibited content and inappropriate behavior." \
  --subscribed-topics "content.uploaded,liveModeration" \
  --permission-scopes "content.moderation.read,content.moderation.flag"
```

Flags: `--no-compose-wire` / `--no-pytest-wire`.

Then implement `handle_event()` and fill contract TODOs.

## Run locally

```bash
docker compose up --build
```

- Camera: http://localhost:8081/health
- Live: http://localhost:8082/contract
- Gifts: http://localhost:8083/metrics
- Orchestration: http://localhost:8084/health

Redis ops env (defaults): `AGENT_STREAM_MAXLEN`, `AGENT_MAX_DELIVERIES`,
`AGENT_EVENTS_DLQ_STREAM` (`agent:events:dlq`).

## Tests

```bash
pip install -e "./foundation[dev]"
pytest
```

## What's still open

- Durable orchestration task store (still in-memory)
- Kafka/Rabbit adapters (not needed while Redis Streams is the channel)
- Product decisions: auction rules, content policies, camera feature list
- Real SDK / LiveKit / overlay integrations inside sample agents
