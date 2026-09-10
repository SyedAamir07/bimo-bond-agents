"""
Foundation-level delivery helpers: bounded event-id dedup and handler retry.

Orchestration-specific task routing stays in the orchestration agent;
these helpers are generic and shared by every BaseAgent.
"""
from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class EventDeduplicator:
    """Bounded LRU set of seen event_ids (at-least-once safe locally)."""

    def __init__(self, max_size: int = 10_000) -> None:
        self._max_size = max(1, max_size)
        self._seen: OrderedDict[str, None] = OrderedDict()

    def seen_before(self, event_id: str) -> bool:
        if event_id in self._seen:
            self._seen.move_to_end(event_id)
            return True
        self._seen[event_id] = None
        while len(self._seen) > self._max_size:
            self._seen.popitem(last=False)
        return False

    def __contains__(self, event_id: str) -> bool:
        return event_id in self._seen

    def __len__(self) -> int:
        return len(self._seen)


@dataclass
class RetryPolicy:
    """Simple exponential backoff for handler execution."""

    max_attempts: int = 3
    base_delay_seconds: float = 0.05
    max_delay_seconds: float = 2.0

    def run(self, fn: Callable[[], T], *, on_attempt_fail: Callable[[int, BaseException], None] | None = None) -> T:
        last_exc: BaseException | None = None
        attempts = max(1, self.max_attempts)
        for attempt in range(1, attempts + 1):
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if on_attempt_fail is not None:
                    on_attempt_fail(attempt, exc)
                if attempt >= attempts:
                    break
                delay = min(
                    self.max_delay_seconds,
                    self.base_delay_seconds * (2 ** (attempt - 1)),
                )
                logger.debug("Retry attempt=%s sleeping=%.3fs", attempt, delay)
                time.sleep(delay)
        assert last_exc is not None
        raise last_exc
