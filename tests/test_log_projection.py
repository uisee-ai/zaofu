"""doc 82 §8.2 — log-row projection over events."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.log_projection import build_log_rows


def _ev(etype: str, *, eid: str = "e1", task_id: str | None = None,
        actor: str | None = None, correlation_id: str | None = None,
        payload: dict | None = None) -> ZfEvent:
    return ZfEvent(type=etype, id=eid, task_id=task_id, actor=actor,
                   correlation_id=correlation_id, payload=payload or {})


def test_level_inference():
    rows = build_log_rows([
        (1, _ev("task.dispatched", eid="e1")),
        (2, _ev("verify.failed", eid="e2")),
        (3, _ev("worker.stuck", eid="e3")),
        (4, _ev("human.escalate", eid="e4")),
    ])
    levels = {r["raw_event_ref"]: r["level"] for r in rows}
    assert levels["event:e1"] == "INFO"
    assert levels["event:e2"] == "ERROR"
    assert levels["event:e3"] == "WARN"
    assert levels["event:e4"] == "WARN"


def test_level_min_filter():
    events = [
        (1, _ev("task.dispatched", eid="e1")),
        (2, _ev("worker.stuck", eid="e2")),
        (3, _ev("verify.failed", eid="e3")),
    ]
    warn_up = build_log_rows(events, level_min="WARN")
    assert {r["raw_event_ref"] for r in warn_up} == {"event:e2", "event:e3"}
    err_only = build_log_rows(events, level_min="ERROR")
    assert {r["raw_event_ref"] for r in err_only} == {"event:e3"}


def test_task_role_trace_filters():
    events = [
        (1, _ev("a.b", eid="e1", task_id="T-1", actor="dev", correlation_id="tr-a")),
        (2, _ev("a.b", eid="e2", task_id="T-2", actor="test", correlation_id="tr-b")),
    ]
    assert [r["task_id"] for r in build_log_rows(events, task_id="T-1")] == ["T-1"]
    assert [r["role"] for r in build_log_rows(events, role="test")] == ["test"]
    assert [r["trace_id"] for r in build_log_rows(events, trace_id="tr-b")] == ["tr-b"]


def test_newest_first_and_limit():
    events = [(i, _ev("a.b", eid=f"e{i}")) for i in range(1, 6)]
    rows = build_log_rows(events, limit=2)
    assert [r["raw_event_ref"] for r in rows] == ["event:e5", "event:e4"]


def test_message_prefers_payload_then_falls_back_to_type():
    rows = build_log_rows([
        (1, _ev("verify.failed", eid="e1",
                payload={"reason": "pytest failed, 2 failing tests"})),
        (2, _ev("task.dispatched", eid="e2")),
    ])
    by_ref = {r["raw_event_ref"]: r for r in rows}
    assert by_ref["event:e1"]["message"] == "pytest failed, 2 failing tests"
    assert by_ref["event:e2"]["message"] == "task.dispatched"


def test_attrs_pick_known_keys_only():
    rows = build_log_rows([
        (1, _ev("verify.failed", eid="e1", payload={
            "exit_code": 1, "stage_id": "verify", "huge_blob": "x" * 50})),
    ])
    assert rows[0]["attrs"] == {"exit_code": 1, "stage_id": "verify"}
