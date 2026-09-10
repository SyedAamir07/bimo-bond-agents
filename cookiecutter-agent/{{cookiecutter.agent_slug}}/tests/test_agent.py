"""Minimal smoke test every generated agent starts with."""
from foundation import EventEnvelope, InMemoryEventBus

from src.{{cookiecutter.agent_slug}}.agent import {{cookiecutter.agent_name.title().replace(' ', '')}}
from src.{{cookiecutter.agent_slug}}.config import SETTINGS


def test_agent_handles_event_without_crashing():
    bus = InMemoryEventBus()
    agent = {{cookiecutter.agent_name.title().replace(' ', '')}}(SETTINGS, event_bus=bus)
    agent.run()

    event = EventEnvelope(
        event_type=SETTINGS.subscribed_topics[0] if SETTINGS.subscribed_topics else "test.event",
        source_agent="test",
        payload={"example": True},
    )
    agent.handle_event(event)  # should not raise
