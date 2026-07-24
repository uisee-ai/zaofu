from __future__ import annotations

import argparse
import json
from pathlib import Path

from fastapi.testclient import TestClient

from zf.cli.trace import run_workflow_operation
from zf.core.config.schema import ProjectConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.operation_projection import project_workflow_operation
from zf.runtime.workflow_resume import build_workflow_resume_projection
from zf.web.server import create_app


def _state(tmp_path: Path) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    return state_dir


def _settled_operation(state_dir: Path) -> str:
    operation_id = "wop-verify-TASK-1"
    request_hash = "a" * 64
    result_ref = {
        "ref_schema_version": "sidecar-ref.v1",
        "kind": "call_result_envelope",
        "ref": "artifacts/call-results/envelopes/result.json",
        "sha256": "b" * 64,
        "byte_count": 2,
        "content_type": "application/json",
        "schema_version": "call-result-envelope.v1",
        "created_by": "test",
        "required": True,
    }
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="workflow.operation.requested",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "workflow_run_id": "run-1",
            "operation_id": operation_id,
            "operation_type": "reader_fanout",
            "request_hash": request_hash,
            "task_id": "TASK-1",
        },
    ))
    log.append(ZfEvent(
        type="workflow.operation.settled",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "workflow_run_id": "run-1",
            "operation_id": operation_id,
            "request_hash": request_hash,
            "task_id": "TASK-1",
            "admitted_call_result_ref": result_ref,
        },
    ))
    return operation_id


def test_workflow_operation_is_consistent_across_projection_web_and_cli(
    tmp_path: Path,
    capsys,
) -> None:
    state_dir = _state(tmp_path)
    operation_id = _settled_operation(state_dir)

    projected = project_workflow_operation(state_dir, operation_id)
    response = TestClient(create_app(state_dir)).get(
        f"/api/workflow-operations/{operation_id}"
    )
    exit_code = run_workflow_operation(argparse.Namespace(
        state_dir=str(state_dir),
        operation_id=operation_id,
        format="json",
    ))
    cli = json.loads(capsys.readouterr().out)

    assert response.status_code == 200
    assert projected["status"] == "settled"
    assert response.json()["admitted_call_result_ref"] == projected["admitted_call_result_ref"]
    assert response.json()["source"] == "read_model.sqlite"
    assert response.json()["projection_state"] == "ready"
    assert exit_code == 0
    assert cli["operation_id"] == operation_id
    assert cli["status"] == "settled"
    assert cli["freshness"]["event_count"] == 2


def test_workflow_operation_web_fast_path_catches_up_active_tail(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    operation_id = _settled_operation(state_dir)
    client = TestClient(create_app(state_dir))

    first = client.get(f"/api/workflow-operations/{operation_id}").json()
    assert first["freshness"]["event_count"] == 2
    assert first["status"] == "settled"

    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="workflow.operation.failed",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "workflow_run_id": "run-1",
            "operation_id": operation_id,
            "request_hash": "a" * 64,
            "task_id": "TASK-1",
            "reason": "late failure",
        },
    ))

    current = client.get(f"/api/workflow-operations/{operation_id}").json()
    assert current["source"] == "read_model.sqlite"
    assert current["status"] == "failed"
    assert current["freshness"]["event_count"] == 3
    assert current["timeline"][-1]["type"] == "workflow.operation.failed"


def test_workflow_operation_web_falls_back_when_read_model_is_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = _state(tmp_path)
    operation_id = _settled_operation(state_dir)
    monkeypatch.setattr(
        "zf.web.projections.operations.read_model.hydrate_events_by_ref",
        lambda *args, **kwargs: None,
    )

    response = TestClient(create_app(state_dir)).get(
        f"/api/workflow-operations/{operation_id}"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "settled"
    assert response.json()["source"] == "events.jsonl"
    assert response.json()["projection_state"] == "fallback"


def test_workflow_operation_web_falls_back_when_read_model_is_corrupt(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    operation_id = _settled_operation(state_dir)
    sqlite_path = state_dir / "projections" / "read_model.sqlite"
    sqlite_path.parent.mkdir(parents=True)
    sqlite_path.write_bytes(b"not-a-sqlite-database")

    response = TestClient(create_app(state_dir)).get(
        f"/api/workflow-operations/{operation_id}"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "settled"
    assert response.json()["source"] == "events.jsonl"
    assert response.json()["projection_state"] == "fallback"


def test_workflow_resume_projection_rebuilds_stable_operation_state(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    operation_id = _settled_operation(state_dir)

    projection = build_workflow_resume_projection(
        state_dir,
        ZfConfig(project=ProjectConfig(name="demo", state_dir=str(state_dir))),
    )

    assert projection["summary"]["workflow_operations"] == 1
    assert projection["summary"]["resumable_operations"] == 0
    assert projection["workflow_operations"][0]["operation_id"] == operation_id
    assert projection["workflow_operations"][0]["status"] == "settled"
