"""
Live Streaming Agent

Monitor stream quality and stability, detect interruptions, manage reconnection attempts, and track performance indicators.
"""
from foundation import AgentContract, BaseAgent, EventEnvelope

# Reconnection policy -- kept as explicit, auditable constants rather
# than a value an LLM decides at runtime (per the project doc: don't
# assign decisions like this solely to a language model).
MAX_RECONNECT_ATTEMPTS = 3


class LiveStreamingAgent(BaseAgent):
    objective = "Monitor stream quality and stability, detect interruptions, manage reconnection attempts, and track performance indicators."
    contract = AgentContract(
        objective=objective,
        inputs=["stream.started", "stream.heartbeat", "stream.interrupted", "liveEnded"],
        outputs=["stream.reconnect.attempted", "stream.ended"],
        tools=["streaming_infra"],
        permission_scopes=["stream.session.read", "stream.session.write"],
        subscribed_topics=["stream.started", "stream.heartbeat", "stream.interrupted", "liveEnded"],
        published_topics=["stream.reconnect.attempted", "stream.ended"],
        failure_cases=["reconnect_exhausted", "infra_unavailable"],
        owner="platform-live",
        acceptance_criteria=["track interruption rate", "reconnect success measurable"],
        automatic_actions=["reconnect", "end_on_exhausted"],
        human_review_actions=["raise_max_reconnect_attempts"],
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # session_id -> reconnect attempt count. In production this would
        # live in shared/durable storage (e.g. Redis) so it survives a
        # restart -- kept as an in-memory dict here for a minimal sample.
        self._reconnect_attempts: dict[str, int] = {}

    def handle_event(self, event: EventEnvelope) -> None:
        if event.event_type == "stream.started":
            self._handle_stream_started(event)
        elif event.event_type == "stream.heartbeat":
            self._handle_heartbeat(event)
        elif event.event_type == "stream.interrupted":
            self._handle_interruption(event)
        elif event.event_type == "liveEnded":
            self._handle_live_ended(event)
        else:
            self.logger.warning("Unhandled event_type=%s", event.event_type)

    def _handle_stream_started(self, event: EventEnvelope) -> None:
        session_id = event.payload.get("session_id")
        self._reconnect_attempts[session_id] = 0
        self.logger.info("Stream started session_id=%s", session_id)

    def _handle_heartbeat(self, event: EventEnvelope) -> None:
        # TODO: record actual quality metrics (bitrate, dropped frames,
        # startup time) against the "measurable acceptance criteria"
        # defined for this agent, feeding an observability backend.
        self.logger.debug("Heartbeat session_id=%s", event.payload.get("session_id"))

    def _handle_live_ended(self, event: EventEnvelope) -> None:
        session_id = event.payload.get("liveId") or event.payload.get("session_id")
        self.logger.info("Nest liveEnded observed session_id=%s", session_id)
        self.publish(
            "stream.ended",
            payload={"session_id": session_id, "reason": "live_ended"},
            correlation_id=event.correlation_id,
        )

    def _handle_interruption(self, event: EventEnvelope) -> None:
        session_id = event.payload.get("session_id")
        attempts = self._reconnect_attempts.get(session_id, 0)

        if attempts >= MAX_RECONNECT_ATTEMPTS:
            self.logger.warning("Giving up reconnect for session_id=%s after %s attempts", session_id, attempts)
            self.publish(
                "stream.ended",
                payload={"session_id": session_id, "reason": "reconnect_attempts_exhausted"},
                correlation_id=event.correlation_id,
            )
            return

        self._reconnect_attempts[session_id] = attempts + 1
        self.logger.info("Reconnecting session_id=%s attempt=%s", session_id, attempts + 1)
        # TODO: trigger the actual reconnection against the streaming infra.
        self.publish(
            "stream.reconnect.attempted",
            payload={"session_id": session_id, "attempt": attempts + 1},
            correlation_id=event.correlation_id,
        )
