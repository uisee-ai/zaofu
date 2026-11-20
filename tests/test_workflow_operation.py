import json
from pathlib import Path

import zf.runtime.workflow_operation as workflow_operation_module
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    reduce_workflow_operations,
    stable_continuation_idempotency_key,
    stable_operation_id,
)


def _service(tmp_path: Path) -> WorkflowOperationService:
    log = EventLog(tmp_path / "events.jsonl")
    return WorkflowOperationService(
        state_dir=tmp_path,
        event_log=log,
        event_writer=EventWriter(log),
    )


def test_ensure_operation_dedupes_and_fails_closed_on_drift(tmp_path: Path) -> None:
    service = _service(tmp_path)
    operation_id = stable_operation_id(
        workflow_run_id="run-1",
        parent_stage_id="verify",
        operation_key="security",
    )
    first = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id=operation_id,
        operation_type="agent",
        request={"prompt": "review", "dispatch_id": "volatile-1"},
        parent_stage_id="verify",
        task_id="T1",
    )
    replay = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id=operation_id,
        operation_type="agent",
        request={"prompt": "review", "dispatch_id": "volatile-2"},
        parent_stage_id="verify",
        task_id="T1",
    )
    divergent = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id=operation_id,
        operation_type="agent",
        request={"prompt": "different"},
        parent_stage_id="verify",
        task_id="T1",
    )
    assert first.created is True
    assert replay.replay_hit is True
    assert replay.request_hash == first.request_hash
    assert divergent.status == "divergent"
    events = service.event_log.read_all()
    assert sum(event.type == "workflow.operation.requested" for event in events) == 1
    assert sum(event.type == "workflow.operation.blocked" for event in events) == 1
    assert all(
        event.origin == "kernel"
        for event in events
        if event.type.startswith("workflow.operation.")
    )
    view = reduce_workflow_operations(events)[operation_id]
    assert view["request_count"] == 1
    assert view["replay_count"] == 0


def test_task_pipeline_operation_replays_across_placement_relocation(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    common_request = {
        "task_pipeline_stage": "impl",
        "task_id": "T1",
        "operation_generation": 1,
        "task_map_generation": 3,
        "workspace_generation": 1,
        "prompt": "implement the admitted task contract",
        "execution_profile": {
            "schema_version": "execution-profile.v1",
            "role": "writer_lane_1",
            "profile_id": "bounded-direct-v1",
            "profile_digest": "profile-sha",
        },
        "result_identity": {
            "task_id": "T1",
            "role_instance": "writer_lane_1",
            "attempt_id": "attempt-1",
            "lease_id": "lease-1",
            "placement_epoch": 1,
        },
    }
    first = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-task-stage",
        operation_type="task-stage",
        request={
            **common_request,
            "role_instance": "writer_lane_1",
            "active_attempt_id": "attempt-1",
            "lease_id": "lease-1",
            "placement_epoch": 1,
            "task_stage_session_binding": "binding-1",
        },
        task_id="T1",
        role_instance="writer_lane_1",
        active_attempt_id="attempt-1",
        lease_id="lease-1",
    )
    replay = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-task-stage",
        operation_type="task-stage",
        request={
            **common_request,
            "result_identity": {
                "task_id": "T1",
                "role_instance": "writer_lane_2",
                "attempt_id": "attempt-2",
                "lease_id": "lease-2",
                "placement_epoch": 2,
            },
            "execution_profile": {
                "schema_version": "execution-profile.v1",
                "role": "writer_lane_2",
                "profile_id": "bounded-direct-v1",
                "profile_digest": "profile-sha",
            },
            "role_instance": "writer_lane_2",
            "active_attempt_id": "attempt-2",
            "lease_id": "lease-2",
            "placement_epoch": 2,
            "task_stage_session_binding": "binding-2",
        },
        task_id="T1",
        role_instance="writer_lane_2",
        active_attempt_id="attempt-2",
        lease_id="lease-2",
    )

    assert replay.replay_hit is True
    assert replay.request_hash == first.request_hash
    requested = next(
        event
        for event in service.event_log.read_all()
        if event.type == "workflow.operation.requested"
    )
    request_path = tmp_path / requested.payload["request_ref"]["ref"]
    persisted = json.loads(request_path.read_text(encoding="utf-8"))
    assert persisted["role_instance"] == "writer_lane_1"
    assert persisted["active_attempt_id"] == "attempt-1"
    assert persisted["lease_id"] == "lease-1"
    assert persisted["request"]["placement_epoch"] == 1


