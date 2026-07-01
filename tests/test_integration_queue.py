from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.integration_queue import build_integration_queue, read_integration_queue


def _state(tmp_path: Path) -> tuple[Path, EventLog, EventWriter]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    return state_dir, log, EventWriter(log)


def _fanout_started(fanout_id: str) -> ZfEvent:
    return ZfEvent(
        type="fanout.started",
        task_id="TASK-STALE",
        payload={
            "fanout_id": fanout_id,
            "stage_id": "impl",
            "target_ref": "candidate/CJMIN-1",
            "pdd_id": "CJMIN-1",
        },
    )


def test_integration_queue_tracks_failure_and_idempotent_retry(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    writer.append(ZfEvent(
        type="task.integration_enqueued",
        actor="zf-cli",
        task_id="TASK-GW-1",
        payload={
            "queue_entry_id": "iq-gateway",
            "fanout_instance_id": "fanout-current",
            "source_ref": "refs/zf/TASK-GW-1/dev",
            "base_ref": "main",
            "handoff_ref": "artifacts/handoff/TASK-GW-1.json",
            "artifact_refs": ["artifacts/impl/gateway.md"],
            "verification_refs": ["artifacts/verify/gateway.log"],
            "reason": "isolated lane completed",
        },
    ))
    writer.append(ZfEvent(
        type="integration.queue.integrating",
        actor="integrator",
        payload={"queue_entry_id": "iq-gateway"},
    ))
    writer.append(ZfEvent(
        type="integration.failed",
        actor="integrator",
        task_id="TASK-GW-1",
        payload={
            "queue_entry_id": "iq-gateway",
            "reason": "merge conflict",
            "verification_refs": ["artifacts/verify/conflict.log"],
        },
    ))
    writer.append(ZfEvent(
        type="integration.queue.retry_requested",
        actor="operator",
        payload={
            "queue_entry_id": "iq-gateway",
            "idempotency_key": "retry-once",
        },
    ))
    log.append(ZfEvent(
        type="integration.queue.retry_requested",
        actor="operator",
        payload={
            "queue_entry_id": "iq-gateway",
            "idempotency_key": "retry-once",
        },
    ))

    projection = read_integration_queue(state_dir)
    entry = projection["entries"][0]

    assert projection["summary"]["queued"] == 1
    assert entry["status"] == "queued"
    assert entry["retry_count"] == 1
    assert entry["reason"] == "merge conflict"
    assert entry["handoff_ref"] == "artifacts/handoff/TASK-GW-1.json"
    assert entry["artifact_refs"] == ["artifacts/impl/gateway.md"]
    assert entry["verification_refs"] == [
        "artifacts/verify/gateway.log",
        "artifacts/verify/conflict.log",
    ]


def test_integration_queue_rejects_illegal_transition() -> None:
    events = [
        ZfEvent(
            type="task.integration_enqueued",
            task_id="TASK-1",
            payload={"queue_entry_id": "iq1", "source_ref": "refs/zf/TASK-1"},
        ),
        ZfEvent(
            type="integration.queue.integrated",
            payload={"queue_entry_id": "iq1", "reason": "skipped integrating"},
        ),
    ]

    projection = build_integration_queue(events)
    entry = projection["entries"][0]

    assert entry["status"] == "queued"
    assert projection["summary"]["issue_count"] == 1
    assert entry["issues"][0]["reason"] == "illegal_transition:queued->integrated"


def test_integration_queue_stale_entry_does_not_pollute_current_queue() -> None:
    projection = build_integration_queue([
        ZfEvent(
            type="task.integration_enqueued",
            task_id="TASK-STALE",
            payload={
                "queue_entry_id": "iq-stale",
                "fanout_instance_id": "fanout-old",
                "source_ref": "refs/zf/TASK-STALE/dev",
                "stale": True,
            },
        )
    ])

    assert projection["entries"] == []
    assert projection["summary"]["stale_rejected"] == 1
    assert projection["stale_entries"][0]["status"] == "stale_rejected"
    assert projection["issues"][0]["reason"] == "stale_queue_event_rejected"


def test_integration_queue_rejects_superseded_fanout_entry() -> None:
    projection = build_integration_queue([
        _fanout_started("fanout-old"),
        _fanout_started("fanout-new"),
        ZfEvent(
            type="task.integration_enqueued",
            task_id="TASK-STALE",
            payload={
                "queue_entry_id": "iq-old",
                "fanout_instance_id": "fanout-old",
                "source_ref": "refs/zf/TASK-STALE/dev",
            },
        ),
        ZfEvent(
            type="task.integration_enqueued",
            task_id="TASK-STALE",
            payload={
                "queue_entry_id": "iq-new",
                "fanout_instance_id": "fanout-new",
                "source_ref": "refs/zf/TASK-STALE/dev-retry",
            },
        ),
    ])

    entries = {item["id"]: item for item in projection["entries"]}

    assert sorted(entries) == ["iq-new"]
    assert projection["summary"]["stale_rejected"] == 1
    assert projection["stale_entries"][0]["id"] == "iq-old"
    assert projection["stale_entries"][0]["stale_reason"] == (
        "superseded_by_latest_fanout"
    )
    assert projection["stale_entries"][0]["superseded_by"] == "fanout-new"
    assert projection["issues"][0]["reason"] == "stale_queue_event_rejected"


def test_web_api_projects_integration_queue(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from zf.web.server import create_app

    state_dir, _, writer = _state(tmp_path)
    writer.append(ZfEvent(
        type="task.integration_enqueued",
        actor="zf-cli",
        task_id="TASK-WEB-1",
        payload={
            "queue_entry_id": "iq-web",
            "source_ref": "refs/zf/TASK-WEB-1/dev",
        },
    ))
    writer.append(ZfEvent(
        type="integration.queue.needs_review",
        actor="integrator",
        payload={"queue_entry_id": "iq-web", "reason": "manual review"},
    ))

    client = TestClient(create_app(state_dir))
    response = client.get("/api/integration-queue")
    data = response.json()

    assert response.status_code == 200
    assert data["schema_version"] == "integration-queue.v1"
    assert data["summary"]["needs_review"] == 1
    assert data["entries"][0]["id"] == "iq-web"
    assert data["entries"][0]["reason"] == "manual review"

    projects = client.get("/api/workspace/projects").json()
    project_id = projects["active_project_id"]
    scoped = client.get(f"/api/projects/{project_id}/integration-queue")
    scoped_data = scoped.json()

    assert scoped.status_code == 200
    assert scoped_data["schema_version"] == "integration-queue.v1"
    assert scoped_data["entries"][0]["id"] == "iq-web"
