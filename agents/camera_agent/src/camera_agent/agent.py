"""
Camera Agent

Manage camera functions, improve image quality, and control approved in-app camera features.
"""
from foundation import AgentContract, BaseAgent, EventEnvelope

# Approved feature list — per the project doc, this must be defined and
# approved before implementation, not invented ad hoc by the agent.
APPROVED_FEATURES = {"beauty_filter", "hdr", "low_light_boost", "grid_overlay"}


class CameraAgent(BaseAgent):
    objective = "Manage camera functions, improve image quality, and control approved in-app camera features."
    contract = AgentContract(
        objective=objective,
        inputs=["camera.settings.requested", "camera.feature.toggled"],
        outputs=["camera.feature.applied", "camera.feature.rejected", "camera.settings.reported"],
        tools=["camera_sdk"],
        permission_scopes=["device.camera.read", "device.camera.write"],
        subscribed_topics=["camera.settings.requested", "camera.feature.toggled"],
        published_topics=["camera.feature.applied", "camera.feature.rejected", "camera.settings.reported"],
        failure_cases=["unapproved_feature", "camera_sdk_unavailable"],
        owner="platform-media",
        acceptance_criteria=["feature latency < 200ms", "reject unapproved features"],
        automatic_actions=["apply_approved_feature", "report_settings"],
        human_review_actions=["approve_new_camera_features"],
    )

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

        if feature not in APPROVED_FEATURES:
            # Never silently apply an unapproved feature -- reject and
            # report, don't grant scope the agent wasn't given.
            self.logger.warning("Rejected unapproved camera feature=%s", feature)
            self.publish(
                "camera.feature.rejected",
                payload={"feature": feature, "reason": "not_in_approved_feature_list"},
                correlation_id=event.correlation_id,
            )
            return

        self.logger.info("Applying camera feature=%s enabled=%s", feature, enabled)
        # TODO: call the actual camera SDK/service here.
        self.publish(
            "camera.feature.applied",
            payload={"feature": feature, "enabled": enabled},
            correlation_id=event.correlation_id,
        )

    def _handle_settings_request(self, event: EventEnvelope) -> None:
        self.logger.info("Settings requested for device=%s", event.payload.get("device_id"))
        # TODO: fetch actual current settings from the camera service.
        self.publish(
            "camera.settings.reported",
            payload={"device_id": event.payload.get("device_id"), "available_features": sorted(APPROVED_FEATURES)},
            correlation_id=event.correlation_id,
        )
