from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.goal_dossier import (
    GoalDossierError,
    build_goal_dossier,
    write_goal_dossier_projection,
)
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import (
    build_plan_artifact_package,
    package_event_payload,
    write_plan_artifact_package,
)
from zf.runtime.run_contract import stable_json_sha256, write_run_contract_snapshot
from zf.runtime.workflow_anchor import mark_workflow_fanout_anchor
from zf.web.server import create_app


NOW = datetime(2026, 7, 21, 6, 30, tzinfo=timezone.utc)


def _state(tmp_path: Path) -> tuple[Path, EventLog]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-A", title="A", status="done", assigned_to="dev-a"))
    store.add(Task(id="TASK-B", title="B", status="done", assigned_to="dev-b"))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        id="evt-run-a-start",
        type="run.goal.started",
        correlation_id="trace-a",
        payload={
            "run_id": "run-a",
            "goal_id": "GOAL-A",
            "objective": "deliver A TOKEN=secret-value",
            "token": "secret-value",
        },
    ))
    log.append(ZfEvent(
        id="evt-run-a-task",
        type="dev.build.done",
        task_id="TASK-A",
        correlation_id="trace-a",
        payload={
            "run_id": "run-a",
            "dispatch_id": "dispatch-a",
            "evidence_refs": ["artifacts/a/result.json"],
            "artifact_digest": "sha256:a",
        },
    ))
    log.append(ZfEvent(
        id="evt-run-a-complete",
        type="run.goal.completed",
        correlation_id="trace-a",
        payload={"run_id": "run-a", "goal_id": "GOAL-A"},
    ))
    log.append(ZfEvent(
        id="evt-run-b-start",
        type="run.goal.started",
        correlation_id="trace-b",
        payload={"run_id": "run-b", "goal_id": "GOAL-B", "objective": "deliver B"},
    ))
    log.append(ZfEvent(
        id="evt-run-b-task",
        type="dev.build.done",
        task_id="TASK-B",
        correlation_id="trace-b",
        payload={"run_id": "run-b", "dispatch_id": "dispatch-b"},
    ))
    return state_dir, log


def test_goal_dossier_is_run_scoped_redacted_and_rebuildable(tmp_path: Path) -> None:
    state_dir, _log = _state(tmp_path)
    event_digest = _sha256(state_dir / "events.jsonl")
    task_digest = _sha256(state_dir / "kanban.json")

    first = build_goal_dossier(state_dir, "trace-a", now=NOW)
    projection = write_goal_dossier_projection(state_dir, first)

    assert projection == (
        state_dir / "projections/goals/run-a/goal-dossier.v1.json"
    )
    assert first["run_id"] == "run-a"
    assert first["requested_run_id"] == "trace-a"
    assert first["goal"]["status"] == "complete"
    assert first["closure"]["status"] == "goal_completed"
    assert first["state"]["task_counts"] == {"total": 1, "terminal": 1, "open": 0}
    assert first["state"]["tasks"][0]["id"] == "TASK-A"
    assert "TASK-B" not in str(first)
    assert "secret-value" not in str(first)
    assert "[REDACTED_SECRET]" in str(first)
    assert first["source_manifest"]["artifact_refs"] == ["artifacts/a/result.json"]

    projection.unlink()
    second = build_goal_dossier(state_dir, "run-a", now=NOW)
    assert second["source_fingerprint"] == first["source_fingerprint"]
    assert second["source_manifest"] == first["source_manifest"]
    assert _sha256(state_dir / "events.jsonl") == event_digest
    assert _sha256(state_dir / "kanban.json") == task_digest


def test_goal_dossier_unknown_run_fails_closed(tmp_path: Path) -> None:
    state_dir, _log = _state(tmp_path)

    with pytest.raises(GoalDossierError, match="unknown run_id"):
        build_goal_dossier(state_dir, "missing-run")


def test_goal_dossier_projects_current_plan_package_and_history(tmp_path: Path) -> None:
    state_dir, log = _state(tmp_path)
    contract = {
        "schema_version": "run-contract.v1",
        "workflow": {"kind": "prd"},
    }
    contract["contract_digest"] = stable_json_sha256(contract)
    run_contract = write_run_contract_snapshot(state_dir, contract)

    def package(revision: str, generation: str):
        ports = []
        for name in (
            "requirement_spec",
            "goal_claim_set",
            "task_map",
            "planning_result",
        ):
            descriptor = write_immutable_json_sidecar(
                state_dir,
                {"schema_version": f"{name}.v1", "revision": revision},
                root=f"dossier-fixtures/{name}",
                kind=name,
                schema_version=f"{name}.v1",
                created_by="test",
            )
            ports.append({
                "logical_name": name,
                "artifact_kind": name,
                "schema_version": f"{name}.v1",
                "producer_stage_id": "prd-plan",
                "ref": descriptor["ref"],
                "sha256": descriptor["sha256"],
            })
        body = build_plan_artifact_package(
            workflow_run_id="run-a",
            flow_kind="prd",
            producer_stage_id="prd-plan",
            run_contract=run_contract,
            plan_revision=revision,
            task_map_generation=generation,
            produced=ports,
            required_ports=[item["logical_name"] for item in ports],
        )
        return body, write_plan_artifact_package(state_dir, body)

    first, first_ref = package("r1", "g1")
    second, second_ref = package("r2", "g2")
    log.append(ZfEvent(
        type="plan.artifact_package.admitted",
        correlation_id="trace-a",
        payload=package_event_payload(first, first_ref, status="admitted"),
    ))
    log.append(ZfEvent(
        type="plan.artifact_package.admitted",
        correlation_id="trace-a",
        payload=package_event_payload(second, second_ref, status="admitted"),
    ))

    dossier = build_goal_dossier(state_dir, "run-a", now=NOW)

    assert dossier["roadmap"]["current_plan_package"]["plan_revision"] == "r2"
    assert dossier["roadmap"]["current_plan_package"]["hydrate_status"] == "ready"
    assert dossier["roadmap"]["plan_package_history"][0]["plan_revision"] == "r1"
    assert dossier["roadmap"]["plan_package_freshness"]["status"] == "ready"


