"""
BaseAgent: the standard lifecycle every agent follows.

This is the piece that makes agents interchangeable at the plumbing
level. A new agent should only need to implement `handle_event()` and
declare its subscribed topics / AgentContract — everything else
(logging, health, permissions, dedup, retry, metrics, backend client,
graceful shutdown) is inherited.
"""
from __future__ import annotations

import logging
import signal
from abc import ABC, abstractmethod
from types import FrameType
from typing import Any

from .acceptance import AcceptanceTracker
from .audit import AuditLog
from .audit_sink import build_audit_sink
from .backend import BackendClient
from .config import AgentSettings
from .contracts import AgentContract, EventEnvelope, validate_event_payload
from .event_bus import EventBus, build_event_bus
from .health import HealthStatus, Status
from .health_server import HealthServer
from .logging_setup import configure_logging
from .metrics import AgentMetrics
from .permissions import PermissionDenied, assert_scope
from .reliability import EventDeduplicator, RetryPolicy
from .retention import get_retention, parse_occurred_at


class BaseAgent(ABC):
    """
    Subclass this for every agent. Contract each subclass fulfills:

      - objective:           human-readable purpose
      - contract:            machine-readable AgentContract (optional override)
      - handle_event():      business logic for one incoming event
      - startup()/shutdown() (optional overrides for setup/teardown)

    What subclasses get for free:
      - config loaded the same way (AgentSettings)
      - structured logging configured the same way
      - an event bus client wired to `subscribed_topics` from config
      - permission enforcement on inbound required_scope
      - bounded event-id dedup + handler retry
      - health/contract/metrics HTTP endpoints
      - optional NestJS BackendClient
      - clean shutdown on SIGTERM/SIGINT
    """

    objective: str = "Undocumented agent objective — fill this in per the agent contract."
    contract: AgentContract | None = None

    def __init__(self, settings: AgentSettings, event_bus: EventBus | None = None) -> None:
        self.settings = settings
        configure_logging(settings.agent_name, settings.log_level)
        self.logger = logging.getLogger(settings.agent_name)

        self.event_bus = event_bus or build_event_bus(
            settings.event_bus_url,
            agent_name=settings.agent_name,
            stream_key=settings.event_stream_key,
            consumer_group=settings.resolved_consumer_group(),
            dedup_max_size=settings.dedup_max_size,
            stream_maxlen=settings.stream_maxlen,
            max_deliveries=settings.max_deliveries,
            dlq_stream_key=settings.dlq_stream_key,
        )
        self.health = HealthStatus(agent_name=settings.agent_name)
        self.metrics = AgentMetrics(agent_name=settings.agent_name)
        self.audit = AuditLog(
            settings.agent_name,
            max_records=settings.audit_max_records,
            sink=build_audit_sink(settings.event_bus_url),
        )
        self.acceptance = AcceptanceTracker()
        self._dedup = EventDeduplicator(max_size=settings.dedup_max_size)
        self._retry = RetryPolicy(max_attempts=settings.handler_max_attempts)
        self.backend = BackendClient(
            base_url=settings.backend_base_url,
            api_token=settings.backend_api_token,
            timeout_seconds=settings.backend_timeout_seconds,
        )
        self._health_server = HealthServer(
            settings.health_port,
            health_provider=lambda: self.health,
            contract_provider=self.get_contract,
            metrics_provider=lambda: self.metrics,
            audit_provider=lambda: self.audit,
            acceptance_provider=lambda: self.acceptance,
        )

        if self.contract is None:
            self.contract = AgentContract(
                objective=self.objective,
                permission_scopes=list(settings.permission_scopes),
                subscribed_topics=list(settings.subscribed_topics),
            )

        self._register_subscriptions()
        signal.signal(signal.SIGTERM, self._handle_shutdown_signal)
        signal.signal(signal.SIGINT, self._handle_shutdown_signal)

    # --- lifecycle -----------------------------------------------------

    def run(self) -> None:
        self.logger.info(
            "Starting agent=%s version=%s env=%s bus=%s",
            self.settings.agent_name,
            self.settings.agent_version,
            self.settings.environment,
            self.settings.event_bus_url.split("?", 1)[0],
        )
        self.startup()
        self._health_server.start()
        self.event_bus.start()
        self.health.status = Status.OK
        self.logger.info(
            "Agent=%s is up, subscribed to topics=%s",
            self.settings.agent_name,
            self.settings.subscribed_topics,
        )

    def startup(self) -> None:
        """Override for one-time setup (load models, warm caches, connect to DB, ...)."""

    def shutdown(self) -> None:
        """Override for cleanup. Called automatically on SIGTERM/SIGINT."""
        self._health_server.stop()
        self.event_bus.stop()
        self.logger.info("Agent=%s stopped cleanly", self.settings.agent_name)

    def _handle_shutdown_signal(self, signum: int, frame: FrameType | None) -> None:
        self.logger.info(
            "Received signal=%s, shutting down agent=%s",
            signum,
            self.settings.agent_name,
        )
        self.health.status = Status.DOWN
        self.shutdown()

    def get_contract(self) -> AgentContract | None:
        return self.contract

    # --- event handling --------------------------------------------------

    def _register_subscriptions(self) -> None:
        for topic in self.settings.subscribed_topics:
            self.event_bus.subscribe(topic, self._safe_handle_event)

    def _safe_handle_event(self, event: EventEnvelope) -> None:
        """
        Pipeline: dedup → permission → payload validate → retry handle → metrics.

        One bad event degrades health instead of crashing the process.
        """
        log = self.logger
        log.info(
            "Handling event_type=%s event_id=%s correlation_id=%s",
            event.event_type,
            event.event_id,
            event.correlation_id,
        )

        if self._dedup.seen_before(event.event_id):
            self.metrics.incr("duplicates_dropped")
            self.audit.record(
                "event.duplicate_dropped",
                outcome="denied",
                event_id=event.event_id,
                correlation_id=event.correlation_id,
                detail={"event_type": event.event_type},
            )
            log.info("Duplicate event_id=%s dropped", event.event_id)
            return

        occurred = parse_occurred_at(event.occurred_at)
        if occurred is not None and get_retention("event_envelope").is_expired(occurred):
            self.metrics.incr("events_expired")
            self.audit.record(
                "event.expired_dropped",
                outcome="denied",
                event_id=event.event_id,
                correlation_id=event.correlation_id,
                detail={"event_type": event.event_type, "occurred_at": event.occurred_at},
            )
            log.info("Expired event_id=%s dropped (retention)", event.event_id)
            return

        required_scope = event.payload.get("required_scope")
        if required_scope:
            try:
                assert_scope(self.settings, required_scope)
            except PermissionDenied as exc:
                self.metrics.incr("permission_denied")
                self.audit.record(
                    "event.permission_denied",
                    outcome="denied",
                    scope=required_scope,
                    event_id=event.event_id,
                    correlation_id=event.correlation_id,
                    detail={"event_type": event.event_type},
                )
                log.warning("%s event_id=%s", exc, event.event_id)
                return

        missing = validate_event_payload(event)
        if missing:
            self.metrics.incr("events_invalid")
            log.warning(
                "Event payload missing keys=%s event_type=%s event_id=%s",
                missing,
                event.event_type,
                event.event_id,
            )
            # Soft-fail: still deliver so agents can decide; metrics track it.

        try:
            with self.metrics.timer("handler_latency") as elapsed_holder:
                self._retry.run(
                    lambda: self.handle_event(event),
                    on_attempt_fail=lambda attempt, err: log.warning(
                        "Handler attempt=%s failed event_id=%s err=%s",
                        attempt,
                        event.event_id,
                        err,
                    ),
                )
            self.metrics.incr("events_handled")
            latency_ms = elapsed_holder.get("ms")
            if latency_ms is not None:
                self.acceptance.observe("handler_latency_ms", latency_ms)
        except Exception:  # noqa: BLE001
            self.metrics.incr("events_failed")
            self.audit.record(
                "event.handle_failed",
                outcome="failed",
                event_id=event.event_id,
                correlation_id=event.correlation_id,
                detail={"event_type": event.event_type},
            )
            log.exception(
                "Failed handling event_type=%s event_id=%s",
                event.event_type,
                event.event_id,
            )
            self.health.status = Status.DEGRADED

    @abstractmethod
    def handle_event(self, event: EventEnvelope) -> None:
        """Business logic for a single incoming event. Must be implemented
        by every concrete agent."""

    def require_scope(self, scope: str) -> None:
        """Call from agent code before a privileged side effect."""
        assert_scope(self.settings, scope)
        self.audit.record(
            "scope.asserted",
            outcome="success",
            scope=scope,
        )

    def publish(
        self,
        event_type: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
        *,
        required_scope: str | None = None,
    ) -> None:
        """Convenience wrapper so agents don't construct EventEnvelope by hand."""
        if required_scope is not None:
            assert_scope(self.settings, required_scope)

        event = EventEnvelope(
            event_type=event_type,
            source_agent=self.settings.agent_name,
            payload=payload,
            correlation_id=correlation_id,
        )
        self.event_bus.publish(event)
        self.metrics.incr("events_published")
        self.audit.record(
            "event.published",
            outcome="success",
            scope=required_scope,
            event_id=event.event_id,
            correlation_id=correlation_id,
            detail={"event_type": event_type},
        )
        self.logger.debug(
            "Published event_type=%s event_id=%s",
            event_type,
            event.event_id,
        )
