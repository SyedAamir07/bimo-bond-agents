"""
Orchestration Agent

Coordinate events and tasks across agents, track execution status, and
manage timeouts, retries, and duplicate execution prevention
(project doc: AG-10a, proposed 10th agent -- subject to AG-10 approval
before production use; see README).
"""
from __future__ import annotations

import time
from enum import Enum

from foundation import (
    AgentContract,
    BaseAgent,
    EventEnvelope,
    TaskRecord,
    TaskStore,
    build_task_store,
)


class TaskStatus(str, Enum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


# Routing table: which agent owns which action, which event dispatches
# it, and which permission scope that action requires. This is the
# single place task routing is decided -- the orchestrator does not
# invent routes at runtime, and it never grants a route a scope beyond
# what's listed here (see "without... granting itself additional
# access" in the project doc).
ROUTING_TABLE: dict[str, dict] = {
    "toggle_camera_feature": {
        "target_agent": "camera_agent",
        "dispatch_event": "camera.feature.toggled",
        "required_scope": "device.camera.write",
    },
    "trigger_gift_effect": {
        "target_agent": "gift_effects_agent",
        "dispatch_event": "gift.sent",
        "required_scope": "stream.overlay.write",
    },
    "start_stream_monitor": {
        "target_agent": "live_streaming_agent",
        "dispatch_event": "stream.started",
        "required_scope": "stream.session.write",
    },
}

TASK_TIMEOUT_SECONDS = 30
MAX_RETRIES = 2

# Events each target agent emits back that this orchestrator correlates
# to a task_id it dispatched. Kept separate from FAILURE/SUCCESS below
# because some event types (stream.ended) carry both success and
# failure meanings depending on payload contents -- see
# _is_stream_ended_failure().
CORRELATED_EVENT_TYPES = frozenset(
    {
        "camera.feature.rejected",
        "camera.feature.applied",
        "stream.ended",
        "stream.monitor.armed",
        "gift.effect.skipped",
        "gift.effect.triggered",
    }
)

# Unconditional failure/success signals -- the event type alone
# determines the outcome, no payload inspection needed.
UNCONDITIONAL_FAILURE_EVENTS = {"camera.feature.rejected", "gift.effect.skipped"}
UNCONDITIONAL_SUCCESS_EVENTS = {"camera.feature.applied", "gift.effect.triggered", "stream.monitor.armed"}

# stream.ended reasons that represent a genuine failure of the dispatched
# task (vs. a normal end-of-session that isn't a task failure at all --
# see live_streaming_agent's StreamSession end reasons).
STREAM_ENDED_FAILURE_REASONS = {"reconnect_attempts_exhausted", "heartbeat_stale_timeout"}


def _is_stream_ended_failure(event: EventEnvelope) -> bool:
    return event.payload.get("reason") in STREAM_ENDED_FAILURE_REASONS


class OrchestrationAgent(BaseAgent):
    objective = "Coordinate events and tasks across agents, track execution status, and manage timeouts, retries, and duplicate execution prevention."
    contract = AgentContract(
        objective=objective,
        inputs=[
            "task.requested",
            "camera.feature.rejected",
            "camera.feature.applied",
            "stream.ended",
            "stream.monitor.armed",
            "gift.effect.skipped",
            "gift.effect.triggered",
        ],
        outputs=["camera.feature.toggled", "gift.sent", "stream.started", "task.failed"],
        tools=["routing_table", "task_store (durable)"],
        permission_scopes=["orchestration.route.dispatch"],
        subscribed_topics=[
            "task.requested",
            "camera.feature.rejected",
            "camera.feature.applied",
            "stream.ended",
            "stream.monitor.armed",
            "gift.effect.skipped",
            "gift.effect.triggered",
        ],
        published_topics=["camera.feature.toggled", "gift.sent", "stream.started", "task.failed"],
        failure_cases=["unknown_action", "timeout", "max_retries"],
        owner="platform-platform",
        acceptance_criteria=[
            "no duplicate task_id dispatch",
            "timeouts retried then failed",
            "task state survives orchestrator restart",
            "routes stay within ROUTING_TABLE -- never a broader scope than declared",
        ],
        automatic_actions=["route_task", "retry_on_failure", "timeout_sweep", "mark_completed"],
        human_review_actions=["approve_new_routing_table_entries"],
    )

    def __init__(self, *args, task_store: TaskStore | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Durable by default: build_task_store() returns InMemoryTaskStore
        # for memory:// (local/tests) and RedisTaskStore otherwise, so
        # task state survives an orchestrator restart in production
        # instead of silently vanishing (the prior in-memory-only dict).
        self._store: TaskStore = task_store or build_task_store(
            self.settings.event_bus_url,
            key_prefix=f"orchestration:{self.settings.agent_name}:tasks",
        )

    def handle_event(self, event: EventEnvelope) -> None:
        if event.event_type == "task.requested":
            self._handle_task_requested(event)
        elif event.event_type in CORRELATED_EVENT_TYPES:
            if self._is_failure(event):
                self._handle_target_failure(event)
            else:
                self._handle_target_success(event)
        else:
            self.logger.warning("Unhandled event_type=%s", event.event_type)

    def _is_failure(self, event: EventEnvelope) -> bool:
        if event.event_type in UNCONDITIONAL_FAILURE_EVENTS:
            return True
        if event.event_type in UNCONDITIONAL_SUCCESS_EVENTS:
            return False
        if event.event_type == "stream.ended":
            return _is_stream_ended_failure(event)
        # Unreachable given CORRELATED_EVENT_TYPES, but fail closed
        # (treat as failure) rather than silently marking an unknown
        # signal as success.
        return True

    # --- task dispatch ---------------------------------------------------

    def _handle_task_requested(self, event: EventEnvelope) -> None:
        task_id = event.payload.get("task_id")
        action = event.payload.get("action")

        # Duplicate execution prevention: a task_id already seen is never
        # re-dispatched, regardless of how many times the request event
        # arrives (at-least-once delivery from a real broker will resend).
        # Because self._store is durable, this holds across restarts too --
        # not just within one process lifetime.
        existing = self._store.get(task_id)
        if existing is not None:
            self.logger.info(
                "Ignoring duplicate task_id=%s (status=%s)", task_id, existing.status
            )
            self.audit.record(
                "task.duplicate_ignored",
                event_id=event.event_id,
                correlation_id=task_id,
                detail={"action": action, "existing_status": existing.status},
            )
            return

        route = ROUTING_TABLE.get(action)
        if route is None:
            self.logger.warning("Rejected task_id=%s: no route for action=%s", task_id, action)
            self._store.save(
                TaskRecord(
                    task_id=task_id,
                    action=action or "unknown",
                    target_agent="unknown",
                    status=TaskStatus.FAILED.value,
                    last_error="unknown_action",
                )
            )
            self.audit.record(
                "task.rejected",
                outcome="failed",
                event_id=event.event_id,
                correlation_id=task_id,
                detail={"action": action, "reason": "unknown_action"},
            )
            return

        self._dispatch(task_id, action, route, event.payload, attempt=1)

    def _dispatch(self, task_id: str, action: str, route: dict, task_payload: dict, attempt: int) -> None:
        """
        `task_payload` must be the *original* task.requested payload, not
        whatever event triggered this dispatch call. A retry is driven by
        a failure or timeout event -- neither carries the real task data
        (a failure event's payload describes the failure, and a timeout
        has no payload at all) -- so the record is the only place that
        data can come from on retry. See _handle_target_failure() and
        check_timeouts(), which both pass record.payload here instead of
        the triggering event's payload.
        """
        record = TaskRecord(
            task_id=task_id,
            action=action,
            target_agent=route["target_agent"],
            status=TaskStatus.DISPATCHED.value,
            attempts=attempt,
            dispatched_at=time.time(),
            payload=task_payload,
        )
        self._store.save(record)

        self.logger.info(
            "Dispatching task_id=%s action=%s -> agent=%s attempt=%s",
            task_id, action, route["target_agent"], attempt,
        )
        self.audit.record(
            "task.dispatched",
            correlation_id=task_id,
            detail={"action": action, "target_agent": route["target_agent"], "attempt": attempt},
        )
        # Route strictly within the scope this action is defined for --
        # never a broader one, even if the caller asked for more.
        self.publish(
            route["dispatch_event"],
            payload={**task_payload, "required_scope": route["required_scope"]},
            correlation_id=task_id,
        )

    def _handle_target_success(self, event: EventEnvelope) -> None:
        task_id = event.correlation_id
        if not task_id:
            self.logger.debug("Success event without correlation_id type=%s", event.event_type)
            return
        self.mark_completed(task_id)
        self.logger.info(
            "task_id=%s completed via event_type=%s",
            task_id,
            event.event_type,
        )

    # --- failure / retry ---------------------------------------------------

    def _handle_target_failure(self, event: EventEnvelope) -> None:
        task_id = event.correlation_id
        record = self._store.get(task_id)
        if record is None:
            self.logger.warning("Failure event for unknown task_id=%s", task_id)
            return

        if record.attempts >= MAX_RETRIES:
            record.status = TaskStatus.FAILED.value
            record.last_error = f"failed_via_event:{event.event_type}"
            self._store.save(record)
            self.logger.warning("task_id=%s failed permanently after %s attempts", task_id, record.attempts)
            self.audit.record(
                "task.failed_permanently",
                outcome="failed",
                correlation_id=task_id,
                detail={"action": record.action, "attempts": record.attempts},
            )
            self.publish("task.failed", payload={"task_id": task_id, "action": record.action}, correlation_id=task_id)
            return

        route = ROUTING_TABLE[record.action]
        self.logger.info("Retrying task_id=%s attempt=%s", task_id, record.attempts + 1)
        self._dispatch(task_id, record.action, route, record.payload, attempt=record.attempts + 1)

    # --- timeout sweep ---------------------------------------------------

    def check_timeouts(self) -> None:
        """
        Call periodically (e.g. from a scheduler loop in main.py) to catch
        tasks that were dispatched but never confirmed complete/failed --
        the case a pure event-driven handler alone can't detect.

        Reads from the durable store, so a sweep after an orchestrator
        restart still finds tasks that were dispatched before the crash.
        """
        now = time.time()
        for record in self._store.all_dispatched():
            if now - record.dispatched_at <= TASK_TIMEOUT_SECONDS:
                continue

            if record.attempts >= MAX_RETRIES:
                record.status = TaskStatus.TIMED_OUT.value
                record.last_error = "timeout"
                self._store.save(record)
                self.logger.warning("task_id=%s timed out permanently", record.task_id)
                self.audit.record(
                    "task.timed_out",
                    outcome="failed",
                    correlation_id=record.task_id,
                    detail={"action": record.action, "attempts": record.attempts},
                )
                self.publish(
                    "task.failed",
                    payload={"task_id": record.task_id, "action": record.action, "reason": "timeout"},
                    correlation_id=record.task_id,
                )
            else:
                route = ROUTING_TABLE.get(record.action)
                if route is None:
                    record.status = TaskStatus.FAILED.value
                    record.last_error = "route_missing_on_retry"
                    self._store.save(record)
                    continue
                self.logger.info("task_id=%s timed out, retrying attempt=%s", record.task_id, record.attempts + 1)
                self._dispatch(
                    record.task_id,
                    record.action,
                    route,
                    record.payload,
                    attempt=record.attempts + 1,
                )

    def mark_completed(self, task_id: str) -> None:
        """Called when a target agent's success event is observed (wire this
        up to each agent's *.applied / *.triggered event in the routing table
        once the orchestration agent subscribes to success events too)."""
        record = self._store.get(task_id)
        if record is not None:
            record.status = TaskStatus.COMPLETED.value
            self._store.save(record)
            self.audit.record(
                "task.completed",
                correlation_id=task_id,
                detail={"action": record.action},
            )

    def get_task(self, task_id: str) -> TaskRecord | None:
        """Expose task state for tests / ops inspection."""
        return self._store.get(task_id)
