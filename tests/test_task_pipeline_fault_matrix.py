from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    WorkflowConfig,
    WorkflowTaskAttemptConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.task_attempts import (
    TASK_ATTEMPT_IDENTITY_OPERATION_V2,
    TaskAttemptStore,
)
from zf.runtime.task_attempt_runtime import reconcile_task_attempts
from zf.runtime.task_attempt_recovery import (
    pending_task_attempt_recovery_actions,
)
from zf.runtime.task_pipeline_recovery import (
    project_task_pipeline_recovery,
    reconcile_task_pipeline_redrives,
    task_pipeline_fault_contract,
)
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    reduce_workflow_operations,
)


_FAULTS = {
    "pane_dead",
    "lease_expired",
    "provider_stop",
    "wrc_restart",
    "candidate_head_cas_mismatch",
    "late_result",
    "cancel",
    "semantic_rework",
}


def _runtime(tmp_path: Path) -> SimpleNamespace:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    return SimpleNamespace(
        state_dir=state_dir,
        project_root=tmp_path,
        config=ZfConfig(
            project=ProjectConfig(name="task-pipeline-recovery"),
            workflow=WorkflowConfig(
                task_attempt=WorkflowTaskAttemptConfig(
                    mode="enforce",
                    max_attempts=3,
                ),
            ),
        ),
        event_log=log,
        event_writer=EventWriter(log),
    )


def _running_operation_v2_attempt(runtime: SimpleNamespace) -> dict:
    service = WorkflowOperationService(
        state_dir=runtime.state_dir,
        event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    )
    ensured = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-task-a-impl-g1",
        operation_type="task-stage",
        request={
            "task_pipeline_stage": "impl",
            "operation_generation": 1,
            "task_map_generation": "map-g1",
            "prompt": "implement",
        },
        task_id="TASK-A",
    )
    attempt = TaskAttemptStore(
        runtime.state_dir / "task_attempts.json"
    ).ensure_for_dispatch(
        run_id="run-1",
        task_id="TASK-A",
        dispatch_id="dispatch-1",
        role="impl",
        instance_id="impl-1",
        operation_id="op-task-a-impl-g1",
        briefing_ref="briefings/TASK-A-impl.md",
        created_at="2026-08-03T00:00:00+00:00",
        lease_expires_at="2026-08-03T00:01:00+00:00",
        max_attempts=3,
        identity_version=TASK_ATTEMPT_IDENTITY_OPERATION_V2,
        placement_epoch=1,
    ).attempt
    service.mark_started(
        operation_id="op-task-a-impl-g1",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="TASK-A",
        dispatch_id="dispatch-1",
        role_instance="impl-1",
        active_attempt_id=attempt["attempt_id"],
        lease_id=attempt["lease_id"],
    )
    return attempt


def test_fault_matrix_has_one_decision_and_effect_owner_per_fault() -> None:
    contracts = {
        fault: task_pipeline_fault_contract(fault)
        for fault in _FAULTS
    }

    assert set(contracts) == _FAULTS
    assert all(row["decision_owner"] for row in contracts.values())
    assert all(row["effect_owner"] for row in contracts.values())
    assert all(isinstance(row["decision_owner"], str) for row in contracts.values())
    assert all(isinstance(row["effect_owner"], str) for row in contracts.values())
    assert contracts["lease_expired"]["decision_owner"] == "run_manager"
    assert contracts["lease_expired"]["effect_owner"] == (
        "workflow_runtime_coordinator"
    )
    with pytest.raises(ValueError, match="unsupported Task Pipeline fault"):
        task_pipeline_fault_contract("unknown")


def test_live_pipeline_attempt_fences_only_its_role(tmp_path: Path) -> None:
    from zf.runtime.task_pipeline_attempt_recovery import live_task_pipeline_attempt_for_role

    runtime = _runtime(tmp_path)
    attempt = _running_operation_v2_attempt(runtime)
    assert live_task_pipeline_attempt_for_role(runtime, "impl-1")["attempt_id"] == attempt["attempt_id"]
    assert live_task_pipeline_attempt_for_role(runtime, "other") is None
    TaskAttemptStore(runtime.state_dir / "task_attempts.json").update(
        attempt["attempt_id"], status="expired", updated_at="2026-08-03T00:03:00+00:00",
    )
    assert live_task_pipeline_attempt_for_role(runtime, "impl-1") is None


