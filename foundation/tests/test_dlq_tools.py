"""
Unit tests for DLQ list/replay/purge against a fake Redis client, so
these run without a real Redis instance (same spirit as InMemoryEventBus
covering RedisStreamsEventBus's shape without needing infra).
"""
from __future__ import annotations

import json
import time
from itertools import count

import pytest

from foundation import dlq_tools


class _FakeRedisClient:
    """Minimal XADD/XRANGE/XDEL fake sufficient for dlq_tools."""

    def __init__(self) -> None:
        self._streams: dict[str, list[tuple[str, dict]]] = {}
        self._id_counter = count(1)

    def xadd(self, stream_key: str, fields: dict, **kwargs) -> str:
        message_id = f"{next(self._id_counter)}-0"
        self._streams.setdefault(stream_key, []).append((message_id, dict(fields)))
        return message_id

    def xrange(self, stream_key: str, min: str = "-", max: str = "+", count: int = 100):  # noqa: A002
        entries = self._streams.get(stream_key, [])
        return entries[:count]

    def xdel(self, stream_key: str, message_id: str) -> int:
        entries = self._streams.get(stream_key, [])
        before = len(entries)
        self._streams[stream_key] = [e for e in entries if e[0] != message_id]
        return before - len(self._streams[stream_key])


@pytest.fixture()
def fake_client(monkeypatch):
    client = _FakeRedisClient()

    def _get_client(redis_url: str):
        return client

    monkeypatch.setattr(dlq_tools, "_get_client", _get_client)
    return client


def _seed_dlq_entry(
    client: _FakeRedisClient,
    *,
    event_id: str,
    event_type: str = "gift.sent",
    dead_lettered_at: str | None = None,
    dlq_stream: str = "agent:events:dlq",
) -> None:
    client.xadd(
        dlq_stream,
        {
            "event_id": event_id,
            "event_type": event_type,
            "source_agent": "gift_effects_agent",
            "correlation_id": "",
            "original_stream": "agent:events",
            "consumer_group": "cg:gift_effects_agent",
            "delivery_count": "6",
            "dead_lettered_at": dead_lettered_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "payload": json.dumps({"gift_id": "g1"}),
        },
    )


def test_list_dlq_returns_parsed_entries(fake_client):
    _seed_dlq_entry(fake_client, event_id="e1")
    entries = dlq_tools.list_dlq("redis://fake")

    assert len(entries) == 1
    assert entries[0].event_id == "e1"
    assert entries[0].payload == {"gift_id": "g1"}


def test_list_dlq_empty_stream_returns_empty_list(fake_client):
    assert dlq_tools.list_dlq("redis://fake") == []


def test_replay_dlq_moves_entry_back_to_original_stream(fake_client):
    _seed_dlq_entry(fake_client, event_id="e1")

    replayed = dlq_tools.replay_dlq("redis://fake", limit=10)

    assert replayed == ["e1"]
    assert dlq_tools.list_dlq("redis://fake") == []  # removed from DLQ
    assert len(fake_client._streams["agent:events"]) == 1  # landed on live stream


def test_replay_dlq_dry_run_does_not_mutate_streams(fake_client):
    _seed_dlq_entry(fake_client, event_id="e1")

    replayed = dlq_tools.replay_dlq("redis://fake", limit=10, dry_run=True)

    assert replayed == ["e1"]
    assert len(dlq_tools.list_dlq("redis://fake")) == 1  # still in DLQ
    assert "agent:events" not in fake_client._streams  # nothing published


def test_replay_dlq_filters_by_event_type(fake_client):
    _seed_dlq_entry(fake_client, event_id="e1", event_type="gift.sent")
    _seed_dlq_entry(fake_client, event_id="e2", event_type="liveGift")

    replayed = dlq_tools.replay_dlq("redis://fake", limit=10, event_type_filter="gift.sent")

    assert replayed == ["e1"]
    remaining = {e.event_id for e in dlq_tools.list_dlq("redis://fake")}
    assert remaining == {"e2"}


def test_purge_dlq_removes_old_entries_only(fake_client):
    old_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60 * 86400))
    _seed_dlq_entry(fake_client, event_id="old", dead_lettered_at=old_ts)
    _seed_dlq_entry(fake_client, event_id="new")

    purged = dlq_tools.purge_dlq("redis://fake", older_than_days=30)

    assert purged == 1
    remaining = {e.event_id for e in dlq_tools.list_dlq("redis://fake")}
    assert remaining == {"new"}
