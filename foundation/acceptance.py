"""
Measurable acceptance criteria with baselines and targets.

Doc: approve targets after establishing a baseline, and track each
agent's cost and response time. Contract strings alone are not enough —
this module holds numeric targets and evaluates samples.
"""
from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Direction = Literal["lower_is_better", "higher_is_better"]


@dataclass(frozen=True)
class AcceptanceTarget:
    metric: str
    unit: str
    direction: Direction
    baseline: float | None = None
    target: float | None = None
    description: str = ""

    def evaluate(self, value: float) -> str:
        """
        Return: meeting | missing_baseline | missing_target | below_target | above_target
        """
        if self.baseline is None:
            return "missing_baseline"
        if self.target is None:
            return "missing_target"
        if self.direction == "lower_is_better":
            return "meeting" if value <= self.target else "above_target"
        return "meeting" if value >= self.target else "below_target"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Shared pilot targets — product must approve before treating as SLOs.
PILOT_TARGETS: dict[str, AcceptanceTarget] = {
    "stream_startup_ms": AcceptanceTarget(
        metric="stream_startup_ms",
        unit="ms",
        direction="lower_is_better",
        baseline=2500.0,
        target=2000.0,
        description="Time from stream.started intent to first healthy heartbeat",
    ),
    "stream_interruption_rate": AcceptanceTarget(
        metric="stream_interruption_rate",
        unit="ratio",
        direction="lower_is_better",
        baseline=0.05,
        target=0.02,
        description="Interruptions per started session",
    ),
    "stream_reconnect_success_rate": AcceptanceTarget(
        metric="stream_reconnect_success_rate",
        unit="ratio",
        direction="higher_is_better",
        baseline=0.85,
        target=0.95,
        description="Successful reconnects / reconnect attempts",
    ),
    "gift_effect_latency_ms": AcceptanceTarget(
        metric="gift_effect_latency_ms",
        unit="ms",
        direction="lower_is_better",
        baseline=400.0,
        target=250.0,
        description="Event receive → effect.triggered publish",
    ),
    "gift_duplicate_or_missing_rate": AcceptanceTarget(
        metric="gift_duplicate_or_missing_rate",
        unit="ratio",
        direction="lower_is_better",
        baseline=0.01,
        target=0.001,
        description="Duplicate drops + missing effects / gifts received",
    ),
    "handler_latency_ms": AcceptanceTarget(
        metric="handler_latency_ms",
        unit="ms",
        direction="lower_is_better",
        baseline=100.0,
        target=50.0,
        description="Generic BaseAgent handler latency",
    ),
    "agent_cost_per_event": AcceptanceTarget(
        metric="agent_cost_per_event",
        unit="usd",
        direction="lower_is_better",
        baseline=None,
        target=None,
        description="Fill after cost baseline exists",
    ),
}


@dataclass
class AcceptanceTracker:
    """Collect samples and report against registered targets."""

    targets: dict[str, AcceptanceTarget] = field(
        default_factory=lambda: dict(PILOT_TARGETS)
    )

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._samples: dict[str, list[float]] = {k: [] for k in self.targets}
        self._max_samples = 500

    def observe(self, metric: str, value: float) -> str | None:
        """Record a sample; return evaluation status if a target exists."""
        with self._lock:
            if metric not in self._samples:
                self._samples[metric] = []
            bucket = self._samples[metric]
            bucket.append(float(value))
            if len(bucket) > self._max_samples:
                del bucket[: len(bucket) - self._max_samples]
        target = self.targets.get(metric)
        if target is None:
            return None
        return target.evaluate(float(value))

    def mean(self, metric: str) -> float | None:
        with self._lock:
            values = list(self._samples.get(metric, []))
        if not values:
            return None
        return sum(values) / len(values)

    def report(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        with self._lock:
            metrics = list(self.targets.keys()) + [
                m for m in self._samples if m not in self.targets
            ]
            for metric in metrics:
                values = list(self._samples.get(metric, []))
                target = self.targets.get(metric)
                mean = sum(values) / len(values) if values else None
                status = (
                    target.evaluate(mean)
                    if target is not None and mean is not None
                    else ("no_samples" if not values else "untracked")
                )
                out[metric] = {
                    "samples": len(values),
                    "mean": mean,
                    "target": target.to_dict() if target else None,
                    "status": status,
                }
        return out
