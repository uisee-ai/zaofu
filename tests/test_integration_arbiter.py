from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.integration_arbiter import (
    INTEGRATION_ARBITER_DECISION_EVENT,
    emit_integration_arbiter_decisions,
)
from zf.runtime.integration_queue import build_integration_queue


def _state(tmp_path: Path) -> tuple[Path, EventLog, EventWriter]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    return state_dir, log, EventWriter(log)


def _enqueue(entry_id: str = "iq-1") -> ZfEvent:
    return ZfEvent(
        type="task.integration_enqueued",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "queue_entry_id": entry_id,
            "fanout_instance_id": "fanout-1",
            "source_ref": "refs/zf/TASK-1/dev",
            "base_ref": "dev",
        },
    )


def test_integration_arbiter_blocks_dirty_merge_intent() -> None:
    projection = build_integration_queue(
        [_enqueue()],
        dirty_files=["src/app.py"],
        git_head="abc123",
        git_branch="dev",
    )
    arbiter = projection["arbiter"]
    decision = arbiter["decisions"][0]

    assert arbiter["policy"]["direct_truth_mutation"] is False
    assert arbiter["policy"]["direct_git_merge"] is False
    assert arbiter["dirty_guard"]["dirty"] is True
    assert decision["status"] == "blocked"
    assert decision["decision"] == "start_controlled_integration"
    assert decision["target_event_type"] == "integration.queue.integrating"
    assert decision["controlled_action"]["required"] is True
    assert decision["controlled_action"]["dirty_guard_required"] is True
    assert decision["merge_safety"]["merge_preflight_passed"] is False


def test_integration_arbiter_audit_events_are_replay_idempotent(
    tmp_path: Path,
) -> None:
    _, log, writer = _state(tmp_path)
    writer.append(_enqueue())

    projection = build_integration_queue(log.read_all(), git_head="abc123")
    emitted = emit_integration_arbiter_decisions(writer, projection["arbiter"])

    assert len(emitted) == 1
    assert emitted[0].type == INTEGRATION_ARBITER_DECISION_EVENT
    assert emitted[0].payload["idempotency_key"].startswith(
        "integration-arbiter:start_controlled_integration:iq-1:",
    )

    replayed = build_integration_queue(log.read_all(), git_head="abc123")
    replayed_decision = replayed["arbiter"]["decisions"][0]
    emitted_again = emit_integration_arbiter_decisions(
        writer,
        replayed["arbiter"],
    )

    audit_events = [
        event for event in log.read_all()
        if event.type == INTEGRATION_ARBITER_DECISION_EVENT
    ]
    assert emitted_again == []
    assert len(audit_events) == 1
    assert replayed_decision["status"] == "emitted"
    assert replayed_decision["audit_event_id"] == emitted[0].id


def test_integration_arbiter_needs_review_exposes_retry_discard_options() -> None:
    projection = build_integration_queue([
        _enqueue(),
        ZfEvent(
            type="integration.failed",
            actor="integrator",
            task_id="TASK-1",
            payload={
                "queue_entry_id": "iq-1",
                "reason": "merge conflict",
            },
        ),
    ], git_head="abc123")

    decision = projection["arbiter"]["decisions"][0]
    labels = {item["label"] for item in decision["action_options"]}

    assert decision["queue_status"] == "needs_review"
    assert decision["status"] == "needs_review"
    assert labels == {"retry", "discard"}
    assert all(item["idempotency_key"] for item in decision["action_options"])
    assert decision["controlled_action"]["surface"] == "repair.action.requested"


def test_web_api_exposes_integration_arbiter_projection(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from zf.web.server import create_app

    state_dir, _, writer = _state(tmp_path)
    writer.append(_enqueue("iq-web"))

    client = TestClient(create_app(state_dir, project_root=tmp_path))
    response = client.get("/api/integration-queue")
    data = response.json()

    assert response.status_code == 200
    assert data["arbiter"]["schema_version"] == "integration-arbiter.v1"
    assert data["arbiter"]["decisions"][0]["queue_entry_id"] == "iq-web"
