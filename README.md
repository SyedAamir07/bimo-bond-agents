# Bimo Bond — Agent Foundation Sample

Sample implementation of the "foundation" layer discussed for the Bimo
Bond agent architecture, plus a cookiecutter-style template and sample
agents (Camera, Live Streaming, Gift Effects, Orchestration).

This is a **starting skeleton**, not a production system: agent business
logic is still stubbed where it would call real services (camera SDK,
streaming infra, overlay rendering). The **connectivity layer** is now
wired for a shared Redis Streams bus, permissions, contracts, NestJS
fan-out, HTTP backend client, and health/metrics endpoints.

## Layout

```
bimo-bond-agents/
├── foundation/              # shared library every agent depends on
├── cookiecutter-agent/      # template for scaffolding NEW agents
├── generate_agent.py
├── camera_agent/
├── live_streaming_agent/
├── gift_effects_agent/
├── orchestration_agent/
└── docker-compose.yml       # redis + all agents (shared EVENT_BUS_URL)
```

## Connectivity stage (do this before more agents)

1. Contracts / permissions / event exchange (foundation) — **done**
2. Shared Redis Streams (`agent:events`) across Compose agents — **done**
3. NestJS fan-out (`AGENT_EVENTS_ENABLED=true`) for pilot emits
   (`liveGiftCombo`, `liveEnded`, `liveComment`) — **done**
4. Pilot E2E streaming + gifting against real backends — next
5. Remaining agents — after contracts + bus prove out

### Event names from NestJS

Nest keeps Socket.IO event names as `event_type` on the stream (no rename):

| Nest emit | Stream `event_type` | Sample subscriber |
|---|---|---|
| `liveGiftCombo` | `liveGiftCombo` | gift_effects_agent |
| `liveEnded` | `liveEnded` | live_streaming_agent |
| `liveComment` | `liveComment` | (subscribe when needed) |

Agents may also use internal topics (`gift.sent`, `stream.started`, …)
for orchestration-driven flows.

## Generate a new agent

```bash
python generate_agent.py \
  --agent-name "Content Moderation Agent" \
  --agent-objective "Help detect prohibited content and inappropriate behavior." \
  --subscribed-topics "content.uploaded,stream.frame.sampled" \
  --permission-scopes "content.moderation.read,content.moderation.flag"
```

## Run locally (Compose — shared Redis)

```bash
docker compose up --build
```

Health / contract / metrics:

- Camera: http://localhost:8081/health
- Live: http://localhost:8082/contract
- Gifts: http://localhost:8083/metrics
- Orchestration: http://localhost:8084/health

## Foundation tests (offline, memory bus)

```bash
pip install -e "./foundation[dev]"
cd foundation && pytest
```

## NestJS bridge

In the backend `.env`:

```bash
REDIS_URL=redis://localhost:6379
AGENT_EVENTS_ENABLED=true
AGENT_EVENTS_STREAM=agent:events
```

When enabled, `EventsGateway` fan-outs pilot live events onto the same
Redis Stream agents consume.

## What's still open

- Auth token issuance for `BACKEND_API_TOKEN` (agents calling Nest APIs)
- Durable orchestration task store (still in-memory in orchestration agent)
- Kafka/Rabbit adapters (not needed while Redis Streams is the channel)
- Product decisions: auction rules, content policies, camera feature list approval
