"""Tests for workflow-run.v1 — fanout/workflow run aggregation (doc 68 S1)."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.workflow_run import build_workflow_run


def _ev(seq, type_, payload=None, *, correlation_id=None, task_id=None):
    return (seq, ZfEvent(type=type_, id=f"e{seq}", payload=payload or {},
                         correlation_id=correlation_id, task_id=task_id))


def _started(fid="F1", children=("c1", "c2"), trace="tr1"):
    return _ev(2, "fanout.started", {
        "fanout_id": fid, "trace_id": trace, "stage_id": "review",
        "topology": "fanout_reader", "target_ref": "T1",
        "pdd_id": "F-1", "feature_id": "F-1",
        "task_map_ref": ".zf/artifacts/F-1/task-map.json",
        "source_index_ref": ".zf/artifacts/F-1/source-index.json",
        "expected_children": [{"child_id": c, "role_instance": f"review-{c}"} for c in children],
    }, correlation_id=trace)


def test_aggregates_running_fanout():
    events = [
        _ev(1, "fanout.requested", {"fanout_id": "F1", "trace_id": "tr1"}, correlation_id="tr1"),
        _started(),
        _ev(3, "fanout.child.dispatched", {"fanout_id": "F1", "child_id": "c1", "role_instance": "review-c1"}),
        _ev(4, "fanout.child.dispatched", {"fanout_id": "F1", "child_id": "c2", "role_instance": "review-c2"}),
    ]
    run = build_workflow_run(fanout_id="F1", events=events)
    assert run["schema_version"] == "workflow-run.v1"
    assert run["fanout_id"] == "F1"
    assert run["topology"] == "fanout_reader"
    assert run["pdd_id"] == "F-1"
    assert run["feature_id"] == "F-1"
    assert run["task_map_ref"] == ".zf/artifacts/F-1/task-map.json"
    assert run["source_index_ref"] == ".zf/artifacts/F-1/source-index.json"
    assert run["status"] == "running"
    # both children launched
    launched = {o["child_id"]: o["dispatched"] for o in run["launch_outcomes"]}
    assert launched == {"c1": True, "c2": True}
    # none executed yet
    assert run["execution_outcomes"] == []


def test_launch_vs_execution_distinction():
    # c1 dispatched + completed; c2 dispatched but failed; (launch != execution)
    events = [
        _started(children=("c1", "c2")),
        _ev(3, "fanout.child.dispatched", {"fanout_id": "F1", "child_id": "c1"}),
        _ev(4, "fanout.child.dispatched", {"fanout_id": "F1", "child_id": "c2"}),
        _ev(5, "fanout.child.completed", {"fanout_id": "F1", "child_id": "c1"}),
        _ev(6, "fanout.child.failed", {"fanout_id": "F1", "child_id": "c2", "reason": "output_violation"}),
    ]
    run = build_workflow_run(fanout_id="F1", events=events)
    launched = {o["child_id"]: o["dispatched"] for o in run["launch_outcomes"]}
    assert launched == {"c1": True, "c2": True}  # both launched
    exec_ = {o["child_id"]: o["status"] for o in run["execution_outcomes"]}
    assert exec_ == {"c1": "completed", "c2": "failed"}  # but different execution
    assert "output_violation" in run["failure_reasons"]


def test_completed_when_aggregate_done():
    events = [
        _started(children=("c1",)),
        _ev(3, "fanout.child.dispatched", {"fanout_id": "F1", "child_id": "c1"}),
        _ev(4, "fanout.child.completed", {"fanout_id": "F1", "child_id": "c1"}),
        _ev(5, "fanout.aggregate.started", {"fanout_id": "F1", "mode": "all_required"}),
        _ev(6, "fanout.aggregate.completed", {"fanout_id": "F1"}),
    ]
    run = build_workflow_run(fanout_id="F1", events=events)
    assert run["status"] == "completed"
    assert run["aggregate"] == {"started": True, "completed": True}


def test_recorded_no_runtime_not_shown_as_launched():
    # Web-recorded intent only: fanout.requested(queued_no_runtime), no started/dispatched
    events = [
        _ev(1, "fanout.requested",
            {"fanout_id": "F1", "trace_id": "tr1", "runtime_delivery": "queued_no_runtime"},
            correlation_id="tr1"),
    ]
    run = build_workflow_run(fanout_id="F1", events=events)
    assert run["status"] == "recorded_no_runtime"
    assert run["recorded_no_runtime"] is True
    assert run["launch_outcomes"] == []  # NOT shown as children launched
    assert run["execution_outcomes"] == []


def test_workflow_invoke_pattern_linked_by_trace():
    events = [
        _ev(1, "workflow.invoke.requested", {"pattern_id": "review-wave", "task_id": "T1"}, correlation_id="tr1"),
        _ev(2, "workflow.invoke.accepted", {"pattern_id": "review-wave", "task_id": "T1"}, correlation_id="tr1"),
        _started(),
        _ev(4, "fanout.child.dispatched", {"fanout_id": "F1", "child_id": "c1"}),
    ]
    run = build_workflow_run(fanout_id="F1", events=events)
    assert run["pattern"]["pattern_id"] == "review-wave"
    assert run["pattern"]["invoke_status"] == "accepted"  # last linked invoke


def test_timed_out_status():
    events = [
        _started(children=("c1",)),
        _ev(3, "fanout.child.dispatched", {"fanout_id": "F1", "child_id": "c1"}),
        _ev(4, "fanout.timed_out", {"fanout_id": "F1"}),
    ]
    run = build_workflow_run(fanout_id="F1", events=events)
    assert run["status"] == "timed_out"


def test_unknown_fanout_degrades():
    run = build_workflow_run(fanout_id="NOPE", events=[_started(fid="F1")])
    assert run["status"] == "unknown"
    assert run["launch_outcomes"] == []
    assert {d["kind"] for d in run["diagnostics"]} == {"fanout_not_found"}