def test_expired_operation_v2_waits_for_run_manager_then_redrives_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    first_attempt = _running_operation_v2_attempt(runtime)
    monkeypatch.setattr(
        "zf.runtime.task_attempt_runtime._expired",
        lambda _value: True,
    )

    assert reconcile_task_attempts(runtime) >= 1
    assert reconcile_task_attempts(runtime) == 0

    store = TaskAttemptStore(runtime.state_dir / "task_attempts.json")
    expired = store.get(first_attempt["attempt_id"])
    assert expired is not None
    assert expired["status"] == "expired"
    assert expired["recovery_owner"] == "run_manager"
    events = runtime.event_log.read_all()
    assert len([
        event for event in events
        if event.type == "workflow.operation.interrupted"
    ]) == 1
    assert len([
        event for event in events
        if event.type == "task.attempt.failed"
        and event.payload.get("attempt_id") == first_attempt["attempt_id"]
    ]) == 1
    assert not [
        event for event in events
        if event.type == "task.attempt.retry_scheduled"
    ]
    operation = reduce_workflow_operations(events)["op-task-a-impl-g1"]
    assert operation["status"] == "suspended"
    pending = pending_task_attempt_recovery_actions(
        runtime.state_dir / "projections",
        canonical_store_path=store.path,
    )
    assert len(pending) == 1
    assert pending[0]["operation_id"] == "op-task-a-impl-g1"
    assert pending[0]["attempt_id"] == first_attempt["attempt_id"]
    assert pending[0]["identity_version"] == "operation-v2"
    assert pending[0]["placement_epoch"] == 1
    assert pending[0]["recovery_decision_owner"] == "run_manager"
    assert pending[0]["recovery_effect_owner"] == (
        "workflow_runtime_coordinator"
    )

    request = runtime.event_writer.append(ZfEvent(
        type="worker.respawn.requested",
        actor="run-manager",
        task_id="TASK-A",
        payload={
            "operation_id": "op-task-a-impl-g1",
            "attempt_id": first_attempt["attempt_id"],
            "recovery_decision_owner": "run_manager",
            "recovery_effect_owner": "workflow_runtime_coordinator",
        },
    ))
    runtime.event_writer.append(ZfEvent(
        type="worker.respawn.completed",
        actor="impl-1",
        task_id="TASK-A",
        causation_id=request.id,
        payload={"reason": "respawned"},
    ))
    contexts = {"TASK-A": {"workflow_run_id": "run-1"}}

    first = reconcile_task_pipeline_redrives(
        runtime,
        generation_contexts=contexts,
    )
    replay_after_wrc_restart = reconcile_task_pipeline_redrives(
        runtime,
        generation_contexts=contexts,
    )

    assert [decision.action for decision in first] == [
        "task_pipeline_operation_redrive_admitted"
    ]
    assert replay_after_wrc_restart == []
    operation = reduce_workflow_operations(runtime.event_log.read_all())[
        "op-task-a-impl-g1"
    ]
    assert operation["status"] == "requested"
    assert operation["redrive_count"] == 1
    second_attempt = store.ensure_for_dispatch(
        run_id="run-1",
        task_id="TASK-A",
        dispatch_id="dispatch-2",
        role="impl",
        instance_id="impl-2",
        operation_id="op-task-a-impl-g1",
        briefing_ref="briefings/TASK-A-impl-redrive.md",
        created_at="2026-08-03T00:02:00+00:00",
        lease_expires_at="2026-08-03T00:12:00+00:00",
        max_attempts=3,
        identity_version=TASK_ATTEMPT_IDENTITY_OPERATION_V2,
        placement_epoch=2,
    ).attempt
    assert second_attempt["operation_id"] == first_attempt["operation_id"]
    assert second_attempt["ordinal"] == 2
    assert second_attempt["placement_epoch"] == 2

    recovery = project_task_pipeline_recovery(
        events=runtime.event_log.read_all(),
        attempts=store.rows(),
    )
    assert recovery["run_manager_authority"] == (
        "unique_recovery_decision_owner"
    )
    assert recovery["wrc_authority"] == "frozen_mechanical_effect_executor"
    assert len(recovery["admitted_redrives"]) == 1


def test_retryable_respawn_failure_with_identity_redrives_same_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    first_attempt = _running_operation_v2_attempt(runtime)
    monkeypatch.setattr(
        "zf.runtime.task_attempt_runtime._expired",
        lambda _value: True,
    )
    assert reconcile_task_attempts(runtime) >= 1
    runtime.event_writer.append(ZfEvent(
        type="worker.respawn.failed",
        actor="impl-1",
        task_id="TASK-A",
        payload={
            "task_id": "TASK-A",
            "operation_id": first_attempt["operation_id"],
            "attempt_id": first_attempt["attempt_id"],
            "recovery_retryable": True,
            "recovery_context_bound": True,
            "recovery_decision_owner": "run_manager",
            "recovery_effect_owner": "workflow_runtime_coordinator",
        },
    ))

    decisions = reconcile_task_pipeline_redrives(
        runtime,
        generation_contexts={"TASK-A": {"workflow_run_id": "run-1"}},
    )

    assert [item.action for item in decisions] == [
        "task_pipeline_operation_redrive_admitted",
    ]
    assert reduce_workflow_operations(runtime.event_log.read_all())[
        first_attempt["operation_id"]
    ]["redrive_count"] == 1
