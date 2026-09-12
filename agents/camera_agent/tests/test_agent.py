"""Minimal smoke test every generated agent starts with."""
from foundation import EventEnvelope, InMemoryEventBus

from camera_agent.agent import CameraAgent
from camera_agent.config import SETTINGS


def test_agent_handles_event_without_crashing():
    bus = InMemoryEventBus()
    agent = CameraAgent(SETTINGS, event_bus=bus)
    agent.run()

    event = EventEnvelope(
        event_type=SETTINGS.subscribed_topics[0] if SETTINGS.subscribed_topics else "test.event",
        source_agent="test",
        payload={"example": True},
    )
    agent.handle_event(event)  # should not raise
