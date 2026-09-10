"""
Event bus abstraction.

Agents never talk to Redis/Kafka/RabbitMQ/SQS directly — they talk to this
interface. That means:
  1. Every agent gets duplicate-event handling and retry behavior for free.
  2. Swapping the real broker later doesn't touch agent code.
  3. Local dev/tests can run against InMemoryEventBus with zero infra.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Callable
from urllib.parse import parse_qs, urlparse

from .contracts import EventEnvelope
from .reliability import EventDeduplicator

logger = logging.getLogger(__name__)

Handler = Callable[[EventEnvelope], None]


class EventBus(ABC):
    """Contract every event bus implementation must satisfy."""

    @abstractmethod
    def publish(self, event: EventEnvelope) -> None:
        """Publish an event."""

    @abstractmethod
    def subscribe(self, topic: str, handler: Handler) -> None:
        """Register `handler` to be called for every event of type `topic`."""

    @abstractmethod
    def start(self) -> None:
        """Start consuming."""

    @abstractmethod
    def stop(self) -> None:
        """Stop consuming cleanly."""


class InMemoryEventBus(EventBus):
    """
    Local dev / unit-test bus. No network, no persistence.

    Includes basic duplicate-event suppression by event_id via a bounded
    LRU so agent code that relies on "duplicate events are the bus's
    problem" behaves the same locally as against Redis Streams.
    """

    def __init__(self, dedup_max_size: int = 10_000) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._dedup = EventDeduplicator(max_size=dedup_max_size)
        self._running = False

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._handlers[topic].append(handler)
        logger.debug("Subscribed handler to topic=%s", topic)

    def publish(self, event: EventEnvelope) -> None:
        if self._dedup.seen_before(event.event_id):
            logger.info("Dropping duplicate event_id=%s type=%s", event.event_id, event.event_type)
            return

        for handler in self._handlers.get(event.event_type, []):
            try:
                handler(event)
            except Exception:  # noqa: BLE001 - a bad handler must not crash the bus
                logger.exception(
                    "Handler failed for event_type=%s event_id=%s",
                    event.event_type,
                    event.event_id,
                )

    def start(self) -> None:
        self._running = True
        logger.info("InMemoryEventBus started")

    def stop(self) -> None:
        self._running = False
        logger.info("InMemoryEventBus stopped")


class RedisStreamsEventBus(EventBus):
    """
    Shared Redis Streams bus — all agents (and NestJS fan-out) use one stream.

    - publish → XADD
    - consume → XREADGROUP per agent consumer group + XACK after handlers run
    - On start, XAUTOCLAIM reclaim pending messages older than min_idle_ms
    """

    def __init__(
        self,
        redis_url: str,
        *,
        stream_key: str = "agent:events",
        consumer_group: str = "cg:default",
        consumer_name: str | None = None,
        block_ms: int = 2000,
        count: int = 16,
        min_idle_ms: int = 60_000,
    ) -> None:
        try:
            import redis as redis_lib
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "redis package is required for RedisStreamsEventBus — "
                "pip install redis"
            ) from exc

        self._redis_lib = redis_lib
        self._redis_url = redis_url
        self.stream_key = stream_key
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name or f"consumer-{int(time.time())}"
        self.block_ms = block_ms
        self.count = count
        self.min_idle_ms = min_idle_ms

        self._client = redis_lib.from_url(redis_url, decode_responses=True)
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._running = False
        self._thread: threading.Thread | None = None

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._handlers[topic].append(handler)
        logger.debug(
            "RedisStreams subscribed topic=%s group=%s",
            topic,
            self.consumer_group,
        )

    def publish(self, event: EventEnvelope) -> None:
        data = event.to_dict()
        # Redis stream fields must be strings; nest payload as JSON.
        fields = {
            "event_id": data["event_id"],
            "event_type": data["event_type"],
            "source_agent": data["source_agent"],
            "correlation_id": data["correlation_id"] or "",
            "occurred_at": data["occurred_at"],
            "schema_version": str(data["schema_version"]),
            "payload": json.dumps(data["payload"]),
        }
        self._client.xadd(self.stream_key, fields)
        logger.debug(
            "XADD stream=%s event_type=%s event_id=%s",
            self.stream_key,
            event.event_type,
            event.event_id,
        )

    def start(self) -> None:
        if self._running:
            return
        self._ensure_group()
        self._running = True
        self._thread = threading.Thread(
            target=self._consume_loop,
            name=f"redis-stream-{self.consumer_group}",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "RedisStreamsEventBus started stream=%s group=%s consumer=%s",
            self.stream_key,
            self.consumer_group,
            self.consumer_name,
        )

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=self.block_ms / 1000.0 + 1.0)
            self._thread = None
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            logger.exception("Error closing Redis client")
        logger.info("RedisStreamsEventBus stopped")

    def _ensure_group(self) -> None:
        try:
            self._client.xgroup_create(
                self.stream_key,
                self.consumer_group,
                id="0",
                mkstream=True,
            )
            logger.info(
                "Created consumer group=%s on stream=%s",
                self.consumer_group,
                self.stream_key,
            )
        except self._redis_lib.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def _consume_loop(self) -> None:
        self._reclaim_pending()
        while self._running:
            try:
                results = self._client.xreadgroup(
                    groupname=self.consumer_group,
                    consumername=self.consumer_name,
                    streams={self.stream_key: ">"},
                    count=self.count,
                    block=self.block_ms,
                )
            except Exception:  # noqa: BLE001
                logger.exception("XREADGROUP failed; sleeping briefly")
                time.sleep(1.0)
                continue

            if not results:
                continue

            for _stream, messages in results:
                for message_id, fields in messages:
                    self._dispatch_and_ack(message_id, fields)

    def _reclaim_pending(self) -> None:
        """XAUTOCLAIM stale pending messages for this consumer group."""
        try:
            # redis-py: xautoclaim(name, groupname, consumername, min_idle_time, start_id=...)
            result = self._client.xautoclaim(
                self.stream_key,
                self.consumer_group,
                self.consumer_name,
                min_idle_time=self.min_idle_ms,
                start_id="0-0",
                count=self.count,
            )
            # result: [next_start_id, [(id, fields), ...], deleted_ids?]
            if not result or len(result) < 2:
                return
            messages = result[1] or []
            for message_id, fields in messages:
                if fields:
                    self._dispatch_and_ack(message_id, fields)
        except Exception:  # noqa: BLE001
            # Older Redis without XAUTOCLAIM — non-fatal for sample.
            logger.debug("XAUTOCLAIM unavailable or failed", exc_info=True)

    def _dispatch_and_ack(self, message_id: str, fields: dict) -> None:
        try:
            event = EventEnvelope.from_dict(
                {
                    "event_id": fields.get("event_id"),
                    "event_type": fields.get("event_type"),
                    "source_agent": fields.get("source_agent", "unknown"),
                    "correlation_id": fields.get("correlation_id") or None,
                    "occurred_at": fields.get("occurred_at"),
                    "schema_version": fields.get("schema_version", 1),
                    "payload": fields.get("payload", "{}"),
                }
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to parse stream message_id=%s", message_id)
            self._client.xack(self.stream_key, self.consumer_group, message_id)
            return

        handlers = self._handlers.get(event.event_type, [])
        if not handlers:
            # Not subscribed — ACK so the group does not stall on foreign topics.
            self._client.xack(self.stream_key, self.consumer_group, message_id)
            return

        failed = False
        for handler in handlers:
            try:
                handler(event)
            except Exception:  # noqa: BLE001
                failed = True
                logger.exception(
                    "Handler failed event_type=%s event_id=%s message_id=%s",
                    event.event_type,
                    event.event_id,
                    message_id,
                )

        if not failed:
            self._client.xack(self.stream_key, self.consumer_group, message_id)
        else:
            # Leave pending for XAUTOCLAIM reclaim / retry by another consumer.
            logger.warning(
                "Leaving message_id=%s pending after handler failure",
                message_id,
            )


def _parse_redis_stream_url(event_bus_url: str) -> tuple[str, str]:
    """
    Parse redis://host:port/db?stream=agent:events into (redis_url, stream_key).
    """
    parsed = urlparse(event_bus_url)
    qs = parse_qs(parsed.query)
    stream_key = qs.get("stream", [None])[0] or "agent:events"
    # Strip query for redis client URL
    clean = event_bus_url.split("?", 1)[0]
    return clean, stream_key


def build_event_bus(
    event_bus_url: str,
    *,
    agent_name: str = "agent",
    stream_key: str | None = None,
    consumer_group: str | None = None,
    dedup_max_size: int = 10_000,
) -> EventBus:
    """
    Factory: turns config (a URL/scheme) into a concrete EventBus.

    Supported:
      memory://...  -> InMemoryEventBus
      redis://...   -> RedisStreamsEventBus
      rediss://...  -> RedisStreamsEventBus (TLS)
    """
    if event_bus_url.startswith("memory://"):
        return InMemoryEventBus(dedup_max_size=dedup_max_size)

    if event_bus_url.startswith("redis://") or event_bus_url.startswith("rediss://"):
        clean_url, url_stream = _parse_redis_stream_url(event_bus_url)
        return RedisStreamsEventBus(
            clean_url,
            stream_key=stream_key or url_stream,
            consumer_group=consumer_group or f"cg:{agent_name}",
            consumer_name=f"{agent_name}-1",
        )

    if event_bus_url.startswith("kafka://"):
        raise NotImplementedError(
            "Kafka adapter not implemented — use redis:// for the shared bus."
        )
    if event_bus_url.startswith("amqp://"):
        raise NotImplementedError(
            "RabbitMQ adapter not implemented — use redis:// for the shared bus."
        )
    if event_bus_url.startswith("sqs://"):
        raise NotImplementedError(
            "SQS adapter not implemented — use redis:// for the shared bus."
        )
    raise ValueError(f"Unknown event bus scheme: {event_bus_url}")
