"""Web API tests for delivery-trace endpoints (doc 68 S3 / doc 65 P1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.web.server import create_app


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "feature_list.json").write_text("[]")
    store = TaskStore(sd / "kanban.json")
    store.add(Task(id="T1", title="schema", status="done", assigned_to="dev-1",
                   contract=TaskContract(feature_id="F-1", owner_role="dev", wave=1)))
    store.add(Task(id="T2", title="router", status="in_progress", assigned_to="dev-2",
                   blocked_by=["T1"],
                   contract=TaskContract(feature_id="F-1", owner_role="dev", wave=2)))
    log = event_log_from_project(sd, config=None, warn=False)
    log.append(ZfEvent(type="loop.started", actor="zf-cli"))
    log.append(ZfEvent(type="dev.build.done", id="e-build", task_id="T1"))
    artifacts = sd / "artifacts" / "F-1"
    artifacts.mkdir(parents=True)
    (artifacts / "task_map.json").write_text(json.dumps({
        "schema_version": "task-map.v1", "feature_id": "F-1",
        "tasks": [
            {"task_id": "T1", "title": "schema", "owner_role": "dev", "wave": 1},
            {"task_id": "T2", "title": "router", "owner_role": "dev", "wave": 2, "blocked_by": ["T1"]},
        ],
    }))
    return sd


@pytest.fixture
def client(state_dir: Path) -> TestClient:
    return TestClient(create_app(state_dir))


def test_delivery_trace_endpoint(client: TestClient):
    r = client.get("/api/projects/default/delivery-traces/F-1")
    assert r.status_code == 200
    data = r.json()
    assert data["schema_version"] == "delivery-trace.v1"
    assert data["feature_id"] == "F-1"
    assert data["status"] == "in_progress"
    assert data["execution_graph"]["task_count"] == 2
    assert data["execution_graph"]["done_count"] == 1
    assert data["workflow_spine"]["schema_version"] == "workflow-spine.v1"
    assert data["workflow_trace"]["schema_version"] == "workflow-trace.v1"
    assert data["workflow_trace"]["diagnostics"][0]["kind"] == "workflow_config_missing"
    assert data["task_flow"]["schema_version"] == "delivery-task-flow.v1"
    assert data["task_flow"]["metrics"]["task_total"] == 2
    assert data["run_groups"][0]["schema_version"] == "delivery-run-group.v1"
    assert data["trace"]["schema_version"] == "delivery-run-trace.v1"
    assert data["closed_loop"]["schema_version"] == "delivery-closed-loop.v1"
    assert data["cursor"]["schema_version"] == "delivery-cursor.v1"
    assert data["deltas"] == []
    assert data["related_loop_ids"] == []
    assert data["related_loop_count"] == 0
    assert data["thick_trace"]["schema_version"] == "delivery-thick-trace.v1"
    assert data["thick_trace"]["graph"]["node_count"] >= 2
    assert data["thick_trace"]["spans"]
    assert any(node["node_id"] == "task:T1" for node in data["closed_loop"]["nodes"])
    assert any(edge["kind"] == "blocked_by" for edge in data["closed_loop"]["edges"])


def test_delivery_trace_endpoint_includes_goal_closure_loop(
    client: TestClient,
    state_dir: Path,
):
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="ISSUE-GAP-001",
        title="fill issue gap",
        status="todo",
        assigned_to="dev-gap",
        contract=TaskContract(feature_id="F-1", owner_role="dev", wave=3),
    ))
    base_task_map_ref = ".zf/artifacts/F-1/task_map.json"
    amended_task_map_ref = ".zf/artifacts/F-1/gap-amends/evt-gap/task_map.json"
    gap_plan_ref = "reports/F-1/goal-gap-plan.json"
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(
        type="goal.rescan.requested",
        id="goal-scan-1",
        payload={"pdd_id": "F-1", "task_map_ref": base_task_map_ref},
    ))
    log.append(ZfEvent(
        type="goal.rescan.completed",
        id="goal-scan-2",
        payload={"pdd_id": "F-1", "task_map_ref": base_task_map_ref},
    ))
    log.append(ZfEvent(
        type="goal.gap_plan.ready",
        id="goal-gap-1",
        payload={
            "pdd_id": "F-1",
            "goal_kind": "issue",
            "gap_category": "issue_gap",
            "task_map_ref": base_task_map_ref,
            "gap_plan_ref": gap_plan_ref,
            "replan_history_ref": "docs/plans/F-1/replan-history.jsonl",
            "gap_tasks": [{"task_id": "ISSUE-GAP-001"}],
        },
    ))
    log.append(ZfEvent(
        type="task_map.amended",
        id="goal-amend-1",
        payload={
            "pdd_id": "F-1",
            "task_map_ref": base_task_map_ref,
            "new_task_map_ref": amended_task_map_ref,
            "gap_plan_ref": gap_plan_ref,
            "gap_task_ids": ["ISSUE-GAP-001"],
        },
    ))
    log.append(ZfEvent(
        type="task_map.ready",
        id="goal-ready-1",
        payload={
            "pdd_id": "F-1",
            "task_map_ref": amended_task_map_ref,
            "resume_scope": "gap_tasks_only",
            "task_ids": ["ISSUE-GAP-001"],
        },
    ))

    r = client.get("/api/projects/default/delivery-traces/F-1")

    assert r.status_code == 200
    loop = r.json()["goal_closure_loop"]
    assert loop["schema_version"] == "goal-closure-loop.v1"
    assert loop["status"] == "gap_tasks_dispatched"
    assert loop["gap_task_ids"] == ["ISSUE-GAP-001"]
    assert loop["latest_replan_history_ref"] == "docs/plans/F-1/replan-history.jsonl"


def test_delivery_trace_endpoint_includes_flow_neutral_goal_closure_loop(
    client: TestClient,
    state_dir: Path,
):
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="PRD-GAP-001",
        title="fill product gap",
        status="todo",
        assigned_to="dev-gap",
        contract=TaskContract(feature_id="F-1", owner_role="dev", wave=3),
    ))
    base_task_map_ref = ".zf/artifacts/F-1/task_map.json"
    amended_task_map_ref = ".zf/artifacts/F-1/gap-amends/evt-flow/task_map.json"
    gap_plan_ref = "reports/F-1/flow-gap-plan.json"
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(
        type="flow.discovery.requested",
        id="flow-scan-1",
        payload={"pdd_id": "F-1", "flow_kind": "prd", "task_map_ref": base_task_map_ref},
    ))
    log.append(ZfEvent(
        type="flow.discovery.completed",
        id="flow-scan-2",
        payload={"pdd_id": "F-1", "flow_kind": "prd", "task_map_ref": base_task_map_ref},
    ))
    log.append(ZfEvent(
        type="flow.gap_plan.ready",
        id="flow-gap-1",
        payload={
            "pdd_id": "F-1",
            "flow_kind": "prd",
            "goal_kind": "prd",
            "gap_category": "acceptance_gap",
            "task_map_ref": base_task_map_ref,
            "gap_plan_ref": gap_plan_ref,
            "gap_tasks": [{"task_id": "PRD-GAP-001"}],
        },
    ))
    log.append(ZfEvent(
        type="task_map.amended",
        id="flow-amend-1",
        payload={
            "pdd_id": "F-1",
            "task_map_ref": base_task_map_ref,
            "new_task_map_ref": amended_task_map_ref,
            "gap_plan_ref": gap_plan_ref,
            "gap_task_ids": ["PRD-GAP-001"],
        },
    ))
    log.append(ZfEvent(
        type="task_map.ready",
        id="flow-ready-1",
        payload={
            "pdd_id": "F-1",
            "task_map_ref": amended_task_map_ref,
            "resume_scope": "gap_tasks_only",
            "task_ids": ["PRD-GAP-001"],
        },
    ))

    r = client.get("/api/projects/default/delivery-traces/F-1")

    assert r.status_code == 200
    loop = r.json()["goal_closure_loop"]
    assert loop["schema_version"] == "goal-closure-loop.v1"
    assert loop["status"] == "gap_tasks_dispatched"
    assert loop["scan_request_count"] == 1
    assert loop["scan_result_count"] == 1
    assert loop["gap_task_ids"] == ["PRD-GAP-001"]
    assert loop["latest_gap_plan_ref"] == gap_plan_ref


def test_delivery_features_endpoint(client: TestClient):
    r = client.get("/api/projects/default/delivery-features")

    assert r.status_code == 200
    data = r.json()
    feature_ids = {
        item["id"]
        for item in [*data["delivery_features"], *data["features"]]
    }
    assert "F-1" in feature_ids


def test_delivery_thick_trace_sibling_endpoint(client: TestClient, state_dir: Path):
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(
        type="task.rework.triage.completed",
        id="e-rework",
        task_id="T2",
        payload={"classification": "evidence_payload_gap", "reason": "missing test evidence"},
    ))

    r = client.get("/api/projects/default/delivery-traces/F-1/thick")

    assert r.status_code == 200
    data = r.json()
    assert data["schema_version"] == "delivery-thick-trace.v1"
    assert data["target"]["id"] == "F-1"
    assert any(item["kind"] == "missing_evidence" for item in data["behaviors"])
    assert any(node["kind"] == "behavior" for node in data["graph"]["nodes"])


def test_delivery_trace_includes_related_loop_refs(client: TestClient, state_dir: Path):
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(
        type="static_gate.failed",
        id="gate-fail",
        task_id="T2",
        payload={"feature_id": "F-1", "reason": "pytest failed"},
    ))
    kanban = state_dir / "kanban.json"
    before = (kanban.stat().st_mtime_ns, kanban.read_bytes())

    r = client.get("/api/projects/default/delivery-traces/F-1")

    after = (kanban.stat().st_mtime_ns, kanban.read_bytes())
    assert r.status_code == 200
    data = r.json()
    assert data["related_loop_count"] == 1
    assert data["related_loop_ids"][0].startswith("loop:gate_failure:")
    assert data["thick_trace"]["related_loop_ids"] == data["related_loop_ids"]
    assert before == after


def test_web_delivery_includes_replan_gate_projection(
    client: TestClient,
    state_dir: Path,
):
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(
        type="replan.contract_eval.completed",
        id="eval-web",
        payload={
            "feature_id": "F-1",
            "eval_id": "eval-web",
            "decision": "revise",
            "profile": "baseline",
            "failed_checks": ["resume_safety"],
            "new_task_map_ref": "artifacts/F-1/task_map-v2.json",
        },
    ))

    r = client.get("/api/projects/default/delivery-traces/F-1")

    assert r.status_code == 200
    gate = r.json()["replan_contract_gate"]
    closed_loop = r.json()["closed_loop"]
    assert gate["latest_eval"]["eval_id"] == "eval-web"
    assert gate["latest_eval"]["failed_checks"] == ["resume_safety"]
    assert any(node["kind"] == "contract_gate" for node in closed_loop["nodes"])
    assert "adopt" not in {
        route.path
        for route in client.app.routes
        if "replan" in route.path and "adopt" in route.path
    }


def test_delivery_trace_cursor_deltas(client: TestClient, state_dir: Path):
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(
        type="verify.failed",
        id="e-after",
        task_id="T2",
        payload={"feature_id": "F-1", "stage_id": "verify", "reason": "coverage gap"},
    ))

    r = client.get("/api/projects/default/delivery-traces/F-1?since_event_id=e-build")

    assert r.status_code == 200
    data = r.json()
    assert data["schema_version"] == "delivery-trace.v1"
    assert data["cursor"]["since_event_id"] == "e-build"
    assert data["cursor"]["new_event_count"] == 1
    assert data["cursor"]["degraded"] is False
    assert data["deltas"][0]["event_id"] == "e-after"
    assert data["deltas"][0]["type"] == "stage.status_changed"
    assert data["deltas"][0]["stage_id"] == "verify"


def test_delivery_trace_unknown_cursor_degrades(client: TestClient):
    r = client.get("/api/projects/default/delivery-traces/F-1?since_event_id=missing")

    assert r.status_code == 200
    data = r.json()
    assert data["cursor"]["degraded"] is True
    assert data["deltas"][0]["type"] == "cursor.degraded"
    assert "missing" in data["cursor"]["reason"]


def test_execution_graph_endpoint(client: TestClient):
    r = client.get("/api/projects/default/delivery-traces/F-1/execution-graph")
    assert r.status_code == 200
    data = r.json()
    assert data["schema_version"] == "execution-graph.v1"
    assert {n["task_id"] for n in data["nodes"]} == {"T1", "T2"}
    # blocked_by edge T1->T2 satisfied (T1 done)
    edges = {(e["from"], e["to"]): e for e in data["edges"]}
    assert edges[("T1", "T2")]["status"] == "satisfied"


def test_drift_report_endpoint(client: TestClient):
    r = client.get("/api/projects/default/delivery-traces/F-1/drift-report")
    assert r.status_code == 200
    data = r.json()
    assert data["schema_version"] == "drift-report.v1"
    assert "summary" in data


def test_workflow_run_endpoint(state_dir: Path):
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(type="fanout.started", payload={
        "fanout_id": "FX", "trace_id": "tr", "stage_id": "review", "topology": "fanout_reader",
        "expected_children": [{"child_id": "c1", "role_instance": "review-c1"}],
    }, correlation_id="tr"))
    log.append(ZfEvent(type="fanout.child.dispatched", payload={"fanout_id": "FX", "child_id": "c1"}))
    client = TestClient(create_app(state_dir))
    r = client.get("/api/projects/default/workflow-runs/FX")
    assert r.status_code == 200
    data = r.json()
    assert data["schema_version"] == "workflow-run.v1"
    assert data["fanout_id"] == "FX"
    assert data["status"] == "running"


def test_delivery_trace_does_not_mutate_state(client: TestClient, state_dir: Path):
    kanban = state_dir / "kanban.json"
    before = (kanban.stat().st_mtime_ns, kanban.read_bytes())
    client.get("/api/projects/default/delivery-traces/F-1")
    after = (kanban.stat().st_mtime_ns, kanban.read_bytes())
    assert before == after  # read-only projection
