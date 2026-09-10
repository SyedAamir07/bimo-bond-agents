# {{cookiecutter.agent_name}}

{{cookiecutter.agent_objective}}

Generated from the Bimo Bond agent cookiecutter template — see
`foundation/README.md` for what's shared vs. agent-specific.

## Agent contract

Machine-readable contract lives on the agent class as `contract = AgentContract(...)`
and is served at `GET /contract`. Keep the README table in sync while filling fields:

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

```bash
pip install -e ../foundation
pip install -r requirements.txt
cp .env.example .env
python -m src.{{cookiecutter.agent_slug}}.main
```

## Test

```bash
pytest tests/
```