def test_task_pipeline_legacy_hash_reopens_only_for_attempt_local_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    current_hash_body = workflow_operation_module.task_pipeline_request_hash_body

    def legacy_hash_body(request_body, request):  # noqa: ANN001
        body = dict(current_hash_body(request_body, request))
        semantic_request = dict(body["request"])
        semantic_request["source_manifest_digest"] = str(
            request.get("source_manifest_digest") or ""
        )
        semantic_request["read_policy_digest"] = str(
            request.get("read_policy_digest") or ""
        )
        body["request"] = semantic_request
        return body

    monkeypatch.setattr(
        workflow_operation_module,
        "task_pipeline_request_hash_body",
        legacy_hash_body,
    )
    first = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-task-stage-legacy-hash",
        operation_type="task-stage",
        request={
            "task_pipeline_stage": "impl",
            "operation_generation": 3,
            "prompt": "repair the admitted contract",
            "source_manifest_digest": "a" * 64,
            "read_policy_digest": "b" * 64,
        },
        task_id="TASK-A",
    )
    monkeypatch.setattr(
        workflow_operation_module,
        "task_pipeline_request_hash_body",
        current_hash_body,
    )
    service.block(
        operation_id="op-task-stage-legacy-hash",
        request_hash="f" * 64,
        workflow_run_id="run-1",
        task_id="TASK-A",
        reason="request_hash_divergence",
    )

    replay = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-task-stage-legacy-hash",
        operation_type="task-stage",
        request={
            "task_pipeline_stage": "impl",
            "operation_generation": 3,
            "prompt": "repair the admitted contract",
            "source_manifest_digest": "c" * 64,
            "read_policy_digest": "d" * 64,
        },
        task_id="TASK-A",
    )

    assert replay.status == "requested"
    assert replay.replay_hit is True
    assert replay.request_hash == first.request_hash
    view = reduce_workflow_operations(service.event_log.read_all())[
        "op-task-stage-legacy-hash"
    ]
    assert view["status"] == "requested"
    assert view["divergent"] is False
    assert view["compatibility_proof_digest"]
    compatibility_events = [
        event
        for event in service.event_log.read_all()
        if event.type == "workflow.operation.redrive_admitted"
        and event.payload.get("compatibility_proof_digest")
    ]
    assert len(compatibility_events) == 1
    compatibility_ref = compatibility_events[0].payload[
        "compatibility_request_ref"
    ]
    assert (tmp_path / compatibility_ref["ref"]).is_file()

    service.block(
        operation_id="op-task-stage-legacy-hash",
        request_hash=first.request_hash,
        workflow_run_id="run-1",
        task_id="TASK-A",
        reason="request_hash_compatibility_failed",
    )
    recovered_again = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-task-stage-legacy-hash",
        operation_type="task-stage",
        request={
            "task_pipeline_stage": "impl",
            "operation_generation": 3,
            "prompt": "repair the admitted contract",
            "source_manifest_digest": "c" * 64,
            "read_policy_digest": "d" * 64,
        },
        task_id="TASK-A",
    )
    assert recovered_again.status == "requested"
    assert sum(
        event.type == "workflow.operation.redrive_admitted"
        for event in service.event_log.read_all()
    ) == 2

    divergent = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-task-stage-legacy-hash",
        operation_type="task-stage",
        request={
            "task_pipeline_stage": "impl",
            "operation_generation": 3,
            "prompt": "a different semantic request",
            "source_manifest_digest": "e" * 64,
            "read_policy_digest": "f" * 64,
        },
        task_id="TASK-A",
    )
    assert divergent.status == "divergent"


def test_task_pipeline_replay_ignores_flow_protocol_metadata_addition(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    base_request = {
        "task_pipeline_stage": "impl",
        "task_id": "T-FLOW",
        "operation_generation": 1,
        "task_map_generation": 3,
        "workspace_generation": 1,
        "pipeline_key": "tp-T-FLOW",
        "task_stage_session_binding": "binding-T-FLOW",
        "prompt": "implement the admitted task contract",
    }
    first = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-task-flow-metadata",
        operation_type="task-stage",
        request=base_request,
        task_id="T-FLOW",
    )
    replay = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-task-flow-metadata",
        operation_type="task-stage",
        request={**base_request, "flow_kind": "prd", "request_kind": "prd"},
        task_id="T-FLOW",
    )

    assert replay.status == "requested"
    assert replay.replay_hit is True
    assert replay.request_hash == first.request_hash


