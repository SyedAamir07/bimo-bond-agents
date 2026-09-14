"""Unit tests for the audit sink wiring (AuditLog -> AuditSink)."""
from __future__ import annotations

from foundation.audit import AuditLog
from foundation.audit_sink import AuditSink, NullAuditSink, build_audit_sink


class _RecordingSink(AuditSink):
    def __init__(self) -> None:
        self.written: list[dict] = []

    def write(self, record: dict) -> None:
        self.written.append(record)


def test_audit_log_without_sink_still_keeps_ring_buffer():
    log = AuditLog("agent-a")
    log.record("thing.happened", detail={"x": 1})
    assert len(log.recent()) == 1


def test_audit_log_mirrors_to_sink_when_present():
    sink = _RecordingSink()
    log = AuditLog("agent-a", sink=sink)
    log.record("thing.happened", detail={"x": 1})

    assert len(sink.written) == 1
    assert sink.written[0]["action"] == "thing.happened"


def test_audit_log_sink_receives_sanitized_detail():
    sink = _RecordingSink()
    log = AuditLog("agent-a", sink=sink)
    log.record("login", detail={"password": "hunter2", "user": "bob"})

    assert sink.written[0]["detail"]["password"] == "[redacted]"
    assert sink.written[0]["detail"]["user"] == "bob"


def test_build_audit_sink_memory_url_returns_null_sink():
    sink = build_audit_sink("memory://local")
    assert isinstance(sink, NullAuditSink)


def test_build_audit_sink_empty_url_returns_null_sink():
    sink = build_audit_sink("")
    assert isinstance(sink, NullAuditSink)


def test_null_sink_write_never_raises():
    sink = NullAuditSink()
    sink.write({"anything": "goes"})  # should not raise
