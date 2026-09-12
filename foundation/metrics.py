"""
In-process metrics for acceptance-criteria style observability.

Exposed as Prometheus text via GET /metrics on the health server.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from typing import Iterator


class AgentMetrics:
    """Thread-safe counters and latency sums keyed by metric name."""

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name
        self._lock = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._latency_sum_ms: dict[str, float] = defaultdict(float)
        self._latency_count: dict[str, int] = defaultdict(int)

    def incr(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] += amount

    def observe_ms(self, name: str, duration_ms: float) -> None:
        with self._lock:
            self._latency_sum_ms[name] += duration_ms
            self._latency_count[name] += 1

    @contextmanager
    def timer(self, name: str) -> Iterator[dict[str, float]]:
        """Yield a dict that receives `ms` after the block finishes."""
        start = time.perf_counter()
        holder: dict[str, float] = {}
        try:
            yield holder
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            holder["ms"] = elapsed_ms
            self.observe_ms(name, elapsed_ms)

    def to_prometheus(self) -> str:
        lines: list[str] = []
        agent = self.agent_name.replace('"', '\\"')
        with self._lock:
            for name, value in sorted(self._counters.items()):
                lines.append(f"# TYPE agent_{name} counter")
                lines.append(f'agent_{name}{{agent="{agent}"}} {value}')
            for name, total in sorted(self._latency_sum_ms.items()):
                count = self._latency_count[name]
                lines.append(f"# TYPE agent_{name}_ms summary")
                lines.append(f'agent_{name}_ms_sum{{agent="{agent}"}} {total:.3f}')
                lines.append(f'agent_{name}_ms_count{{agent="{agent}"}} {count}')
        lines.append("")
        return "\n".join(lines)
