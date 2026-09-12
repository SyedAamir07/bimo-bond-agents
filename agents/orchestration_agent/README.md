# Orchestration Agent

Coordinate events and tasks across agents, track execution status, and manage timeouts, retries, and duplicate execution prevention.

Generated from the Bimo Bond agent cookiecutter template — see
`foundation/README.md` for what's shared vs. agent-specific.

## Agent contract (fill in before implementation — see project doc, "Define a contract for each agent")

| Field | Value |
|---|---|
| Objective | Coordinate events and tasks across agents, track execution status, and manage timeouts, retries, and duplicate execution prevention. |
| Inputs / sources | TODO |
| Outputs | TODO |
| Tools available | TODO |
| Permission scopes | orchestration.route.dispatch |
| Subscribed topics | camera.feature.rejected,stream.ended,gift.effect.skipped,task.requested |
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
python -m src.orchestration_agent.main
```

## Test

```bash
pytest tests/
```