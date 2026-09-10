# Camera Agent

Manage camera functions, improve image quality, and control approved in-app camera features.

Generated from the Bimo Bond agent cookiecutter template — see
`foundation/README.md` for what's shared vs. agent-specific.

## Agent contract (fill in before implementation — see project doc, "Define a contract for each agent")

| Field | Value |
|---|---|
| Objective | Manage camera functions, improve image quality, and control approved in-app camera features. |
| Inputs / sources | TODO |
| Outputs | TODO |
| Tools available | TODO |
| Permission scopes | device.camera.read,device.camera.write |
| Subscribed topics | camera.settings.requested,camera.feature.toggled |
| Published topics | TODO |
| Failure cases | TODO |
| Accountable owner | TODO |
| Acceptance criteria | TODO |
| Automatic vs. human-review actions | TODO |

## Run locally

```bash
pip install -e ../foundation
pip install -r requirements.txt
cp .env.example .env
python -m src.camera_agent.main
```

## Test

```bash
pytest tests/
```