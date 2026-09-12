"""Agent-specific config: fills in the topics/scopes this agent needs
on top of the common AgentSettings fields from foundation.config."""
from foundation import AgentSettings

SETTINGS = AgentSettings.from_env(
    agent_name="camera_agent",
    subscribed_topics=tuple("camera.settings.requested,camera.feature.toggled".split(",")),
    permission_scopes=tuple("device.camera.read,device.camera.write".split(",")),
)