def test_task_pipeline_replay_ignores_derived_semantic_submit_mode_migration(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    base_request = {
        "task_pipeline_stage": "impl",
        "task_id": "T-PROTOCOL",
        "operation_generation": 1,
        "task_map_generation": 3,
        "workspace_generation": 1,
        "pipeline_key": "tp-T-PROTOCOL",
        "prompt": "implement the admitted task contract",
        "semantic_result_submit_mode": "off",
        "result_identity": {
            "attempt_domain": "task",
            "pipeline_key": "tp-T-PROTOCOL",
        },
    }
    first = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-task-protocol-migration",
        operation_type="task-stage",
        request=base_request,
        task_id="T-PROTOCOL",
    )
    replay = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-task-protocol-migration",
        operation_type="task-stage",
        request={
            **base_request,
            "semantic_result_submit_mode": "blocking",
            "result_identity": {
                **base_request["result_identity"],
                "flow_kind": "prd",
                "workdir": "/tmp/current-task-workdir",
                "placement_epoch": 2,
            },
        },
        task_id="T-PROTOCOL",
    )

    assert replay.status == "requested"
    assert replay.replay_hit is True
    assert replay.request_hash == first.request_hash

def test_operation_settles_even_when_product_verdict_is_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path)
    operation_id = "op-rejected"
    ensured = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id=operation_id,
        operation_type="agent",
        request={"prompt": "verify"},
        task_id="T1",
    )
    envelope_ref = {
        "ref_schema_version": "sidecar-ref.v1",
        "kind": "call_result_envelope",
        "ref": "artifacts/call-results/rejected.json",
        "sha256": "a" * 64,
    }
    service.settle(
        operation_id=operation_id,
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="T1",
        admitted_call_result_ref=envelope_ref,
    )
    view = reduce_workflow_operations(service.event_log.read_all())[operation_id]
    assert view["status"] == "settled"
    assert view["admitted_call_result_ref"]["ref"].endswith("rejected.json")


def test_failed_operation_can_settle_after_durable_result_repair(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    ensured = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-repaired",
        operation_type="agent",
        request={"prompt": "verify"},
    )
    service.fail(
        operation_id="op-repaired",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        reason="result publication interrupted",
    )
    service.settle(
        operation_id="op-repaired",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        admitted_call_result_ref={
            "ref": "artifacts/call-results/repaired.json",
            "sha256": "b" * 64,
        },
    )

    view = reduce_workflow_operations(service.event_log.read_all())[
        "op-repaired"
    ]
    assert view["status"] == "settled"


def test_cancelled_operation_ignores_late_settlement(tmp_path: Path) -> None:
    service = _service(tmp_path)
    ensured = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-cancelled",
        operation_type="agent",
        request={"prompt": "verify"},
    )
    service.cancel(
        operation_id="op-cancelled",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        reason="operator cancelled",
    )
    service.settle(
        operation_id="op-cancelled",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        admitted_call_result_ref={
            "ref": "artifacts/call-results/late.json",
            "sha256": "c" * 64,
        },
    )

    view = reduce_workflow_operations(service.event_log.read_all())[
        "op-cancelled"
    ]
    assert view["status"] == "cancelled"
    assert view["last_event_type"] == "workflow.operation.cancelled"


def test_interrupted_operation_is_suspended_until_recovery(tmp_path: Path) -> None:
    service = _service(tmp_path)
    ensured = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-interrupted",
        operation_type="agent",
        request={"prompt": "plan"},
        task_id="FLOW-1",
        parent_task_id="FLOW-1",
    )
    service.mark_started(
        operation_id="op-interrupted",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="FLOW-1",
        dispatch_id="dispatch-1",
    )
    service.interrupt(
        operation_id="op-interrupted",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="FLOW-1",
        reason="graceful_stop",
    )

    view = reduce_workflow_operations(service.event_log.read_all())[
        "op-interrupted"
    ]
    assert view["status"] == "suspended"
    assert view["task_id"] == "FLOW-1"
    assert view["parent_task_id"] == "FLOW-1"


