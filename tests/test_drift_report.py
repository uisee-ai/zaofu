"""Tests for drift-report.v1 — planned vs actual reconciliation."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.drift_report import build_drift_report


def _node(task_id, *, status, owner_role="", assigned_to="", blocked_by=None,
          evidence=None, scope=None, instances_history=None):
    actual = {
        "status": status,
        "assigned_to": assigned_to,
        "evidence_events": evidence or [],
    }
    if instances_history is not None:
        actual["affinity"] = {"instances_history": instances_history}
    return {
        "task_id": task_id,
        "title": task_id,
        "planned": {
            "owner_role": owner_role,
            "blocked_by": blocked_by or [],
            "scope": scope or [],
            "verification": "pytest",
        },
        "actual": actual,
        "drift": [],
    }


def _graph(nodes):
    return {"schema_version": "execution-graph.v1", "nodes": nodes}


def test_no_drift_clean_run():
    graph = _graph([
        _node("T1", status="done", owner_role="dev", assigned_to="dev-1",
              evidence=["e1"]),
        _node("T2", status="in_progress", owner_role="dev", assigned_to="dev-2",
              blocked_by=["T1"]),
    ])
    report = build_drift_report(graph=graph)
    assert report["status"] == "ok"
    assert report["items"] == []
    assert report["summary"] == {"error": 0, "warning": 0, "info": 0}


def test_dependency_drift_writer_started_before_blocker_done():
    graph = _graph([
        _node("T1", status="in_progress", owner_role="dev", assigned_to="dev-1"),
        _node("T2", status="in_progress", owner_role="dev", assigned_to="dev-2",
              blocked_by=["T1"]),
    ])
    report = build_drift_report(graph=graph)
    dep = [i for i in report["items"] if i["kind"] == "dependency_drift"]
    assert len(dep) == 1
    assert dep[0]["task_id"] == "T2"
    assert dep[0]["severity"] == "error"
    assert report["status"] == "error"


def test_no_dependency_drift_when_blocker_done():
    graph = _graph([
        _node("T1", status="done", owner_role="dev", assigned_to="dev-1",
              evidence=["e1"]),
        _node("T2", status="in_progress", owner_role="dev", assigned_to="dev-2",
              blocked_by=["T1"]),
    ])
    report = build_drift_report(graph=graph)
    assert [i for i in report["items"] if i["kind"] == "dependency_drift"] == []


def test_no_dependency_drift_when_dependent_not_started():
    # T2 waiting (backlog) with undone blocker is correct, not drift
    graph = _graph([
        _node("T1", status="in_progress", owner_role="dev", assigned_to="dev-1"),
        _node("T2", status="backlog", owner_role="dev", blocked_by=["T1"]),
    ])
    report = build_drift_report(graph=graph)
    assert [i for i in report["items"] if i["kind"] == "dependency_drift"] == []


def test_assignment_drift_role_mismatch():
    graph = _graph([
        _node("T1", status="in_progress", owner_role="dev", assigned_to="review-1"),
    ])
    report = build_drift_report(graph=graph)
    asn = [i for i in report["items"] if i["kind"] == "assignment_drift"]
    assert len(asn) == 1
    assert asn[0]["severity"] == "warning"
    assert asn[0]["actual"]["assigned_to"] == "review-1"


def test_assignment_ok_when_role_prefix_matches():
    graph = _graph([
        _node("T1", status="in_progress", owner_role="dev", assigned_to="dev-2"),
    ])
    report = build_drift_report(graph=graph)
    assert [i for i in report["items"] if i["kind"] == "assignment_drift"] == []


def test_assignment_ok_when_final_assignee_is_downstream_stage():
    graph = _graph([
        _node(
            "T1",
            status="done",
            owner_role="dev",
            assigned_to="qa",
            evidence=["evt-build", "evt-test"],
            instances_history=["dev-1", "qa"],
        ),
    ])

    report = build_drift_report(graph=graph)

    assert [i for i in report["items"] if i["kind"] == "assignment_drift"] == []


def test_evidence_drift_done_without_evidence_is_warning_not_error():
    graph = _graph([
        _node("T1", status="done", owner_role="dev", assigned_to="dev-1",
              evidence=[]),
    ])
    report = build_drift_report(graph=graph)
    ev = [i for i in report["items"] if i["kind"] == "evidence_drift"]
    assert len(ev) == 1
    # red line §20.3: absence-of-evidence never escalates to error
    assert ev[0]["severity"] == "warning"
    assert report["status"] == "warning"


def test_scope_drift_consumes_kernel_event_not_recomputed():
    graph = _graph([
        _node("T1", status="in_progress", owner_role="dev", assigned_to="dev-1",
              scope=["src/api/**"]),
    ])
    events = [
        (1, ZfEvent(type="scope.violation", id="sv1", task_id="T1")),
        (2, ZfEvent(type="worker.progress", id="p1", task_id="T1")),
    ]
    report = build_drift_report(graph=graph, events=events)
    scope = [i for i in report["items"] if i["kind"] == "scope_drift"]
    assert len(scope) == 1
    # references the kernel's own event id — projection does not re-judge
    assert scope[0]["evidence_event_id"] == "sv1"
    assert scope[0]["severity"] == "warning"
