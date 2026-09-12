"""Agent-specific config: fills in the topics/scopes this agent needs
on top of the common AgentSettings fields from foundation.config."""
from foundation import AgentSettings


def _csv_tuple(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


SETTINGS = AgentSettings.from_env(
    agent_name="{{cookiecutter.agent_slug}}",
    subscribed_topics=_csv_tuple("{{cookiecutter.subscribed_topics}}"),
    permission_scopes=_csv_tuple("{{cookiecutter.permission_scopes}}"),
)
