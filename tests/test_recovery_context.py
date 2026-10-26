from __future__ import annotations

from pathlib import Path

import yaml

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.recovery_context import (
    RECOVERY_CONTEXT_SCHEMA_VERSION,
    build_task_recovery_context,
    write_task_recovery_context,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def test_recovery_context_is_bounded_and_contains_task_failure_and_worker_state(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-1",
        title="fix expiry",
        status="review",
        assigned_to="dev-lane-1",
        blocked_reason="waiting for regression evidence",
        retry_count=3,
        contract=TaskContract(
            feature_id="ISSUE-1",
            behavior="expiry remains enforced",
            plan_ref="docs/plans/issue-1.md",
            source_index_ref=".zf/artifacts/ISSUE-1/source-index.json",
        ),
    ))
    (state_dir / "role_sessions.yaml").write_text(yaml.safe_dump({
        "instance_meta": {
            "dev-lane-1": {
                "backend": "codex",
                "worker_state": "idle",
                "active_task_id": "TASK-1",
                "last_heartbeat_at": "2026-07-10T14:00:00+00:00",
            },
        },
    }), encoding="utf-8")
    failures = [
        ZfEvent(
            id=f"failure-{index}",
            type="review.rejected",
            actor="verify-lane-1",
            task_id="TASK-1",
            payload={"reason": "missing expiry regression", "findings": [f"round {index}"]},
        )
        for index in range(1, 4)
    ]
    events = [
        *failures,
        ZfEvent(
            id="cap-1",
            type="task.rework.capped",
            actor="zf-cli",
            task_id="TASK-1",
            payload={"failure_count": 3},
        ),
    ]

    context = build_task_recovery_context(
        state_dir,
        events,
        task_id="TASK-1",
        failure_event_ids=[event.id for event in failures],
        request_id="triage-1",
    )
    descriptor = write_task_recovery_context(
        state_dir,
        events,
        task_id="TASK-1",
        failure_event_ids=[event.id for event in failures],
        request_id="triage-1",
        source_event_id="cap-1",
    )
    hydrated = hydrate_sidecar_ref(
        state_dir,
        descriptor,
        purpose="test",
        actor="run-manager",
    )

    assert context["schema_version"] == RECOVERY_CONTEXT_SCHEMA_VERSION
    assert context["task"]["contract"]["plan_ref"] == "docs/plans/issue-1.md"
    assert context["failure_ledger"]["failure_count"] == 3
    assert context["failure_ledger"]["failures"][0]["evidence"]["findings"] == ["round 1"]
    assert context["worker"]["worker_state"] == "idle"
    assert hydrated.ok is True
    assert hydrated.payload["request_id"] == "triage-1"


def test_recovery_context_excludes_prior_run_failures_and_foreign_worker(
    tmp_path: Path,
) -> None:
    from zf.core.task.schema import TaskExecutionBinding

    state = tmp_path / "state"
    state.mkdir()
    TaskStore(state / "kanban.json").add(
        Task(
            id="TASK-REUSED",
            assigned_to="dev-0",
            active_dispatch_id="dispatch-new",
            execution_binding=TaskExecutionBinding(workflow_run_id="RUN-NEW"),
        )
    )
    (state / "role_sessions.yaml").write_text(
        yaml.safe_dump(
            {
                "instance_meta": {
                    "dev-0": {
                        "backend": "codex",
                        "active_task_id": "TASK-OTHER",
                        "worker_state": "busy",
                    },
                }
            }
        )
    )
    old = ZfEvent(
        id="old-failure",
        type="verify.failed",
        task_id="TASK-REUSED",
        payload={"workflow_run_id": "RUN-OLD", "reason": "obsolete contract"},
    )
    current = ZfEvent(
        id="new-failure",
        type="verify.failed",
        task_id="TASK-REUSED",
        payload={
            "workflow_run_id": "RUN-NEW",
            "dispatch_id": "dispatch-new",
            "reason": "current evidence missing",
        },
    )
    context = build_task_recovery_context(
        state,
        [current, old],
        task_id="TASK-REUSED",
        failure_event_ids=[old.id, current.id],
    )
    assert [e["event_id"] for e in context["failure_ledger"]["failures"]] == [
        current.id
    ]
    assert context["activity"]["last_event_id"] == current.id
    assert context["worker"]["available"] is False
    assert context["failure_ledger"]["failure_event_ids"] == [current.id]
    assert context["failure_ledger"]["unresolved_failure_event_ids"] == [old.id]
    assert context["execution_boundary"] == {
        "workflow_run_id": "RUN-NEW",
        "dispatch_id": "dispatch-new",
        "binding_status": "bound",
        "authorizes_continuation": False,
    }


def test_recovery_context_unaccepted_request_does_not_inherit_old_evidence(
    tmp_path: Path,
) -> None:
    from zf.core.task.schema import TaskExecutionBinding

    state = tmp_path / "state"
    state.mkdir()
    TaskStore(state / "kanban.json").add(
        Task(
            id="TASK-PENDING",
            execution_binding=TaskExecutionBinding(
                request_id="REQ", request_revision=2
            ),
        )
    )
    old = ZfEvent(
        id="old-accepted",
        type="workflow.invoke.accepted",
        task_id="TASK-PENDING",
        payload={
            "request_id": "REQ",
            "request_revision": 1,
            "workflow_run_id": "RUN-OLD",
        },
    )
    failure = ZfEvent(
        id="old-failure",
        type="verify.failed",
        task_id="TASK-PENDING",
        payload={"workflow_run_id": "RUN-OLD"},
    )
    context = build_task_recovery_context(
        state, [old, failure], task_id="TASK-PENDING", failure_event_ids=[failure.id]
    )
    assert context["failure_ledger"]["failure_count"] == 0
    assert context["activity"]["recent_events"] == []
    assert context["execution_boundary"]["binding_status"] == "unresolved"
    assert context["execution_boundary"]["authorizes_continuation"] is False
