"""Agent-specific config: fills in the topics/scopes this agent needs
on top of the common AgentSettings fields from foundation.config."""
from __future__ import annotations

import os

from foundation import AgentSettings

SETTINGS = AgentSettings.from_env(
    agent_name="live_auction_agent",
    subscribed_topics=("auctionUpdated", "liveGiftCombo", "liveEnded"),
    permission_scopes=("auction.read", "auction.audit.write"),
)

# --- Live Auction Agent specific tuning ---

# "earliest" auto-resolves ties by earliest contribution timestamp;
# "explicit" flags ties for human/product review instead (see
# foundation.auction_rules.resolve_purchase_eligibility).
AUCTION_TIE_BREAK_POLICY = os.getenv("AUCTION_TIE_BREAK_POLICY", "earliest")