def test_task_pipeline_redrive_preserves_operation_and_is_idempotent(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    ensured = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-task-a-impl-g1",
        operation_type="task-stage",
        request={
            "task_pipeline_stage": "impl",
            "operation_generation": 1,
            "prompt": "implement",
        },
        task_id="TASK-A",
    )
    service.mark_started(
        operation_id="op-task-a-impl-g1",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="TASK-A",
        dispatch_id="dispatch-1",
        role_instance="impl-1",
        active_attempt_id="attempt-1",
        lease_id="lease-1",
    )
    service.interrupt(
        operation_id="op-task-a-impl-g1",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="TASK-A",
        reason="lease_expired",
        source_attempt_id="attempt-1",
    )

    first = service.admit_redrive(
        operation_id="op-task-a-impl-g1",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="TASK-A",
        source_attempt_id="attempt-1",
        recovery_decision_event_id="run-manager-decision-1",
        reason="worker respawn completed",
    )
    replay = service.admit_redrive(
        operation_id="op-task-a-impl-g1",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="TASK-A",
        source_attempt_id="attempt-1",
        recovery_decision_event_id="run-manager-decision-1",
        reason="worker respawn completed",
    )

    assert first is not None
    assert replay is None
    view = reduce_workflow_operations(service.event_log.read_all())[
        "op-task-a-impl-g1"
    ]
    assert view["status"] == "requested"
    assert view["operation_id"] == "op-task-a-impl-g1"
    assert view["operation_generation"] == 1
    assert view["redrive_count"] == 1
    assert view["redrive_source_attempt_ids"] == ["attempt-1"]
    assert view["role_instance"] == ""
    assert view["active_attempt_id"] == ""

    service.mark_started(
        operation_id="op-task-a-impl-g1",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="TASK-A",
        dispatch_id="dispatch-2",
        role_instance="impl-2",
        active_attempt_id="attempt-2",
        lease_id="lease-2",
    )
    events = service.event_log.read_all()
    restarted = reduce_workflow_operations(events)["op-task-a-impl-g1"]
    assert restarted["status"] == "running"
    assert restarted["active_attempt_id"] == "attempt-2"
    assert restarted["dispatch_id"] == "dispatch-2"
    assert sum(
        event.type == "workflow.operation.started"
        for event in events
    ) == 2


def test_transient_transport_retry_reopens_same_operation_once(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    ensured = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-transport-retry",
        operation_type="orchestrator_agent_semantic",
        request={"prompt": "review plan"},
        role_instance="orchestrator",
    )
    service.mark_started(
        operation_id="op-transport-retry",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        dispatch_id="checkpoint-1",
        role_instance="orchestrator",
    )
    service.interrupt(
        operation_id="op-transport-retry",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        reason="transient_transport:pane_dead:TmuxError",
    )

    service.mark_retry_started(
        operation_id="op-transport-retry",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        retry_attempt=1,
        reason="retry_after_orchestrator_pane_respawn",
        dispatch_id="checkpoint-retry-1",
        role_instance="orchestrator",
    )

    view = reduce_workflow_operations(service.event_log.read_all())[
        "op-transport-retry"
    ]
    assert view["status"] == "running"
    assert view["retry_count"] == 1
    assert view["retry_attempt"] == 1
    assert view["dispatch_id"] == "checkpoint-retry-1"


def test_continuation_reservation_is_replay_safe_and_supersedable(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    ensured = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="dynamic-op-1",
        operation_type="dynamic_read_only_workflow",
        request={
            "attempt_domain": "read_only_dynamic",
            "continuation_key": "fragment-1",
        },
        parent_operation_id="parent-op",
        task_id="T1",
    )

    first = service.reserve_continuation(
        operation_id="dynamic-op-1",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        continuation_key="fragment-1",
        expected_generation="GEN-1",
        expected_package_ref="artifacts/packages/p1.json",
        expected_package_digest="a" * 64,
        pending_action_digest="b" * 64,
        budget_snapshot={"available": True},
        reservation_expires_at="2026-07-24T12:00:30+00:00",
        parent_operation_id="parent-op",
        task_id="T1",
    )
    replay = service.reserve_continuation(
        operation_id="dynamic-op-1",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        continuation_key="fragment-1",
        expected_generation="GEN-1",
        expected_package_ref="artifacts/packages/p1.json",
        expected_package_digest="a" * 64,
        pending_action_digest="b" * 64,
        budget_snapshot={"available": True},
        reservation_expires_at="2026-07-24T12:01:00+00:00",
        parent_operation_id="parent-op",
        task_id="T1",
    )

    assert first.created is True
    assert replay.replay_hit is True
    assert replay.reservation_id == first.reservation_id
    assert replay.idempotency_key == stable_continuation_idempotency_key(
        workflow_run_id="run-1",
        continuation_key="fragment-1",
        expected_generation="GEN-1",
    )
    service.supersede(
        operation_id="dynamic-op-1",
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        reason="package_generation_changed",
        reservation_id=first.reservation_id,
        task_id="T1",
    )
    view = reduce_workflow_operations(service.event_log.read_all())["dynamic-op-1"]
    assert view["status"] == "superseded"
    assert view["reservation_id"] == first.reservation_id
    assert view["reason"] == "package_generation_changed"
    assert sum(
        event.type == "workflow.operation.reserved"
        for event in service.event_log.read_all()
    ) == 1
