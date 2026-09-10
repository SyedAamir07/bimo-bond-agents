# Gift Effects Agent

Run and coordinate gift effects and animations, linking them to events in Bimo Bond's gifting system.

Generated from the Bimo Bond agent cookiecutter template — see
`foundation/README.md` for what's shared vs. agent-specific.

## Agent contract (fill in before implementation — see project doc, "Define a contract for each agent")

| Field | Value |
|---|---|
| Objective | Run and coordinate gift effects and animations, linking them to events in Bimo Bond's gifting system. |
| Inputs / sources | TODO |
| Outputs | TODO |
| Tools available | TODO |
| Permission scopes | gift.catalog.read,stream.overlay.write |
| Subscribed topics | gift.sent |
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
python -m src.gift_effects_agent.main
```

## Test

```bash
pytest tests/
```