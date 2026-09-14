"""
Camera Agent

Manage camera functions, improve image quality, and control approved
in-app camera features (project doc: AG-01).

Design notes:

"Approved" is not decided by this agent. Per the project doc: "Define
and approve supported features and target devices before
implementation" (AG-01a) is a blocking product gate (PD-02). The
backend's Camera Studio module already implements that approval
mechanism -- filters and AR effects are created and activated through
the admin dashboard, and `GET /camera-studio/catalog` returns only the
currently-active ones. This agent treats that catalog as the single
source of truth (see catalog_client.py) instead of hardcoding its own
feature list that could silently drift out of sync with what product
actually approved.

Deterministic workflow, not an LLM agent -- approval is a lookup
against product-approved data, not a model decision.
"""
from __future__ import annotations

from foundation import AgentContract, BaseAgent, EventEnvelope

from .catalog_client import CatalogClient
from .config import CATALOG_CACHE_TTL_SECONDS


class CameraAgent(BaseAgent):
    objective = "Manage camera functions, improve image quality, and control approved in-app camera features."
    contract = AgentContract(
        objective=objective,
        inputs=["camera.settings.requested", "camera.feature.toggled"],
        outputs=["camera.feature.applied", "camera.feature.rejected", "camera.settings.reported"],
        tools=["camera_studio_catalog (backend, source of truth for approval)"],
        permission_scopes=["device.camera.read", "device.camera.write"],
        subscribed_topics=["camera.settings.requested", "camera.feature.toggled"],
        published_topics=["camera.feature.applied", "camera.feature.rejected", "camera.settings.reported"],
        failure_cases=["unapproved_feature", "catalog_unavailable"],
        owner="platform-media",
        acceptance_criteria=[
            "feature toggle latency measurable",
            "never applies a feature absent from the backend catalog",
            "catalog outage does not silently approve everything",
        ],
        automatic_actions=["apply_catalog_approved_feature", "report_settings"],
        human_review_actions=[
            "approve_new_camera_studio_filters_or_effects (via admin dashboard, not this agent)",
        ],
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._catalog = CatalogClient(self.backend, ttl_seconds=CATALOG_CACHE_TTL_SECONDS)

    def handle_event(self, event: EventEnvelope) -> None:
        if event.event_type == "camera.feature.toggled":
            self._handle_feature_toggle(event)
        elif event.event_type == "camera.settings.requested":
            self._handle_settings_request(event)
        else:
            self.logger.warning("Unhandled event_type=%s", event.event_type)

    def _handle_feature_toggle(self, event: EventEnvelope) -> None:
        feature = event.payload.get("feature")
        enabled = event.payload.get("enabled", False)

        catalog = self._catalog.get()
        if not feature or not catalog.is_approved(str(feature)):
            # Never silently apply an unapproved feature -- reject and
            # report, don't grant scope the agent wasn't given. This
            # also fires if the catalog has never been successfully
            # fetched (fail-closed), which is the correct behavior: an
            # agent that can't confirm approval must not guess yes.
            self.logger.warning(
                "Rejected camera feature=%s (not in approved catalog, catalog_version=%s)",
                feature, catalog.version,
            )
            self.audit.record(
                "camera.feature.rejected",
                outcome="denied",
                event_id=event.event_id,
                correlation_id=event.correlation_id,
                detail={"feature": feature, "catalog_version": catalog.version},
            )
            self.publish(
                "camera.feature.rejected",
                payload={"feature": feature, "reason": "not_in_approved_catalog"},
                correlation_id=event.correlation_id,
            )
            return

        self.logger.info("Applying camera feature=%s enabled=%s", feature, enabled)
        # TODO: call the actual camera SDK/service here. This agent
        # coordinates approval + event flow; it does not itself render
        # frames -- that's the mobile app's native camera engine
        # (see bimobond-app/lib/app/camera_engine).
        self.audit.record(
            "camera.feature.applied",
            outcome="success",
            event_id=event.event_id,
            correlation_id=event.correlation_id,
            detail={"feature": feature, "enabled": enabled, "catalog_version": catalog.version},
        )
        self.publish(
            "camera.feature.applied",
            payload={"feature": feature, "enabled": enabled},
            correlation_id=event.correlation_id,
        )

    def _handle_settings_request(self, event: EventEnvelope) -> None:
        device_id = event.payload.get("device_id")
        self.logger.info("Settings requested for device_id=%s", device_id)
        catalog = self._catalog.get()
        self.publish(
            "camera.settings.reported",
            payload={
                "device_id": device_id,
                "catalog_version": catalog.version,
                "available_filters": sorted(catalog.filter_slugs),
                "available_effects": sorted(catalog.effect_slugs),
            },
            correlation_id=event.correlation_id,
        )

    def refresh_catalog(self) -> None:
        """Force a catalog refresh (exposed for tests / ops / a periodic sweep)."""
        self._catalog.fetch()
