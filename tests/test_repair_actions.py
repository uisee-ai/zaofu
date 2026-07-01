from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.repair_actions import build_repair_action_projection, read_repair_actions


def _state(tmp_path: Path) -> tuple[Path, EventWriter]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    TaskStore(state_dir / "kanban.json").add(
        Task(id="TASK-1", title="repair target", status="in_progress"),
    )
    return state_dir, EventWriter(EventLog(state_dir / "events.jsonl"))


def test_repair_actions_project_pending_applied_and_rejected(
    tmp_path: Path,
) -> None:
    state_dir, writer = _state(tmp_path)
    writer.append(ZfEvent(
        type="repair.action.requested",
        actor="supervisor",
        task_id="TASK-1",
        payload={
            "action_id": "ra-reemit",
            "kind": "reemit_trigger",
            "idempotency_key": "repair:TASK-1:reemit",
            "stage": "review",
            "reason": "review rejected but rework trigger missing",
            "evidence_refs": ["artifacts/supervisor/reemit.json"],
        },
    ))
    writer.append(ZfEvent(
        type="repair.action.applied",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "action_id": "ra-reemit",
            "kind": "reemit_trigger",
            "reason": "trigger re-emitted",
            "evidence_refs": ["events/repair/reemit.json"],
        },
    ))
    writer.append(ZfEvent(
        type="repair.action.requested",
        actor="autoresearch",
        payload={
            "action_id": "ra-worker",
            "kind": "restart_worker",
            "role": "dev",
            "idempotency_key": "repair:dev:restart",
        },
    ))
    writer.append(ZfEvent(
        type="repair.action.rejected",
        actor="zf-cli",
        payload={
            "action_id": "ra-worker",
            "kind": "restart_worker",
            "reason": "worker backend unavailable",
        },
    ))

    projection = read_repair_actions(state_dir)
    actions = {item["id"]: item for item in projection["actions"]}

    assert projection["summary"]["applied"] == 1
    assert projection["summary"]["rejected"] == 1
    assert actions["ra-reemit"]["status"] == "applied"
    assert actions["ra-reemit"]["reason"] == "trigger re-emitted"
    assert actions["ra-reemit"]["evidence_refs"] == [
        "artifacts/supervisor/reemit.json",
        "events/repair/reemit.json",
    ]
    assert actions["ra-worker"]["status"] == "rejected"
    assert actions["ra-worker"]["reason"] == "worker backend unavailable"


def test_repair_actions_mark_invalid_and_duplicate_requests() -> None:
    events = [
        ZfEvent(
            type="repair.action.requested",
            task_id="TASK-404",
            payload={
                "action_id": "ra-invalid-task",
                "kind": "requeue_task",
                "idempotency_key": "repair:missing",
            },
        ),
        ZfEvent(
            type="repair.action.requested",
            task_id="TASK-1",
            payload={
                "action_id": "ra-1",
                "kind": "requeue_task",
                "idempotency_key": "repair:once",
            },
        ),
        ZfEvent(
            type="repair.action.requested",
            task_id="TASK-1",
            payload={
                "action_id": "ra-2",
                "kind": "requeue_task",
                "idempotency_key": "repair:once",
            },
        ),
        ZfEvent(
            type="repair.action.requested",
            task_id="TASK-1",
            payload={
                "action_id": "ra-unknown",
                "kind": "replace_everything",
                "idempotency_key": "repair:bad-kind",
            },
        ),
        ZfEvent(
            type="repair.action.requested",
            task_id="TASK-1",
            payload={
                "action_id": "ra-rerun-missing-fanout",
                "kind": "rerun_fanout_child",
                "idempotency_key": "repair:missing-fanout",
                "fanout_child_id": "dev-child",
            },
        ),
        ZfEvent(
            type="repair.action.requested",
            task_id="TASK-1",
            payload={
                "action_id": "ra-projection-missing-target",
                "kind": "mark_stale_projection_for_rebuild",
                "idempotency_key": "repair:projection:missing-target",
            },
        ),
        ZfEvent(
            type="repair.action.requested",
            task_id="TASK-1",
            payload={
                "action_id": "ra-iq-retry-missing-entry",
                "kind": "retry_integration_queue_entry",
                "idempotency_key": "repair:integration-queue:retry-missing",
            },
        ),
        ZfEvent(
            type="repair.action.requested",
            task_id="TASK-1",
            payload={
                "action_id": "ra-iq-discard-missing-entry",
                "kind": "discard_integration_queue_entry",
                "idempotency_key": "repair:integration-queue:discard-missing",
            },
        ),
    ]

    projection = build_repair_action_projection(events, valid_task_ids={"TASK-1"})
    actions = {item["id"]: item for item in projection["actions"]}

    assert actions["ra-invalid-task"]["status"] == "invalid"
    assert actions["ra-invalid-task"]["reason"] == "unknown_task:TASK-404"
    assert actions["ra-1"]["status"] == "pending"
    assert actions["ra-2"]["status"] == "duplicate"
    assert actions["ra-unknown"]["status"] == "invalid"
    assert actions["ra-unknown"]["reason"] == "unknown_action_kind:replace_everything"
    assert actions["ra-rerun-missing-fanout"]["status"] == "invalid"
    assert actions["ra-rerun-missing-fanout"]["reason"] == "missing_fanout_id"
    assert actions["ra-projection-missing-target"]["status"] == "invalid"
    assert actions["ra-projection-missing-target"]["reason"] == "missing_projection"
    assert actions["ra-iq-retry-missing-entry"]["status"] == "invalid"
    assert actions["ra-iq-retry-missing-entry"]["reason"] == "missing_queue_entry_id"
    assert actions["ra-iq-discard-missing-entry"]["status"] == "invalid"
    assert actions["ra-iq-discard-missing-entry"]["reason"] == "missing_queue_entry_id"
    assert projection["summary"]["issue_count"] == 7


def test_web_api_projects_repair_actions(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from zf.web.server import create_app

    state_dir, writer = _state(tmp_path)
    writer.append(ZfEvent(
        type="repair.action.requested",
        actor="supervisor",
        task_id="TASK-1",
        payload={
            "action_id": "ra-web",
            "kind": "requeue_task",
            "idempotency_key": "repair:web",
        },
    ))

    client = TestClient(create_app(state_dir))
    response = client.get("/api/repair-actions")
    data = response.json()

    assert response.status_code == 200
    assert data["schema_version"] == "repair-actions.v1"
    assert data["summary"]["pending"] == 1
    assert data["actions"][0]["id"] == "ra-web"

    projects = client.get("/api/workspace/projects").json()
    project_id = projects["active_project_id"]
    scoped = client.get(f"/api/projects/{project_id}/repair-actions")
    scoped_data = scoped.json()

    assert scoped.status_code == 200
    assert scoped_data["schema_version"] == "repair-actions.v1"
    assert scoped_data["actions"][0]["id"] == "ra-web"
