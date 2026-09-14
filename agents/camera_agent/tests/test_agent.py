"""Camera Agent tests: catalog-driven approval, rejection, fail-closed
behavior on catalog outage, and settings reporting."""
from __future__ import annotations

from foundation import BackendClient, EventEnvelope, InMemoryEventBus

from camera_agent.agent import CameraAgent
from camera_agent.catalog_client import CameraCatalog, CatalogClient
from camera_agent.config import SETTINGS


class _FakeBackendClient(BackendClient):
    """BackendClient stand-in returning a fixed catalog payload without HTTP."""

    def __init__(self, catalog_payload: dict | None, *, raise_error: bool = False) -> None:
        super().__init__(base_url="http://fake-backend", api_token="test-token")
        self._catalog_payload = catalog_payload
        self._raise_error = raise_error

    def get(self, path: str, **kwargs):  # noqa: D401
        if self._raise_error:
            raise ConnectionError("simulated backend outage")
        if path == "/camera-studio/catalog":
            return self._catalog_payload
        raise AssertionError(f"unexpected path {path}")


_SAMPLE_CATALOG_PAYLOAD = {
    "version": "2026-01-01T00",
    "colorFilterCategories": [
        {"slug": "trending", "filters": [{"slug": "beauty_filter"}, {"slug": "warm_tone"}]},
    ],
    "effectCategories": [
        {"slug": "ar", "effects": [{"slug": "sparkle_overlay"}]},
    ],
}


def _make_agent(backend: BackendClient | None = None) -> CameraAgent:
    bus = InMemoryEventBus()
    agent = CameraAgent(SETTINGS, event_bus=bus)
    if backend is not None:
        agent.backend = backend
        agent._catalog = CatalogClient(backend, ttl_seconds=60)
    agent.run()
    return agent


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


def test_approved_filter_from_catalog_is_applied():
    agent = _make_agent(_FakeBackendClient(_SAMPLE_CATALOG_PAYLOAD))
    applied: list[EventEnvelope] = []
    agent.event_bus.subscribe("camera.feature.applied", applied.append)

    agent.handle_event(
        EventEnvelope(
            event_type="camera.feature.toggled",
            source_agent="test",
            payload={"feature": "beauty_filter", "enabled": True},
        )
    )

    assert len(applied) == 1
    assert applied[0].payload["feature"] == "beauty_filter"


def test_approved_effect_from_catalog_is_applied():
    agent = _make_agent(_FakeBackendClient(_SAMPLE_CATALOG_PAYLOAD))
    applied: list[EventEnvelope] = []
    agent.event_bus.subscribe("camera.feature.applied", applied.append)

    agent.handle_event(
        EventEnvelope(
            event_type="camera.feature.toggled",
            source_agent="test",
            payload={"feature": "sparkle_overlay", "enabled": True},
        )
    )

    assert len(applied) == 1


def test_feature_not_in_catalog_is_rejected():
    agent = _make_agent(_FakeBackendClient(_SAMPLE_CATALOG_PAYLOAD))
    rejected: list[EventEnvelope] = []
    agent.event_bus.subscribe("camera.feature.rejected", rejected.append)

    agent.handle_event(
        EventEnvelope(
            event_type="camera.feature.toggled",
            source_agent="test",
            payload={"feature": "totally_made_up_feature", "enabled": True},
        )
    )

    assert len(rejected) == 1
    assert rejected[0].payload["reason"] == "not_in_approved_catalog"


def test_missing_feature_field_is_rejected_not_crashed():
    agent = _make_agent(_FakeBackendClient(_SAMPLE_CATALOG_PAYLOAD))
    rejected: list[EventEnvelope] = []
    agent.event_bus.subscribe("camera.feature.rejected", rejected.append)

    agent.handle_event(
        EventEnvelope(event_type="camera.feature.toggled", source_agent="test", payload={"enabled": True})
    )

    assert len(rejected) == 1


def test_catalog_outage_fails_closed_rejects_everything():
    """
    A backend outage must not silently approve every feature -- the
    catalog stays empty (never successfully fetched) and every toggle
    is rejected until the backend is reachable again.
    """
    agent = _make_agent(_FakeBackendClient(None, raise_error=True))
    rejected: list[EventEnvelope] = []
    applied: list[EventEnvelope] = []
    agent.event_bus.subscribe("camera.feature.rejected", rejected.append)
    agent.event_bus.subscribe("camera.feature.applied", applied.append)

    agent.handle_event(
        EventEnvelope(
            event_type="camera.feature.toggled",
            source_agent="test",
            payload={"feature": "beauty_filter", "enabled": True},
        )
    )

    assert applied == []
    assert len(rejected) == 1


def test_transient_outage_after_success_keeps_serving_cached_catalog():
    """
    Once a catalog has been successfully fetched, a later transient
    outage must keep serving the last-known-good catalog rather than
    suddenly rejecting previously-approved features.
    """
    backend = _FakeBackendClient(_SAMPLE_CATALOG_PAYLOAD)
    client = CatalogClient(backend, ttl_seconds=0)  # TTL 0 -> always re-check
    first = client.get()
    assert first.is_approved("beauty_filter")

    # Backend now fails, but TTL-driven re-fetch happening on next get()
    # should retain the previous cache instead of clearing it.
    backend._raise_error = True
    second = client.get()
    assert second.is_approved("beauty_filter")  # still trusts the last-known-good catalog


def test_settings_reported_lists_catalog_filters_and_effects():
    agent = _make_agent(_FakeBackendClient(_SAMPLE_CATALOG_PAYLOAD))
    reported: list[EventEnvelope] = []
    agent.event_bus.subscribe("camera.settings.reported", reported.append)

    agent.handle_event(
        EventEnvelope(
            event_type="camera.settings.requested",
            source_agent="test",
            payload={"device_id": "device-1"},
        )
    )

    assert len(reported) == 1
    payload = reported[0].payload
    assert payload["device_id"] == "device-1"
    assert "beauty_filter" in payload["available_filters"]
    assert "sparkle_overlay" in payload["available_effects"]


def test_camera_catalog_from_api_response_parses_nested_slugs():
    catalog = CameraCatalog.from_api_response(_SAMPLE_CATALOG_PAYLOAD)
    assert catalog.filter_slugs == frozenset({"beauty_filter", "warm_tone"})
    assert catalog.effect_slugs == frozenset({"sparkle_overlay"})
    assert catalog.is_approved("beauty_filter")
    assert not catalog.is_approved("unknown")


def test_empty_catalog_approves_nothing():
    catalog = CameraCatalog.empty()
    assert not catalog.is_approved("beauty_filter")
