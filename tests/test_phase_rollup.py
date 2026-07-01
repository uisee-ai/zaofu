"""Tests for phase-level rollup (doc 69 S-c)."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.runtime.execution_graph import build_execution_graph
from zf.runtime.phase_rollup import build_phase_rollups


def _task(tid, *, status="backlog", wave=0, phase=""):
    return Task(id=tid, title=tid, status=status,
                contract=TaskContract(feature_id="F-1", wave=wave, phase=phase))


def _task_map():
    return {
        "schema_version": "task-map.v1", "feature_id": "F-1",
        "tasks": [
            {"task_id": "T1", "owner_role": "dev", "phase": "impl", "wave": 1},
            {"task_id": "T2", "owner_role": "dev", "phase": "impl", "wave": 1},
            {"task_id": "T3", "owner_role": "test", "phase": "acceptance", "wave": 2, "blocked_by": ["T1"]},
        ],
    }


def _build(tasks, events=()):
    graph = build_execution_graph(task_map=_task_map(), tasks=tasks, events=events)
    return {p["phase_id"]: p for p in build_phase_rollups(graph=graph, events=events, tasks=tasks)}


def test_groups_by_phase_and_orders_by_wave():
    tasks = {"T1": _task("T1", status="done", wave=1),
             "T2": _task("T2", status="in_progress", wave=1),
             "T3": _task("T3", status="backlog", wave=2)}
    phases = _build(tasks)
    assert set(phases) == {"impl", "acceptance"}
    assert phases["impl"]["order"] == 0       # wave 1 phase first
    assert phases["acceptance"]["order"] == 1
    assert phases["impl"]["task_ids"] == ["T1", "T2"]


def test_completion_rate():
    tasks = {"T1": _task("T1", status="done", wave=1),
             "T2": _task("T2", status="in_progress", wave=1),
             "T3": _task("T3", status="backlog", wave=2)}
    phases = _build(tasks)
    assert phases["impl"]["completion_rate"] == 0.5   # 1 of 2 done
    assert phases["impl"]["done_count"] == 1
    assert phases["acceptance"]["completion_rate"] == 0.0


def test_pass_rate_and_eval_latest_state():
    tasks = {"T1": _task("T1", status="done", wave=1),
             "T2": _task("T2", status="done", wave=1),
             "T3": _task("T3", status="done", wave=2)}
    events = [
        (1, ZfEvent(type="test.failed", id="a", task_id="T1")),
        (2, ZfEvent(type="test.passed", id="b", task_id="T1")),   # latest=pass
        (3, ZfEvent(type="judge.passed", id="c", task_id="T2")),
        (4, ZfEvent(type="review.rejected", id="d", task_id="T2")),
    ]
    phases = _build(tasks, events)
    impl = phases["impl"]
    # gates in impl phase: T1 test=pass, T2 judge=pass + review=fail → 2 pass, 1 fail
    assert impl["pass_rate"] == round(2 / 3, 4)
    assert impl["eval"]["test"] == {"passed": 1, "failed": 0}
    assert impl["eval"]["review"] == {"passed": 0, "failed": 1}
    assert impl["eval"]["verdict"] == "mixed"  # has pass and fail, all tasks done


def test_rework_and_pause_counts():
    tasks = {"T1": _task("T1", status="in_progress", wave=1),
             "T2": _task("T2", status="in_progress", wave=1),
             "T3": _task("T3", wave=2)}
    events = [
        (1, ZfEvent(type="task.rework.requested", id="r1", task_id="T1")),
        (2, ZfEvent(type="task.rework.requested", id="r2", task_id="T1")),
        (3, ZfEvent(type="dispatch.paused", id="p1", task_id="T2")),
    ]
    phases = _build(tasks, events)
    assert phases["impl"]["rework_count"] == 2
    assert phases["impl"]["paused_count"] == 1
    assert phases["impl"]["status"] == "rework"   # rework + not all done


def test_phase_done_status():
    tasks = {"T1": _task("T1", status="done", wave=1),
             "T2": _task("T2", status="done", wave=1),
             "T3": _task("T3", status="backlog", wave=2)}
    phases = _build(tasks)
    assert phases["impl"]["status"] == "done"
    assert phases["acceptance"]["status"] == "waiting"


def test_phase_fallback_to_contract_when_no_planned_phase():
    # kanban-only (no task-map) → planned phase empty → use contract.phase
    tasks = {"X1": _task("X1", status="done", phase="design")}
    graph = build_execution_graph(task_map=None, tasks=tasks)
    phases = {p["phase_id"]: p for p in build_phase_rollups(graph=graph, tasks=tasks)}
    assert "design" in phases


def test_agent_runs_linked_via_trigger_chain():
    # task.fanout.requested(T1) → fanout.started(trigger_event_id=req) → children
    tasks = {"T1": _task("T1", status="in_progress", wave=1, phase="impl"),
             "T2": _task("T2", wave=1, phase="impl"),
             "T3": _task("T3", wave=2, phase="acceptance")}
    events = [
        (1, ZfEvent(type="task.fanout.requested", id="req1", task_id="T1")),
        (2, ZfEvent(type="fanout.started", id="fs1", payload={
            "fanout_id": "FX1", "trigger_event_id": "req1", "stage_id": "review",
            "topology": "fanout_reader",
            "expected_children": [{"child_id": "c1", "role_instance": "review-c1"},
                                  {"child_id": "c2", "role_instance": "review-c2"}]})),
        (3, ZfEvent(type="fanout.child.dispatched", payload={"fanout_id": "FX1", "child_id": "c1"})),
        (4, ZfEvent(type="fanout.child.completed", payload={"fanout_id": "FX1", "child_id": "c1"})),
    ]
    graph = build_execution_graph(task_map=_task_map(), tasks=tasks, events=events)
    by_id = {n["task_id"]: n for n in graph["nodes"]}
    assert by_id["T1"]["actual"]["fanout_ids"] == ["FX1"]   # linked via trigger chain
    phases = {p["phase_id"]: p for p in build_phase_rollups(graph=graph, events=events, tasks=tasks)}
    runs = phases["impl"]["agent_runs"]
    assert len(runs) == 1
    assert runs[0]["task_id"] == "T1" and runs[0]["fanout_id"] == "FX1"
    assert runs[0]["topology"] == "fanout_reader"
    assert runs[0]["launched"] == 1 and runs[0]["executed"] == 1


def test_phase_affinity_drifted():
    # T2 owner mismatch → phase affinity drifted
    tasks = {"T1": _task("T1", status="done", wave=1, phase="impl"),
             "T2": _task("T2", status="in_progress", wave=1, phase="impl")}
    tm = {"schema_version": "task-map.v1", "feature_id": "F-1", "tasks": [
        {"task_id": "T1", "owner_role": "dev", "owner_instance": "dev-1", "phase": "impl", "wave": 1},
        {"task_id": "T2", "owner_role": "dev", "owner_instance": "dev-1", "phase": "impl", "wave": 1}]}
    # T2 assigned to dev-9 ≠ planned dev-1
    tasks["T2"].assigned_to = "dev-9"
    graph = build_execution_graph(task_map=tm, tasks=tasks)
    phases = {p["phase_id"]: p for p in build_phase_rollups(graph=graph, tasks=tasks)}
    assert phases["impl"]["affinity"]["status"] == "drifted"
    assert phases["impl"]["affinity"]["drifted_count"] == 1
