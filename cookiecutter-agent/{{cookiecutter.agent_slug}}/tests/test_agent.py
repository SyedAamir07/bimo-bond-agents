"""Minimal smoke test every generated agent starts with."""
from foundation import EventEnvelope, InMemoryEventBus

from {{cookiecutter.agent_slug}}.agent import {{cookiecutter.agent_name.title().replace(' ', '')}}
from {{cookiecutter.agent_slug}}.config import SETTINGS


def test_agent_handles_event_without_crashing():
    bus = InMemoryEventBus()
    agent = {{cookiecutter.agent_name.title().replace(' ', '')}}(SETTINGS, event_bus=bus)
    agent.run()
    try:
        topic = SETTINGS.subscribed_topics[0] if SETTINGS.subscribed_topics else "test.event"
        event = EventEnvelope(
            event_type=topic,
            source_agent="test",
            payload={"example": True},
        )
        agent.handle_event(event)  # should not raise
    finally:
        agent.shutdown()
