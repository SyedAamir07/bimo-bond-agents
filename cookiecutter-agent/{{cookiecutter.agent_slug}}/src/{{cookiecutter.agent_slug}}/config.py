"""Agent-specific config: fills in the topics/scopes this agent needs
on top of the common AgentSettings fields from foundation.config."""
from foundation import AgentSettings

SETTINGS = AgentSettings.from_env(
    agent_name="{{cookiecutter.agent_slug}}",
    subscribed_topics=tuple("{{cookiecutter.subscribed_topics}}".split(",")),
    permission_scopes=tuple("{{cookiecutter.permission_scopes}}".split(",")),
)
