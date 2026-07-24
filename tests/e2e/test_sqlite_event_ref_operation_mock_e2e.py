from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.web.projections import read_model
from zf.web.server import create_app


def _state(tmp_path: Path) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    return state_dir


def _operation_event(
    event_type: str,
    *,
    event_id: str,
    operation_id: str,
    package_id: str,
    reason: str = "",
) -> ZfEvent:
    return ZfEvent(
        type=event_type,
        id=event_id,
        actor="zf-cli",
        task_id="TASK-1",
        correlation_id="run-1",
        payload={
            "workflow_run_id": "run-1",
            "operation_id": operation_id,
            "operation_type": "impl",
            "request_hash": operation_id.rjust(64, "a")[-64:],
            "task_id": "TASK-1",
            "plan_artifact_package_id": package_id,
            "reason": reason,
        },
    )


def test_stale_and_current_operations_remain_isolated_across_tail_and_rebuild(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    old_operation = "op-r1"
    current_operation = "op-r2"
    log.append(_operation_event(
        "workflow.operation.requested",
        event_id="evt-r1-requested",
        operation_id=old_operation,
        package_id="planpkg-r1",
    ))
    log.append(_operation_event(
        "workflow.operation.failed",
        event_id="evt-r1-failed",
        operation_id=old_operation,
        package_id="planpkg-r1",
        reason="superseded",
    ))
    log.append(_operation_event(
        "workflow.operation.requested",
        event_id="evt-r2-requested",
        operation_id=current_operation,
        package_id="planpkg-r2",
    ))
    log.append(_operation_event(
        "workflow.operation.started",
        event_id="evt-r2-started",
        operation_id=current_operation,
        package_id="planpkg-r2",
    ))
    client = TestClient(create_app(state_dir))

    old = client.get(f"/api/workflow-operations/{old_operation}").json()
    current = client.get(f"/api/workflow-operations/{current_operation}").json()

    assert old["status"] == "failed"
    assert old["freshness"]["event_count"] == 2
    assert current["status"] == "running"
    assert current["freshness"]["event_count"] == 2
    assert current["source"] == "read_model.sqlite"
    current_package_events = read_model.hydrate_events_by_ref(
        state_dir,
        ref_kind="plan_artifact_package_id",
        ref_id="planpkg-r2",
    )
    assert current_package_events is not None
    assert [event.id for event in current_package_events] == [
        "evt-r2-requested",
        "evt-r2-started",
    ]

    log.append(_operation_event(
        "workflow.operation.settled",
        event_id="evt-r2-settled",
        operation_id=current_operation,
        package_id="planpkg-r2",
    ))
    settled = client.get(f"/api/workflow-operations/{current_operation}").json()
    assert settled["status"] == "settled"
    assert settled["freshness"]["event_count"] == 3
    assert settled["timeline"][-1]["event_id"] == "evt-r2-settled"

    sqlite_path = read_model.db_path(state_dir)
    sqlite_path.unlink()
    for suffix in ("-wal", "-shm"):
        sqlite_path.with_name(sqlite_path.name + suffix).unlink(missing_ok=True)

    replayed = client.get(f"/api/workflow-operations/{current_operation}").json()
    assert replayed["source"] == "read_model.sqlite"
    assert replayed["status"] == "settled"
    assert replayed["freshness"]["event_count"] == 3
    assert client.get(f"/api/workflow-operations/{old_operation}").json()["status"] == "failed"
