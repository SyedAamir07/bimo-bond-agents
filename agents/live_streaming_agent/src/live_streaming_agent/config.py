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

# --- ML anomaly detection (advisory early-warning layer; see anomaly_model.py) ---

ML_MIN_TRAINING_SAMPLES = int(os.getenv("ML_MIN_TRAINING_SAMPLES", "30"))
ML_RETRAIN_INTERVAL_SAMPLES = int(os.getenv("ML_RETRAIN_INTERVAL_SAMPLES", "20"))

# "auto" (IsolationForest's own data-driven default) unless a fixed
# fraction is explicitly set. A fixed contamination (e.g. 0.05) forces
# that exact proportion of training data to be labeled anomalous no
# matter how it's actually distributed -- on the small, low-variance
# datasets this agent trains on early on, that produced both false
# positives (clean sessions flagged) and false negatives (the fixed
# fraction was "used up" on noise, leaving no margin to flag a real
# outlier) in manual live testing. "auto" lets scikit-learn derive the
# threshold from the data's own spread instead of a hardcoded prior.
_ML_CONTAMINATION_RAW = os.getenv("ML_CONTAMINATION", "auto")
ML_CONTAMINATION: str | float = (
    _ML_CONTAMINATION_RAW if _ML_CONTAMINATION_RAW == "auto" else float(_ML_CONTAMINATION_RAW)
)
