"""
Durable gift-effect trigger history.

BaseAgent's own EventDeduplicator (foundation/reliability.py) already
drops a redelivered event_id within one process lifetime, but it's an
in-memory LRU -- a restart forgets everything. The project doc's
acceptance criteria for gifts explicitly calls out "duplicate or
missing events" as a measured metric (M-02), so this agent keeps a
durable second layer keyed on event_id: a restart mid-stream must not
re-trigger an effect for an event it already handled.

This intentionally does NOT dedup on transactionId or gift_id+session:
each tap in a combo burst is its own gift.sent / liveGiftCombo event
with its own transactionId (the backend creates one GiftTransaction row
per send call, not per combo), and each one is meant to trigger its own
effect -- the combo's `comboCount` field tells the client/overlay how
to render escalating combo visuals, it is not a signal to suppress
repeated animations.

Reuses foundation.task_store's Redis/InMemory split the same way the
other agents' stores do (session_store, ledger_store).
"""
from __future__ import annotations

import time

from foundation import TaskRecord, TaskStore, build_task_store


class TriggerStore:
    """Durable event_id -> "already triggered" record."""

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    def was_triggered(self, event_id: str) -> bool:
        return self._store.get(event_id) is not None

    def mark_triggered(self, event_id: str, *, effect: str) -> None:
        self._store.save(
            TaskRecord(
                task_id=event_id,
                action=effect,
                target_agent="gift_effects_agent",
                status="completed",
                dispatched_at=time.time(),
            )
        )


def build_trigger_store(event_bus_url: str, *, key_prefix: str = "gift_effects:triggers") -> TriggerStore:
    return TriggerStore(build_task_store(event_bus_url, key_prefix=key_prefix))
