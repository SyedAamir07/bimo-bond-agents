"""
ML anomaly detection for stream health -- an advisory early-warning
layer on top of live_streaming_agent's deterministic reconnect policy.

Uses scikit-learn's IsolationForest (unsupervised) to score how
"normal" a live session's behavior looks, based on heartbeat-count-
based rate features (interruptions per heartbeat, reconnect attempts
per heartbeat -- see session_store.StreamSession.feature_vector() for
why these specific rates, not raw counts or elapsed/wall-clock time,
are used; two real bugs were found and fixed there via manual live
testing). Unlike the fixed thresholds the rest of this agent uses (N
reconnect attempts, M seconds of silence), this model *learns what
normal looks like* from the sessions this agent has actually observed,
and can flag a session that doesn't fit that pattern even when no
single fixed threshold has been crossed yet.

Why IsolationForest specifically: it needs no labeled data (there is
no "this session was bad" ground truth to train on), it's fast to
train/predict on small tabular feature vectors, and its output
(anomaly score) is a single interpretable number rather than a
black-box classification -- important for something that gates an
early-warning signal in production.

This is advisory only, same principle live_auction_agent follows for
money-moving decisions: the anomaly score publishes
`stream.health.anomaly_detected` as a signal, it never itself ends a
session or blocks/forces a reconnect -- that stays on the fixed rules
in agent.py, which run identically whether or not the model is
trained yet.

Training samples are durably persisted (see TrainingSampleStore below)
so an agent restart doesn't lose accumulated training data and have to
start the cold-start climb back to min_training_samples from zero. The
fitted sklearn model itself is NOT persisted -- refitting from the
persisted raw samples on startup is fast (milliseconds at the sample
counts this agent trains on) and avoids pickling a model object across
process/version boundaries.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from foundation import TaskStore

logger = logging.getLogger(__name__)

FEATURE_NAMES = (
    "interruptions_per_heartbeat",
    "reconnect_attempts_per_heartbeat",
)


@dataclass
class AnomalyPrediction:
    is_anomaly: bool
    score: float  # lower = more anomalous (raw IsolationForest decision_function output)
    model_ready: bool  # False when falling back to the fixed-rule floor (not enough training data yet)
    reason: str


class TrainingSampleStore:
    """
    Durable list of feature vectors, backed by a single foundation
    TaskStore record (same Redis/InMemory split every other agent's
    store uses -- see session_store.py, ledger_store.py for the same
    pattern). All samples are kept as one JSON-encoded list under one
    key rather than one record per sample: the sample count this agent
    trains on (tens, not millions) makes a single read/write far
    simpler than paginating, and TaskStore's `action` field (a plain
    string) is exactly wide enough for that JSON blob.
    """

    _RECORD_ID = "samples"

    def __init__(self, store: "TaskStore") -> None:
        self._store = store

    def load(self) -> list[list[float]]:
        record = self._store.get(self._RECORD_ID)
        if record is None:
            return []
        try:
            data = json.loads(record.action)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Corrupt training-sample record -- starting fresh")
            return []
        return data if isinstance(data, list) else []

    def save(self, samples: list[list[float]]) -> None:
        from foundation import TaskRecord

        self._store.save(
            TaskRecord(
                task_id=self._RECORD_ID,
                action=json.dumps(samples),
                target_agent="live_streaming_agent",
                status="completed",
            )
        )


class StreamAnomalyModel:
    """
    Wraps an IsolationForest with the training-data bookkeeping and the
    fallback policy this agent needs. Not thread-safe across processes
    (each agent instance owns its own model in memory) -- retraining
    uses an in-process lock since Redis/multi-process model sharing is
    out of scope for this pilot. Training SAMPLES are durably persisted
    via TrainingSampleStore when one is supplied; the fitted model
    itself always starts in-memory-only and is rebuilt from those
    samples (see fit-on-load in agent construction).
    """

    def __init__(
        self,
        *,
        min_training_samples: int,
        retrain_interval_samples: int,
        contamination: str | float = "auto",
        sample_store: "TrainingSampleStore | None" = None,
    ) -> None:
        self._min_training_samples = max(2, min_training_samples)
        self._retrain_interval_samples = max(1, retrain_interval_samples)
        self._contamination = contamination
        self._sample_store = sample_store
        self._samples: list[list[float]] = list(sample_store.load()) if sample_store else []
        self._model = None  # sklearn.ensemble.IsolationForest, built lazily
        self._samples_since_last_fit = 0
        self._lock = threading.Lock()

        # If restart-recovered samples already clear the training bar,
        # fit immediately instead of waiting for the next completed
        # session -- otherwise a freshly-restarted agent would serve
        # zero predictions until one more session finished, despite
        # already having enough history to predict from.
        if len(self._samples) >= self._min_training_samples:
            with self._lock:
                self._fit_locked()

    @property
    def is_trained(self) -> bool:
        return self._model is not None

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    def record_completed_session(self, features: list[float]) -> None:
        """
        Feed one completed session's feature vector into the training
        set and retrain if enough new samples have accumulated. Called
        only for ENDED sessions -- a session mid-flight doesn't yet have
        a stable feature vector to learn from.

        Persists the updated sample list durably (when a sample_store
        was supplied) before fitting, so a crash between the append and
        the fit still keeps the sample -- retraining is cheap to redo,
        losing accumulated history is not.
        """
        with self._lock:
            self._samples.append(features)
            self._samples_since_last_fit += 1

            if self._sample_store is not None:
                self._sample_store.save(self._samples)

            should_fit = (
                len(self._samples) >= self._min_training_samples
                and (
                    self._model is None
                    or self._samples_since_last_fit >= self._retrain_interval_samples
                )
            )
            if should_fit:
                self._fit_locked()

    def _fit_locked(self) -> None:
        """Caller must hold self._lock."""
        try:
            from sklearn.ensemble import IsolationForest
        except ImportError:
            logger.warning("scikit-learn not installed -- anomaly model stays untrained")
            return

        model = IsolationForest(
            contamination=self._contamination,
            random_state=42,
            n_estimators=100,
        )
        model.fit(self._samples)
        self._model = model
        self._samples_since_last_fit = 0
        logger.info(
            "Retrained IsolationForest on %d samples (contamination=%s)",
            len(self._samples), self._contamination,
        )

    def predict(self, features: list[float]) -> AnomalyPrediction:
        """
        Score one in-flight session's current feature vector.

        Fails to a conservative fixed-rule floor (never anomalous)
        rather than raising or guessing when the model isn't trained
        yet -- an untrained model has no basis to call anything
        anomalous, and treating "no model" as "everything is anomalous"
        would flood the early-warning channel with noise.
        """
        with self._lock:
            model = self._model

        if model is None:
            return AnomalyPrediction(
                is_anomaly=False,
                score=0.0,
                model_ready=False,
                reason=f"model_not_trained (have {self.sample_count}/{self._min_training_samples} samples)",
            )

        # decision_function: higher = more normal, lower/negative = more
        # anomalous. predict: 1 = normal, -1 = anomaly (per contamination
        # threshold fit during training).
        score = float(model.decision_function([features])[0])
        label = int(model.predict([features])[0])
        return AnomalyPrediction(
            is_anomaly=(label == -1),
            score=score,
            model_ready=True,
            reason="isolation_forest_prediction",
        )
