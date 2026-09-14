"""Agent-specific config: fills in the topics/scopes this agent needs
on top of the common AgentSettings fields from foundation.config."""
from __future__ import annotations

import os

from foundation import AgentSettings

SETTINGS = AgentSettings.from_env(
    agent_name="camera_agent",
    subscribed_topics=("camera.settings.requested", "camera.feature.toggled"),
    permission_scopes=("device.camera.read", "device.camera.write"),
)

# --- Camera Agent specific tuning ---

# How long a fetched catalog is trusted before re-fetching from the
# backend (GET /camera-studio/catalog).
CATALOG_CACHE_TTL_SECONDS = float(os.getenv("CATALOG_CACHE_TTL_SECONDS", "60"))
