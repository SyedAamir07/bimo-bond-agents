# {{cookiecutter.agent_name}}

{{cookiecutter.agent_objective}}

Generated from the Bimo Bond agent cookiecutter template — see
`../../foundation/README.md` for what's shared vs. agent-specific.

## Agent contract

Machine-readable contract lives on the agent class as `contract = AgentContract(...)`
and is served at `GET /contract`. Keep this table in sync while filling fields:

| Field | Value |
|---|---|
| Objective | {{cookiecutter.agent_objective}} |
| Inputs / sources | TODO (also set on AgentContract.inputs) |
| Outputs | TODO |
| Tools available | TODO |
| Permission scopes | {{cookiecutter.permission_scopes}} |
| Subscribed topics | {{cookiecutter.subscribed_topics}} |
| Published topics | TODO |
| Failure cases | TODO |
| Accountable owner | {{cookiecutter.author}} |
| Acceptance criteria | TODO |
| Automatic vs. human-review actions | TODO |

Env for connectivity:

```bash
EVENT_BUS_URL=redis://localhost:6379/0
AGENT_EVENTS_STREAM=agent:events
BACKEND_BASE_URL=
BACKEND_API_TOKEN=
```

## Run locally

From this agent folder:

```bash
pip install -e ../../foundation
pip install -r requirements.txt
cp .env.example .env
python -m src.{{cookiecutter.agent_slug}}.main
```

Ops endpoints (default `HEALTH_PORT=8080`):

- `GET /health`
- `GET /contract`
- `GET /metrics`
- `GET /audit`
- `GET /acceptance`

## Test

From this folder:

```bash
pytest
```

From repo root (after `generate_agent.py` wired pytest.ini):

```bash
pytest agents/{{cookiecutter.agent_slug}}/tests
```

## Docker Compose

Add a service to the repo-root `docker-compose.yml` (pick a free host port):

```yaml
  {{cookiecutter.agent_slug}}:
    build:
      context: .
      dockerfile: agents/{{cookiecutter.agent_slug}}/Dockerfile
    env_file: agents/{{cookiecutter.agent_slug}}/.env.example
    environment:
      EVENT_BUS_URL: redis://redis:6379/0
      AGENT_EVENTS_STREAM: agent:events
    ports:
      - "8085:8080"
    depends_on:
      redis:
        condition: service_healthy
```
