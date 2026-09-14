"""Agent-specific config: fills in the topics/scopes this agent needs
on top of the common AgentSettings fields from foundation.config."""
from __future__ import annotations

import os

from foundation import AgentSettings

SETTINGS = AgentSettings.from_env(
    agent_name="live_streaming_agent",
    subscribed_topics=(
        "stream.started",
        "stream.heartbeat",
        "stream.interrupted",
        "stream.reconnect.attempted",
        "liveEnded",
    ),
    permission_scopes=("stream.session.read", "stream.session.write"),
)

# --- Live Streaming Agent specific tuning (not part of shared AgentSettings) ---

MAX_RECONNECT_ATTEMPTS = int(os.getenv("MAX_RECONNECT_ATTEMPTS", "3"))

# A session with no heartbeat for this long is treated as stalled/interrupted
# even if the client never sent an explicit stream.interrupted event
# (covers a hard app crash / lost network with no chance to signal).
HEARTBEAT_STALE_SECONDS = float(os.getenv("HEARTBEAT_STALE_SECONDS", "45"))

# How often the background sweep checks for stale sessions.
STALE_SWEEP_INTERVAL_SECONDS = float(os.getenv("STALE_SWEEP_INTERVAL_SECONDS", "10"))