def test_goal_dossier_cli_writes_json_and_markdown(tmp_path: Path) -> None:
    state_dir, _log = _state(tmp_path)
    out = tmp_path / "reports" / "dossier.md"

    rc = main([
        "report",
        "goal-dossier",
        "--state-dir",
        str(state_dir),
        "--run-id",
        "run-a",
        "--out",
        str(out),
    ])

    assert rc == 0
    assert "Goal Dossier: run-a" in out.read_text(encoding="utf-8")
    assert (state_dir / "projections/goals/run-a/goal-dossier.v1.json").is_file()


def test_goal_dossier_web_endpoint_is_read_only(tmp_path: Path) -> None:
    state_dir, _log = _state(tmp_path)
    before = _sha256(state_dir / "events.jsonl")
    client = TestClient(create_app(state_dir))

    response = client.get("/api/runs/run-a/dossier")
    preview = client.get("/api/runs/run-a/dossier?preview=true")
    section = client.get("/api/runs/run-a/dossier?section=closure")
    bad_section = client.get("/api/runs/run-a/dossier?section=unknown")
    missing = client.get("/api/runs/missing/dossier")

    assert response.status_code == 200
    assert response.json()["schema_version"] == "goal-dossier.v1"
    assert response.json()["run_id"] == "run-a"
    assert preview.status_code == 200
    assert preview.json()["view"] == "preview"
    assert preview.json()["task_counts"]["total"] == 1
    assert section.status_code == 200
    assert section.json()["section"] == "closure"
    assert section.json()["data"]["status"] == "goal_completed"
    assert bad_section.status_code == 404
    assert missing.status_code == 404
    assert _sha256(state_dir / "events.jsonl") == before
    assert not (state_dir / "projections/goals/run-a/goal-dossier.v1.json").exists()


def test_goal_dossier_groups_and_settles_failure_incidents(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-A", title="A", status="done",
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        id="evt-start",
        type="run.goal.started",
        payload={"run_id": "run-incidents", "goal_id": "GOAL-A"},
    ))
    for index in range(2):
        log.append(ZfEvent(
            id=f"evt-fail-{index}",
            type="verify.failed",
            task_id="TASK-A",
            payload={
                "run_id": "run-incidents",
                "failure_fingerprint": "same-gap",
                "reason": "expected output missing",
            },
        ))

    active = build_goal_dossier(state_dir, "run-incidents", now=NOW)

    assert len(active["incident_history"]) == 1
    assert active["incident_history"][0]["status"] == "active"
    assert active["incident_history"][0]["count"] == 2
    failure_gaps = [
        gap for gap in active["gaps"]
        if gap["type"] == "failure_incident"
    ]
    assert len(failure_gaps) == 1
    assert failure_gaps[0]["occurrence_count"] == 2

    log.append(ZfEvent(
        id="evt-pass",
        type="verify.passed",
        task_id="TASK-A",
        payload={"run_id": "run-incidents"},
    ))
    settled = build_goal_dossier(state_dir, "run-incidents", now=NOW)

    assert settled["incident_history"][0]["status"] == "resolved"
    assert settled["incident_history"][0]["resolved_by_event_id"] == "evt-pass"
    assert not [
        gap for gap in settled["gaps"]
        if gap["type"] == "failure_incident"
    ]


def test_goal_dossier_excludes_workflow_anchor_from_task_roadmap(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-A", title="A", status="done"))
    store.add(mark_workflow_fanout_anchor(Task(
        id="TASK-ROOT", title="workflow root", status="backlog",
    )))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        id="evt-start",
        type="run.goal.started",
        payload={"run_id": "run-anchor", "goal_id": "GOAL-A"},
    ))
    for task_id in ("TASK-ROOT", "TASK-A"):
        log.append(ZfEvent(
            id=f"evt-{task_id}",
            type="task.assigned",
            task_id=task_id,
            payload={"run_id": "run-anchor"},
        ))

    dossier = build_goal_dossier(state_dir, "run-anchor", now=NOW)

    assert dossier["state"]["task_counts"] == {
        "total": 1, "terminal": 1, "open": 0,
    }
    assert dossier["roadmap"]["task_order"] == ["TASK-A"]
    assert dossier["roadmap"]["workflow_anchor_task_ids"] == ["TASK-ROOT"]
    assert not [
        gap for gap in dossier["gaps"]
        if gap.get("task_id") == "TASK-ROOT"
    ]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
