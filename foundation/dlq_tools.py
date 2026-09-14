"""
DLQ inspection and replay for the shared Redis Streams event bus.

Poison messages land on `agent:events:dlq` (RedisStreamsEventBus._dead_letter)
after exceeding AGENT_MAX_DELIVERIES, and today nothing ever reads that
stream back — it is a write-only graveyard. This module is the missing
half: list what's there, and replay entries back onto the main event
stream (either to retry, or after a fix has shipped).

CLI usage:
    python -m foundation.dlq_tools list --redis-url redis://localhost:6379
    python -m foundation.dlq_tools replay --redis-url redis://localhost:6379 --limit 50
    python -m foundation.dlq_tools purge --redis-url redis://localhost:6379 --older-than-days 30
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeadLetterEntry:
    dlq_message_id: str
    event_id: str
    event_type: str
    source_agent: str
    original_stream: str
    consumer_group: str
    delivery_count: int
    dead_lettered_at: str
    payload: dict[str, Any]
    correlation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "dlq_message_id": self.dlq_message_id,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source_agent": self.source_agent,
            "original_stream": self.original_stream,
            "consumer_group": self.consumer_group,
            "delivery_count": self.delivery_count,
            "dead_lettered_at": self.dead_lettered_at,
            "correlation_id": self.correlation_id,
            "payload": self.payload,
        }


def _get_client(redis_url: str):
    try:
        import redis as redis_lib
    except ImportError as exc:  # pragma: no cover
        raise ImportError("redis package is required — pip install redis") from exc
    return redis_lib.from_url(redis_url, decode_responses=True)


def _parse_entry(message_id: str, fields: dict[str, str]) -> DeadLetterEntry:
    payload_raw = fields.get("payload", "{}")
    try:
        payload = json.loads(payload_raw) if payload_raw else {}
    except json.JSONDecodeError:
        payload = {"_unparsed_payload": payload_raw}
    return DeadLetterEntry(
        dlq_message_id=message_id,
        event_id=fields.get("event_id", ""),
        event_type=fields.get("event_type", ""),
        source_agent=fields.get("source_agent", ""),
        original_stream=fields.get("original_stream", "agent:events"),
        consumer_group=fields.get("consumer_group", ""),
        delivery_count=int(fields.get("delivery_count", "0") or 0),
        dead_lettered_at=fields.get("dead_lettered_at", ""),
        payload=payload,
        correlation_id=fields.get("correlation_id") or None,
    )


def list_dlq(
    redis_url: str,
    *,
    dlq_stream_key: str = "agent:events:dlq",
    count: int = 100,
) -> list[DeadLetterEntry]:
    """Return up to `count` dead-lettered entries, oldest first."""
    client = _get_client(redis_url)
    raw = client.xrange(dlq_stream_key, min="-", max="+", count=count)
    return [_parse_entry(message_id, fields) for message_id, fields in raw]


def replay_dlq(
    redis_url: str,
    *,
    dlq_stream_key: str = "agent:events:dlq",
    target_stream_key: str | None = None,
    limit: int = 100,
    event_type_filter: str | None = None,
    dry_run: bool = False,
) -> list[str]:
    """
    Re-publish dead-lettered events onto their original stream (or
    `target_stream_key` if given) and remove them from the DLQ on
    success. Returns the list of event_ids that were replayed.

    Use this only after the root cause of the poison messages has been
    fixed (bad handler, bug, etc.) — replaying without a fix just moves
    the same failures back onto the live stream.
    """
    client = _get_client(redis_url)
    entries = list_dlq(redis_url, dlq_stream_key=dlq_stream_key, count=limit)

    replayed: list[str] = []
    for entry in entries:
        if event_type_filter and entry.event_type != event_type_filter:
            continue

        destination = target_stream_key or entry.original_stream
        if dry_run:
            logger.info(
                "[dry-run] would replay event_id=%s event_type=%s -> stream=%s",
                entry.event_id, entry.event_type, destination,
            )
            replayed.append(entry.event_id)
            continue

        fields = {
            "event_id": entry.event_id,
            "event_type": entry.event_type,
            "source_agent": entry.source_agent,
            "correlation_id": entry.correlation_id or "",
            "occurred_at": entry.dead_lettered_at,
            "schema_version": "1",
            "payload": json.dumps(entry.payload),
        }
        client.xadd(destination, fields)
        client.xdel(dlq_stream_key, entry.dlq_message_id)
        replayed.append(entry.event_id)
        logger.info(
            "Replayed event_id=%s event_type=%s -> stream=%s",
            entry.event_id, entry.event_type, destination,
        )

    return replayed


def purge_dlq(
    redis_url: str,
    *,
    dlq_stream_key: str = "agent:events:dlq",
    older_than_days: float = 30.0,
    dry_run: bool = False,
) -> int:
    """
    Delete DLQ entries older than `older_than_days` (matches the
    `audit_record`-style long-tail cleanup other retention policies use).
    Returns the number of entries purged.
    """
    client = _get_client(redis_url)
    cutoff = time.time() - (older_than_days * 86400)
    entries = list_dlq(redis_url, dlq_stream_key=dlq_stream_key, count=10_000)

    purged = 0
    for entry in entries:
        try:
            entry_ts = time.mktime(time.strptime(entry.dead_lettered_at, "%Y-%m-%dT%H:%M:%SZ"))
        except (ValueError, TypeError):
            continue
        if entry_ts >= cutoff:
            continue
        if not dry_run:
            client.xdel(dlq_stream_key, entry.dlq_message_id)
        purged += 1

    return purged


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect and replay the shared Redis DLQ stream.")
    parser.add_argument("--redis-url", required=True)
    parser.add_argument("--dlq-stream", default="agent:events:dlq")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="Print DLQ entries as JSON lines.")
    p_list.add_argument("--count", type=int, default=100)

    p_replay = sub.add_parser("replay", help="Re-publish DLQ entries onto the live stream.")
    p_replay.add_argument("--limit", type=int, default=100)
    p_replay.add_argument("--target-stream", default=None)
    p_replay.add_argument("--event-type", default=None)
    p_replay.add_argument("--dry-run", action="store_true")

    p_purge = sub.add_parser("purge", help="Delete DLQ entries older than N days.")
    p_purge.add_argument("--older-than-days", type=float, default=30.0)
    p_purge.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.command == "list":
        entries = list_dlq(args.redis_url, dlq_stream_key=args.dlq_stream, count=args.count)
        for entry in entries:
            print(json.dumps(entry.to_dict()))
        print(f"# {len(entries)} entries", file=sys.stderr)
        return 0

    if args.command == "replay":
        replayed = replay_dlq(
            args.redis_url,
            dlq_stream_key=args.dlq_stream,
            target_stream_key=args.target_stream,
            limit=args.limit,
            event_type_filter=args.event_type,
            dry_run=args.dry_run,
        )
        print(f"Replayed {len(replayed)} events", file=sys.stderr)
        return 0

    if args.command == "purge":
        purged = purge_dlq(
            args.redis_url,
            dlq_stream_key=args.dlq_stream,
            older_than_days=args.older_than_days,
            dry_run=args.dry_run,
        )
        print(f"Purged {purged} entries", file=sys.stderr)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
