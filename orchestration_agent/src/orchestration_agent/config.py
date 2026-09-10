"""Agent-specific config: fills in the topics/scopes this agent needs
on top of the common AgentSettings fields from foundation.config."""
from foundation import AgentSettings

SETTINGS = AgentSettings.from_env(
    agent_name="orchestration_agent",
    subscribed_topics=tuple("camera.feature.rejected,stream.ended,gift.effect.skipped,task.requested".split(",")),
    permission_scopes=tuple("orchestration.route.dispatch".split(",")),
)