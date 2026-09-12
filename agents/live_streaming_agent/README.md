# Live Streaming Agent

Monitor stream quality and stability, detect interruptions, manage reconnection attempts, and track performance indicators.

Generated from the Bimo Bond agent cookiecutter template — see
`foundation/README.md` for what's shared vs. agent-specific.

## Agent contract (fill in before implementation — see project doc, "Define a contract for each agent")

| Field | Value |
|---|---|
| Objective | Monitor stream quality and stability, detect interruptions, manage reconnection attempts, and track performance indicators. |
| Inputs / sources | TODO |
| Outputs | TODO |
| Tools available | TODO |
| Permission scopes | stream.session.read,stream.session.write |
| Subscribed topics | stream.started,stream.heartbeat,stream.interrupted |
| Published topics | TODO |
| Failure cases | TODO |
| Accountable owner | TODO |
| Acceptance criteria | TODO |
| Automatic vs. human-review actions | TODO |

## Run locally

```bash
pip install -e ../../foundation
pip install -r requirements.txt
cp .env.example .env
python -m src.live_streaming_agent.main
```

## Test

```bash
pytest tests/
```