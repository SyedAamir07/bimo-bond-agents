"""Agent-specific config: fills in the topics/scopes this agent needs
on top of the common AgentSettings fields from foundation.config."""
from foundation import AgentSettings

SETTINGS = AgentSettings.from_env(
    agent_name="live_streaming_agent",
    subscribed_topics=tuple(
        "stream.started,stream.heartbeat,stream.interrupted,liveEnded".split(",")
    ),
    permission_scopes=tuple("stream.session.read,stream.session.write".split(",")),
)
