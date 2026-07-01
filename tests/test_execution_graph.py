"""Tests for execution-graph.v1 — planned task-map joined with actual runtime."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.runtime.execution_graph import build_execution_graph, build_superseded_nodes


def _task(task_id: str, *, status: str = "backlog", assigned_to: str = "",
          wave: int = 0, feature_id: str = "F-1") -> Task:
    return Task(
        id=task_id,
        title=f"title-{task_id}",
        status=status,
        assigned_to=assigned_to or None,
        contract=TaskContract(feature_id=feature_id, wave=wave),
    )


def _task_map() -> dict:
    return {
        "schema_version": "task-map.v1",
        "feature_id": "F-1",
        "tasks": [
            {"task_id": "T1", "title": "schema", "owner_role": "dev",
             "wave": 1, "blocked_by": [], "exclusive_files": ["src/a/**"],
             "verification": "pytest tests/test_a.py"},
            {"task_id": "T2", "title": "router", "owner_role": "dev",
             "wave": 2, "blocked_by": ["T1"]},
            {"task_id": "T3", "title": "tests", "owner_role": "test",
             "wave": 3, "blocked_by": ["T2"]},
        ],
    }


def test_joins_planned_with_actual():
    tasks = {
        "T1": _task("T1", status="done", assigned_to="dev-1", wave=1),
        "T2": _task("T2", status="in_progress", assigned_to="dev-2", wave=2),
        "T3": _task("T3", status="backlog", wave=3),
    }
    graph = build_execution_graph(
        task_map=_task_map(), tasks=tasks, feature_id="F-1",
        task_map_ref=".zf/artifacts/F-1/task-map.json",
    )
    assert graph["schema_version"] == "execution-graph.v1"
    assert graph["feature_id"] == "F-1"
    assert graph["task_count"] == 3
    by_id = {n["task_id"]: n for n in graph["nodes"]}
    # planned dimension comes from the task-map
    assert by_id["T1"]["planned"]["owner_role"] == "dev"
    assert by_id["T1"]["planned"]["exclusive_files"] == ["src/a/**"]
    assert by_id["T2"]["planned"]["blocked_by"] == ["T1"]
    # actual dimension comes from kanban
    assert by_id["T1"]["actual"]["status"] == "done"
    assert by_id["T2"]["actual"]["assigned_to"] == "dev-2"
    assert by_id["T3"]["actual"]["status"] == "backlog"


def test_blocked_by_edges_and_satisfaction():
    tasks = {
        "T1": _task("T1", status="done", wave=1),
        "T2": _task("T2", status="in_progress", wave=2),
        "T3": _task("T3", status="backlog", wave=3),
    }
    graph = build_execution_graph(task_map=_task_map(), tasks=tasks)
    edges = {(e["from"], e["to"]): e for e in graph["edges"]}
    assert ("T1", "T2") in edges and ("T2", "T3") in edges
    # T1 done -> edge into T2 satisfied; T2 not done -> edge into T3 pending
    assert edges[("T1", "T2")]["status"] == "satisfied"
    assert edges[("T2", "T3")]["status"] == "pending"


def test_wave_status_summary():
    tasks = {
        "T1": _task("T1", status="done", wave=1),
        "T2": _task("T2", status="in_progress", wave=2),
        "T3": _task("T3", status="backlog", wave=3),
    }
    graph = build_execution_graph(task_map=_task_map(), tasks=tasks)
    waves = {w["wave"]: w for w in graph["waves"]}
    assert waves[1]["status"] == "done"
    assert waves[2]["status"] == "in_progress"
    assert waves[3]["status"] == "waiting"
    assert waves[1]["task_ids"] == ["T1"]


def test_evidence_events_collected_per_task():
    tasks = {"T1": _task("T1", status="done", wave=1),
             "T2": _task("T2", wave=2), "T3": _task("T3", wave=3)}
    events = [
        (1, ZfEvent(type="dev.build.done", id="e1", task_id="T1")),
        (2, ZfEvent(type="static_gate.passed", id="e2", task_id="T1")),
        (3, ZfEvent(type="worker.progress", id="e3", task_id="T1")),  # not evidence
        (4, ZfEvent(type="review.approved", id="e4", task_id="T2")),
    ]
    graph = build_execution_graph(task_map=_task_map(), tasks=tasks, events=events)
    by_id = {n["task_id"]: n for n in graph["nodes"]}
    assert by_id["T1"]["actual"]["evidence_events"] == ["e1", "e2"]
    assert by_id["T2"]["actual"]["evidence_events"] == ["e4"]
    assert by_id["T3"]["actual"]["evidence_events"] == []


def test_degrades_to_kanban_only_when_task_map_missing():
    tasks = {"T1": _task("T1", status="in_progress")}
    graph = build_execution_graph(task_map=None, tasks=tasks, feature_id="F-1")
    assert graph["kanban_only"] is True
    assert graph["task_count"] == 1
    assert graph["nodes"][0]["planned"] == {}
    assert graph["edges"] == []
    kinds = {d["kind"] for d in graph["diagnostics"]}
    assert "task_map_missing" in kinds


def test_diagnostic_when_kanban_task_missing_for_node():
    # task-map has T1/T2/T3 but kanban only has T1
    tasks = {"T1": _task("T1", status="done", wave=1)}
    graph = build_execution_graph(task_map=_task_map(), tasks=tasks)
    by_id = {n["task_id"]: n for n in graph["nodes"]}
    assert by_id["T2"]["actual"]["status"] == "not_created"
    diag = [d for d in graph["diagnostics"] if d["kind"] == "kanban_task_missing"]
    assert {d["task_id"] for d in diag} == {"T2", "T3"}


def test_diagnostic_when_kanban_task_not_in_task_map():
    tasks = {
        "T1": _task("T1", status="done", wave=1),
        "T2": _task("T2", status="in_progress", wave=2),
        "T3": _task("T3", wave=3),
        "T-EXTRA": _task("T-EXTRA", status="in_progress"),
    }
    graph = build_execution_graph(task_map=_task_map(), tasks=tasks)
    diag = [d for d in graph["diagnostics"] if d["kind"] == "task_not_in_task_map"]
    assert {d["task_id"] for d in diag} == {"T-EXTRA"}


# --- doc 69 S-a: gate outcomes (pass_rate base) + lifecycle evidence ---

def test_gate_outcomes_latest_state_wins():
    from zf.runtime.execution_graph import gate_outcomes_by_task
    events = [
        (1, ZfEvent(type="test.failed", id="t1", task_id="T1")),
        (2, ZfEvent(type="test.passed", id="t2", task_id="T1")),   # later → pass
        (3, ZfEvent(type="judge.passed", id="j1", task_id="T1")),
        (4, ZfEvent(type="review.rejected", id="r1", task_id="T2")),
    ]
    out = gate_outcomes_by_task(events)
    # T1 test fail→pass dedups to latest=pass; judge pass kept
    assert out["T1"] == {"test": "pass", "judge": "pass"}
    assert out["T2"] == {"review": "fail"}


def test_discriminator_and_lifecycle_events_become_node_evidence():
    tasks = {"T1": _task("T1", status="done", wave=1),
             "T2": _task("T2", status="in_progress", wave=2), "T3": _task("T3", wave=3)}
    events = [
        (1, ZfEvent(type="discriminator.passed", id="d1", task_id="T1")),
        (2, ZfEvent(type="task.rework.requested", id="rw1", task_id="T2")),
        (3, ZfEvent(type="dispatch.paused", id="p1", task_id="T2")),
        (4, ZfEvent(type="task.fix_spawned", id="fx1", task_id="T2")),
    ]
    graph = build_execution_graph(task_map=_task_map(), tasks=tasks, events=events)
    by_id = {n["task_id"]: n for n in graph["nodes"]}
    assert "d1" in by_id["T1"]["actual"]["evidence_events"]
    assert {"rw1", "p1", "fx1"} <= set(by_id["T2"]["actual"]["evidence_events"])


# --- doc 69 S-h: affinity / timing / trace_id enrichment ---

def test_affinity_owner_mismatch():
    tasks = {"T1": _task("T1", status="in_progress", assigned_to="review-9", wave=1)}
    tm = {"schema_version": "task-map.v1", "feature_id": "F-1", "tasks": [
        {"task_id": "T1", "owner_role": "dev", "owner_instance": "dev-1", "wave": 1}]}
    g = build_execution_graph(task_map=tm, tasks=tasks)
    aff = g["nodes"][0]["actual"]["affinity"]
    assert aff["drift_kind"] == "owner_mismatch"
    assert aff["drifted"] is True
    assert aff["actual_owner"] == "review-9" and aff["planned_owner"] == "dev-1"


def test_affinity_multi_instance_from_dispatch_history():
    tasks = {"T1": _task("T1", status="in_progress", assigned_to="dev-5", wave=1)}
    tm = {"schema_version": "task-map.v1", "feature_id": "F-1", "tasks": [
        {"task_id": "T1", "owner_role": "dev", "wave": 1}]}
    events = [
        (1, ZfEvent(type="task.dispatched", id="d1", task_id="T1", payload={"assignee": "dev-2"})),
        (2, ZfEvent(type="task.dispatched", id="d2", task_id="T1", payload={"assignee": "dev-2"})),  # repeat
        (3, ZfEvent(type="task.dispatched", id="d3", task_id="T1", payload={"assignee": "dev-5"})),  #飘
    ]
    g = build_execution_graph(task_map=tm, tasks=tasks, events=events)
    aff = g["nodes"][0]["actual"]["affinity"]
    assert aff["drift_kind"] == "multi_instance"
    assert aff["instances_history"] == ["dev-2", "dev-5"]  # consecutive repeat collapsed


def test_affinity_stage_handoff_is_not_instance_drift():
    tasks = {"T1": _task("T1", status="done", assigned_to="qa", wave=1)}
    tm = {"schema_version": "task-map.v1", "feature_id": "F-1", "tasks": [
        {"task_id": "T1", "owner_role": "dev", "wave": 1}]}
    events = [
        (1, ZfEvent(type="task.dispatched", id="d1", task_id="T1", payload={"assignee": "dev-1"})),
        (2, ZfEvent(type="task.dispatched", id="d2", task_id="T1", payload={"assignee": "qa"})),
    ]

    g = build_execution_graph(task_map=tm, tasks=tasks, events=events)
    aff = g["nodes"][0]["actual"]["affinity"]

    assert aff["drift_kind"] == "none"
    assert aff["drifted"] is False
    assert aff["stage_handoff"] is True
    assert aff["instances_history"] == ["dev-1", "qa"]


def test_affinity_stable():
    tasks = {"T1": _task("T1", status="done", assigned_to="dev-1", wave=1)}
    tm = {"schema_version": "task-map.v1", "feature_id": "F-1", "tasks": [
        {"task_id": "T1", "owner_role": "dev", "owner_instance": "dev-1", "wave": 1}]}
    events = [(1, ZfEvent(type="task.dispatched", id="d1", task_id="T1", payload={"assignee": "dev-1"}))]
    g = build_execution_graph(task_map=tm, tasks=tasks, events=events)
    assert g["nodes"][0]["actual"]["affinity"]["drift_kind"] == "none"


def test_timing_and_duration_and_trace():
    tasks = {"T1": _task("T1", status="done", wave=1)}
    tm = {"schema_version": "task-map.v1", "feature_id": "F-1", "tasks": [
        {"task_id": "T1", "owner_role": "dev", "wave": 1}]}
    events = [
        (1, ZfEvent(type="task.dispatched", id="d1", task_id="T1",
                    ts="2026-05-30T10:00:00+00:00", payload={"assignee": "dev-1"},
                    correlation_id="corr-1")),
        (2, ZfEvent(type="task.done", id="x1", task_id="T1",
                    ts="2026-05-30T10:30:00+00:00")),
    ]
    g = build_execution_graph(task_map=tm, tasks=tasks, events=events)
    a = g["nodes"][0]["actual"]
    assert a["started_at"] == "2026-05-30T10:00:00+00:00"
    assert a["completed_at"] == "2026-05-30T10:30:00+00:00"
    assert a["duration_seconds"] == 1800.0
    assert a["trace_id"] == "corr-1"


# --- doc 69 S-h pt2: agent_summary / changed_files / health ---

def test_agent_summary_and_changed_files_and_health():
    from zf.core.task.schema import TaskEvidence
    t = _task("T1", status="in_progress", wave=1)
    t.evidence = TaskEvidence(files_touched=["a.py", "b.py"])
    tasks = {"T1": t}
    tm = {"schema_version": "task-map.v1", "feature_id": "F-1", "tasks": [
        {"task_id": "T1", "owner_role": "dev", "wave": 1}]}
    events = [
        (1, ZfEvent(type="task.dispatched", id="d1", task_id="T1",
                    ts="2026-05-30T10:00:00+00:00", payload={"assignee": "dev-1"})),
        (2, ZfEvent(type="task.fanout.requested", id="rq", task_id="T1",
                    ts="2026-05-30T10:01:00+00:00")),
        (3, ZfEvent(type="fanout.started", id="fs", ts="2026-05-30T10:02:00+00:00",
                    payload={"fanout_id": "FX", "trigger_event_id": "rq",
                             "expected_children": [{"child_id": "c1"}, {"child_id": "c2"}]})),
        (4, ZfEvent(type="fanout.child.dispatched", ts="2026-05-30T10:03:00+00:00",
                    payload={"fanout_id": "FX", "child_id": "c1"})),
        (5, ZfEvent(type="fanout.child.completed", ts="2026-05-30T10:04:00+00:00",
                    payload={"fanout_id": "FX", "child_id": "c1"})),
        (6, ZfEvent(type="worker.heartbeat", id="hb", task_id="T1",
                    ts="2026-05-30T10:01:00+00:00")),
        (7, ZfEvent(type="worker.progress", id="p", task_id="T1",
                    ts="2026-05-30T10:20:00+00:00")),  # 19 min after last hb
    ]
    g = build_execution_graph(task_map=tm, tasks=tasks, events=events)
    a = g["nodes"][0]["actual"]
    assert a["changed_files"] == ["a.py", "b.py"]
    assert a["agent_summary"] == {"launched": 1, "executed": 1, "expected": 2}
    # last heartbeat 10:01, latest event 10:20 → age 1140s > 300, in_progress → stuck
    assert a["health"]["heartbeat_age_seconds"] == 1140.0
    assert a["health"]["stuck"] is True


def test_health_not_stuck_when_done():
    t = _task("T1", status="done", wave=1)
    tm = {"schema_version": "task-map.v1", "feature_id": "F-1", "tasks": [
        {"task_id": "T1", "owner_role": "dev", "wave": 1}]}
    events = [
        (1, ZfEvent(type="worker.heartbeat", id="hb", task_id="T1", ts="2026-05-30T10:00:00+00:00")),
        (2, ZfEvent(type="task.done", id="x", task_id="T1", ts="2026-05-30T11:00:00+00:00")),
    ]
    g = build_execution_graph(task_map=tm, tasks={"T1": t}, events=events)
    assert g["nodes"][0]["actual"]["health"]["stuck"] is False  # done not in_progress


def test_build_superseded_nodes_for_dropped_kanban_tasks():
    # doc 69 §14.10 (S-k): T3 in kanban but not in current task-map → superseded node
    tasks = {"T1": _task("T1", status="done"),
             "T2": _task("T2", status="in_progress"),
             "T3": _task("T3", status="cancelled")}
    nodes = build_superseded_nodes(tasks, existing_ids={"T1", "T2"}, events=[])
    assert len(nodes) == 1
    n = nodes[0]
    assert n["task_id"] == "T3"
    assert n["planned"] == {}
    assert n["superseded"] is True
    assert n["actual"]["status"] == "cancelled"
    # enriched like a normal node (affinity/health present)
    assert "affinity" in n["actual"] and "health" in n["actual"]


def test_build_superseded_nodes_empty_when_all_planned():
    tasks = {"T1": _task("T1"), "T2": _task("T2")}
    assert build_superseded_nodes(tasks, existing_ids={"T1", "T2"}, events=[]) == []
