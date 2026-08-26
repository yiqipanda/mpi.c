from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from prototype.main import Main
from prototype.trace import Trace

FIXED_TIME = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def test_record_uses_injected_clock_and_preserves_event_shape() -> None:
    trace = Trace(clock=lambda: FIXED_TIME)
    event = trace.record("worker", "started", index=2)
    assert event == {
        "timestamp": "2026-01-02T03:04:05+00:00",
        "event": "worker",
        "message": "started",
        "details": {"index": 2},
    }
    assert trace.snapshot() == {"entries": [event]}


def test_disabled_trace_returns_event_without_storing_it() -> None:
    trace = Trace(enabled=False, clock=lambda: FIXED_TIME)
    event = trace.record("worker", "not stored")
    assert event["timestamp"] == "2026-01-02T03:04:05+00:00"
    assert trace.snapshot() == {"entries": []}


def test_snapshot_has_a_separate_entry_list() -> None:
    trace = Trace(clock=lambda: FIXED_TIME)
    trace.record("program", "started")
    snapshot = trace.snapshot()
    snapshot["entries"].clear()
    assert len(trace.entries) == 1


def test_persist_if_configured_is_noop_without_path() -> None:
    trace = Trace(path=None)
    trace.record("program", "event")
    assert trace.persist_if_configured() is None


def test_persist_if_configured_writes_compatible_json(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "trace.json"
    trace = Trace(path=target, clock=lambda: FIXED_TIME)
    trace.record("program", "event", value=1)
    assert trace.persist_if_configured() == target
    assert json.loads(target.read_text(encoding="utf-8")) == trace.to_dict()


def test_persistence_failure_is_propagated(tmp_path: Path) -> None:
    trace = Trace(path=tmp_path)
    with pytest.raises(IsADirectoryError):
        trace.persist_if_configured()


def test_main_uses_trace_facade_not_path_or_dump_details() -> None:
    source = inspect.getsource(Main.poll)
    assert "trace.path" not in source
    assert "trace.dump" not in source
    assert "trace.persist_if_configured" in source
    assert "trace.snapshot" in source
