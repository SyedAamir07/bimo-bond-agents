"""Agent-specific config: fills in the topics/scopes this agent needs
on top of the common AgentSettings fields from foundation.config."""
from foundation import AgentSettings

SETTINGS = AgentSettings.from_env(
    agent_name="gift_effects_agent",
    subscribed_topics=tuple("gift.sent,liveGiftCombo".split(",")),
    permission_scopes=tuple("gift.catalog.read,stream.overlay.write".split(",")),
)
