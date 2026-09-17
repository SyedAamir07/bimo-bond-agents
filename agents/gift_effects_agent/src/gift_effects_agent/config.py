"""Agent-specific config: fills in the topics/scopes this agent needs
on top of the common AgentSettings fields from foundation.config."""
from __future__ import annotations

from foundation import AgentSettings

SETTINGS = AgentSettings.from_env(
    agent_name="gift_effects_agent",
    subscribed_topics=("gift.sent", "liveGiftCombo"),
    permission_scopes=("gift.catalog.read", "stream.overlay.write"),
)
