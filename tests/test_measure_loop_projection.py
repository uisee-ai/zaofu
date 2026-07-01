from __future__ import annotations

from pathlib import Path

from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.measure_loop_projection import build_measure_loop_projection


def _state_dir(tmp_path: Path) -> Path:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    return state_dir


def test_measure_loop_projection_builds_delivery_lens_metrics(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1",
        title="schema",
        status="done",
        contract=TaskContract(feature_id="F-1", owner_role="dev"),
    ))
    store.add(Task(
        id="T2",
        title="router",
        status="backlog",
        contract=TaskContract(feature_id="F-1", owner_role="dev"),
    ))
    store.add(Task(
        id="T3",
        title="ui",
        status="in_progress",
        assigned_to="dev-1",
        contract=TaskContract(feature_id="F-1", owner_role="dev"),
    ))
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(type="task.dispatched", id="dispatch-1", task_id="T3", payload={"feature_id": "F-1"}))
    log.append(ZfEvent(type="static_gate.passed", id="gate-pass", task_id="T1", payload={"feature_id": "F-1"}))
    log.append(ZfEvent(type="static_gate.failed", id="gate-fail", task_id="T3", payload={"feature_id": "F-1"}))
    log.append(ZfEvent(type="task.rework.requested", id="rework-1", task_id="T3", payload={"feature_id": "F-1"}))

    projection = build_measure_loop_projection(
        state_dir,
        project_id="proj",
        feature_id="F-1",
        lens="all",
        generated_at="2026-06-17T00:00:00+00:00",
    )

    assert projection["schema_version"] == "measure-loop.v1"
    assert projection["active_lens"] == "all"
    labels = [item["label"] for item in projection["metrics"]]
    assert labels == ["Delivery", "Ready", "Active", "Idle-Ready", "Blocked", "Gate Pass"]
    gate_metric = next(item for item in projection["metrics"] if item["id"] == "gate_pass")
    assert gate_metric["source_event_ids"] == ["gate-pass", "gate-fail"]
    assert gate_metric["graph_node_ids"] == ["verify"]
    ready_metric = next(item for item in projection["metrics"] if item["id"] == "ready")
    assert ready_metric["task_ids"] == ["T2"]
    assert projection["summary"]["total"] == 3
    assert projection["summary"]["done"] == 1
    assert projection["summary"]["ready"] == 1
    assert projection["summary"]["gate_pass_percent"] == 50
    assert [stage["label"] for stage in projection["stages"]] == ["Plan", "Dispatch", "Work", "Verify", "Rework/Ship"]
    verify_stage = next(item for item in projection["stages"] if item["id"] == "verify")
    assert verify_stage["source_event_ids"] == ["gate-pass", "gate-fail"]
    assert projection["graph"]["nodes"][3]["source_event_ids"] == ["gate-pass", "gate-fail"]
    assert projection["graph"]["layout_hint"] == "ring"
    assert projection["source_projection_refs"] == ["TaskStore", "EventLog", "dispatch-diagnostics.v1", "loop.v1"]


def test_measure_loop_projection_switches_lens_payload(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    TaskStore(state_dir / "kanban.json").add(Task(
        id="T1",
        title="gateway",
        status="backlog",
        contract=TaskContract(feature_id="F-1", owner_role="dev"),
    ))
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(type="replan.contract_eval.completed", id="replan-1", payload={"feature_id": "F-1", "decision": "revise"}))
    log.append(ZfEvent(type="autoresearch.loop.completed", id="ar-1", payload={"feature_id": "F-1"}))

    projection = build_measure_loop_projection(
        state_dir,
        project_id="proj",
        feature_id="F-1",
        lens="hill_climbing",
        generated_at="2026-06-17T00:00:00+00:00",
    )

    assert projection["active_lens"] == "hill_climbing"
    assert projection["graph"]["layout_hint"] == "ring"
    assert [item["label"] for item in projection["metrics"]] == [
        "Feedback Loops",
        "Open",
        "Candidates",
        "Improve Events",
    ]
    assert projection["feed"][0]["event_type"] == "autoresearch.loop.completed"
    assert projection["metrics"][3]["source_event_ids"] == ["replan-1", "ar-1"]
    assert projection["stages"][4]["source_projection_refs"] == ["loop.v1"]


def test_measure_loop_projection_keeps_trace_scoped_event_lineage(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(
        type="runtime.action.completed",
        id="trace-event-1",
        correlation_id="trace-demo",
        payload={"trace_id": "trace-demo", "status": "passed"},
    ))

    projection = build_measure_loop_projection(
        state_dir,
        project_id="proj",
        feature_id="trace-demo",
        lens="all",
        generated_at="2026-06-17T00:00:00+00:00",
    )

    delivery_metric = next(item for item in projection["metrics"] if item["id"] == "delivery")
    assert delivery_metric["source_event_ids"] == ["trace-event-1"]
    assert delivery_metric["trace_ids"] == ["trace-demo"]
    assert projection["feed"][0]["event_id"] == "trace-event-1"
    assert projection["feed"][0]["trace_id"] == "trace-demo"
