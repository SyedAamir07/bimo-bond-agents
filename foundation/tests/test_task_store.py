"""Unit tests for the durable task store (InMemoryTaskStore)."""
from __future__ import annotations

from foundation.task_store import InMemoryTaskStore, TaskRecord, build_task_store


def test_save_and_get_round_trip():
    store = InMemoryTaskStore()
    record = TaskRecord(task_id="t1", action="do_thing", target_agent="worker", status="dispatched")
    store.save(record)

    fetched = store.get("t1")
    assert fetched is not None
    assert fetched.task_id == "t1"
    assert fetched.status == "dispatched"


def test_get_missing_returns_none():
    store = InMemoryTaskStore()
    assert store.get("missing") is None


def test_all_dispatched_filters_by_status():
    store = InMemoryTaskStore()
    store.save(TaskRecord(task_id="a", action="x", target_agent="w", status="dispatched"))
    store.save(TaskRecord(task_id="b", action="x", target_agent="w", status="completed"))
    store.save(TaskRecord(task_id="c", action="x", target_agent="w", status="dispatched"))

    dispatched_ids = {r.task_id for r in store.all_dispatched()}
    assert dispatched_ids == {"a", "c"}


def test_status_transition_removes_from_dispatched():
    store = InMemoryTaskStore()
    record = TaskRecord(task_id="a", action="x", target_agent="w", status="dispatched")
    store.save(record)
    assert len(list(store.all_dispatched())) == 1

    record.status = "completed"
    store.save(record)
    assert len(list(store.all_dispatched())) == 0


def test_delete_removes_record():
    store = InMemoryTaskStore()
    store.save(TaskRecord(task_id="a", action="x", target_agent="w"))
    store.delete("a")
    assert store.get("a") is None


def test_build_task_store_memory_url_returns_in_memory():
    store = build_task_store("memory://local")
    assert isinstance(store, InMemoryTaskStore)


def test_task_record_round_trips_through_dict():
    record = TaskRecord(task_id="a", action="x", target_agent="w", status="dispatched", attempts=2, last_error="boom")
    restored = TaskRecord.from_dict(record.to_dict())
    assert restored == record
