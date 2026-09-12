# cookiecutter-agent

Template used to scaffold new agents into `../agents/`.

Prefer:

```bash
# from repo root (no cookiecutter/jinja2 install required)
python generate_agent.py \
  --agent-name "Content Moderation Agent" \
  --agent-objective "Help detect prohibited content." \
  --subscribed-topics "content.uploaded,liveComment" \
  --permission-scopes "content.moderation.read,content.moderation.flag"
```

Or the real Cookiecutter CLI (after `pip install cookiecutter`):

```bash
cookiecutter cookiecutter-agent/
# then move the generated folder into agents/ if needed
```

## What the template produces

```
agents/<agent_slug>/
├── Dockerfile
├── README.md
├── requirements.txt      # -e ../../foundation
├── .env.example
├── pytest.ini            # local: pythonpath = src
├── src/<agent_slug>/
│   ├── __init__.py
│   ├── agent.py          # BaseAgent + AgentContract stub
│   ├── config.py
│   └── main.py
└── tests/test_agent.py
```

## After generating

1. Implement `handle_event()` and fill the contract TODOs.
2. Add a Compose service (copy an existing block in `docker-compose.yml`,
   change slug + host port).
3. Root `pytest.ini` is updated automatically by `generate_agent.py`
   (pythonpath + testpaths). If you used the Cookiecutter CLI by hand,
   add those lines yourself.
