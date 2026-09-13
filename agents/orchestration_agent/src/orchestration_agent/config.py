"""Agent-specific config: fills in the topics/scopes this agent needs
on top of the common AgentSettings fields from foundation.config."""
from foundation import AgentSettings

SETTINGS = AgentSettings.from_env(
    agent_name="orchestration_agent",
    subscribed_topics=(
        "camera.feature.rejected",
        "camera.feature.applied",
        "stream.ended",
        "stream.monitor.armed",
        "gift.effect.skipped",
        "gift.effect.triggered",
        "task.requested",
    ),
    permission_scopes=("orchestration.route.dispatch",),
)
