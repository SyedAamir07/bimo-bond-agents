# agents/

All concrete Bimo Bond agents live here. Shared plumbing stays in
`../foundation/`; new agents are scaffolded into this folder via
`../generate_agent.py`.

## Current agents

| Folder | Role |
|---|---|
| `camera_agent/` | Camera features / quality (catalog-driven approval) |
| `live_streaming_agent/` | Stream stability / reconnect |
| `gift_effects_agent/` | Gift overlay effects |
| `live_auction_agent/` | Gift ledger + independent winner cross-check |
| `orchestration_agent/` | Cross-agent task routing |

## Add a new agent

From the repo root:

```bash
python generate_agent.py \
  --agent-name "Content Moderation Agent" \
  --agent-objective "Help detect prohibited content." \
  --subscribed-topics "content.uploaded,liveComment" \
  --permission-scopes "content.moderation.read"
```

Then wire it in `../docker-compose.yml` (build + port) the same way as
the sample agents.
