from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from zf.runtime.task_attempt_recovery import pending_task_attempt_recovery_actions


def _write_attempts(tmp_path: Path, tasks: dict) -> Path:
    projections = tmp_path / "projections"
    projections.mkdir()
    (projections / "task_attempts.json").write_text(
        json.dumps({
            "schema_version": "shadow-spine.v1",
            "tasks": tasks,
        }),
        encoding="utf-8",
    )
    return projections


def test_expired_open_attempt_becomes_worker_lifecycle_recover(tmp_path: Path) -> None:
    projections = _write_attempts(tmp_path, {
        "TASK-1": {
            "latest_state": "running",
            "current_owner": "dev-lane-1",
            "open_attempts": 1,
            "counted_failures": 0,
            "attempts": [{
                "attempt_key": "attempt-1",
                "state": "running",
                "role": "dev-lane-1",
                "started_ts": "2026-07-06T20:00:00+00:00",
                "last_heartbeat_ts": "2026-07-06T20:10:00+00:00",
                "source_event_id": "evt-start",
                "lease_token": "lease-1",
                "lease_state": "held",
                "terminal": None,
            }],
        },
    })

    actions = pending_task_attempt_recovery_actions(
        projections,
        now=datetime(2026, 7, 6, 20, 40, tzinfo=timezone.utc),
        lease_grace_s=900,
    )

    assert len(actions) == 1
    action = actions[0]
    assert action["action"] == "worker-lifecycle-recover"
    assert action["safe_resume_action"] == "worker_lifecycle_recover"
    assert action["task_id"] == "TASK-1"
    assert action["instance_id"] == "dev-lane-1"
    assert action["policy_decision"]["decision"] == "auto_decide"
    assert action["preflight"]["status"] == "passed"
    assert action["source_refs"] == ["projections/task_attempts.json#tasks.TASK-1"]


def test_recent_open_attempt_stays_quiet(tmp_path: Path) -> None:
    projections = _write_attempts(tmp_path, {
        "TASK-1": {
            "latest_state": "running",
            "current_owner": "dev-lane-1",
            "attempts": [{
                "attempt_key": "attempt-1",
                "state": "running",
                "role": "dev-lane-1",
                "started_ts": "2026-07-06T20:00:00+00:00",
                "last_heartbeat_ts": "2026-07-06T20:35:00+00:00",
                "source_event_id": "evt-start",
                "terminal": None,
            }],
        },
    })

    assert pending_task_attempt_recovery_actions(
        projections,
        now=datetime(2026, 7, 6, 20, 40, tzinfo=timezone.utc),
        lease_grace_s=900,
    ) == []


def test_retryable_failed_attempt_routes_to_diagnosis(tmp_path: Path) -> None:
    projections = _write_attempts(tmp_path, {
        "TASK-2": {
            "latest_state": "failed",
            "counted_failures": 1,
            "attempts": [{
                "attempt_key": "attempt-2",
                "state": "failed",
                "source_event_id": "evt-start",
                "failure_signature": "task_attempt_failed",
                "retryable": True,
                "terminal": {
                    "type": "task.attempt.failed",
                    "event_id": "evt-failed",
                },
            }],
        },
    })

    actions = pending_task_attempt_recovery_actions(projections)

    assert len(actions) == 1
    action = actions[0]
    assert action["action"] == "diagnose-attention"
    assert action["task_id"] == "TASK-2"
    assert action["failure_class"] == "task_attempt_failed"
    assert action["policy_decision"]["decision"] == "needs_diagnosis"
    assert action["preflight"]["status"] == "passed"
    assert "workflow resume checkpoint is required" in action["reason"]


def test_deadletter_or_exhausted_attempt_routes_to_human(tmp_path: Path) -> None:
    projections = _write_attempts(tmp_path, {
        "TASK-3": {
            "latest_state": "deadlettered",
            "counted_failures": 3,
            "attempts": [{
                "attempt_key": "attempt-3",
                "state": "deadlettered",
                "source_event_id": "evt-start",
                "failure_signature": "task_attempt_failed",
                "retryable": False,
                "terminal": {
                    "type": "task.attempt.deadlettered",
                    "event_id": "evt-dead",
                },
            }],
        },
    })

    actions = pending_task_attempt_recovery_actions(
        projections,
        max_retry_attempts=3,
    )

    assert len(actions) == 1
    action = actions[0]
    assert action["action"] == "diagnose-attention"
    assert action["policy_decision"]["decision"] == "human_escalate"
    assert action["policy_decision"]["executable"] is False
    assert action["intervention_class"] == "safe_halt"
