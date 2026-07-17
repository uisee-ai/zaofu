"""Runtime wiring for durable provider-call results.

This module keeps the orchestrator integration small.  It prepares one stable
operation before dispatch, records attempt-local input manifests, and admits a
terminal provider result without changing semantic lane routing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.artifact_read_ledger import (
    build_input_consumption_policy,
    source_manifest_from_payload,
    write_input_consumption_policy,
)
from zf.runtime.call_result_admission import (
    CallResultAdmissionOutcome,
    CallResultAdmissionService,
    dispatch_call_result_correction,
    result_protocol_mode,
)
from zf.runtime.call_result_envelope import hydrate_call_result_envelope
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import (
    WorkflowOperationError,
    WorkflowOperationService,
    stable_operation_id,
)


@dataclass(frozen=True)
class PreparedCallOperation:
    mode: str
    workflow_run_id: str
    operation_id: str
    request_hash: str
    attempt_id: str
    should_dispatch: bool
    ensure_status: str
    replay_hit: bool = False
    admitted_call_result_ref: str = ""
    admitted_call_result_digest: str = ""


def prepare_call_operation(
    runtime: Any,
    *,
    payload: dict[str, Any],
    operation_type: str,
    operation_key: str,
    stage_id: str,
    task_id: str,
    dispatch_id: str,
    causation_id: str = "",
    correlation_id: str = "",
) -> PreparedCallOperation:
    """Pin call identity and immutable inputs before provider dispatch."""

    mode = result_protocol_mode(runtime.config, payload)
    workflow_run_id = str(
        payload.get("workflow_run_id")
        or payload.get("trace_id")
        or correlation_id
        or payload.get("pdd_id")
        or f"legacy-{task_id or stage_id or 'run'}"
    )
    # ZF-REVIEW-140-B3(2026-07-16 实弹):verify child 的 payload 派生自
    # 上游 impl manifest child,曾继承 impl 的 operation_id/attempt_id;
    # retrigger fanout 复用 task/child payload 同病。继承身份 + 本段新
    # request → request_hash_divergence → 预注册 fail-closed → candidate
    # rework 环(有界于 cap=2 但流程死)。修复:调用身份一律本段派生,
    # 不信 payload 携带值;dispatch_id(本次派发)优先于继承 attempt_id;
    # rework_of 把返工重派限定为新 operation(140 裁决 10:rework 不是
    # replay)。同 dispatch 重放输入相同 → 派生结果相同,replay 语义不变。
    attempt_id = str(dispatch_id or payload.get("attempt_id") or payload.get("run_id") or "")
    trigger_payload = (
        payload.get("trigger_payload")
        if isinstance(payload.get("trigger_payload"), dict) else {}
    )
    rework_marker = str(
        payload.get("rework_of") or trigger_payload.get("rework_of") or ""
    ).strip()
    scoped_operation_key = (
        f"{operation_key}@rework:{rework_marker}" if rework_marker else operation_key
    )
    operation_id = stable_operation_id(
        workflow_run_id=workflow_run_id,
        parent_stage_id=stage_id,
        operation_key=scoped_operation_key,
        operation_type=operation_type,
    )
    payload.update({
        "workflow_run_id": workflow_run_id,
        "operation_id": operation_id,
        "attempt_id": attempt_id,
        "result_protocol_mode": mode,
    })

    source_manifest, source_descriptor = source_manifest_from_payload(
        state_dir=runtime.state_dir,
        project_root=runtime.project_root,
        payload=payload,
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        attempt_id=attempt_id,
        dispatch_id=dispatch_id,
        source_event_id=causation_id,
    )
    payload.update({
        "attempt_source_manifest_ref": str(source_descriptor.get("ref") or ""),
        "attempt_source_manifest_digest": str(source_descriptor.get("sha256") or ""),
        "attempt_source_manifest": source_descriptor,
    })
    required_reads = payload.get("required_reads")
    if isinstance(required_reads, list) and required_reads:
        policy = build_input_consumption_policy(
            workflow_run_id=workflow_run_id,
            attempt_id=attempt_id,
            required_reads=required_reads,
        )
        policy_descriptor = write_input_consumption_policy(
            runtime.state_dir,
            policy,
            source_event_id=causation_id,
        )
        payload.update({
            "input_consumption_policy": policy,
            "input_consumption_policy_ref": policy_descriptor,
            "input_consumption_policy_digest": str(policy_descriptor.get("sha256") or ""),
        })

    request = {
        "workflow_run_id": workflow_run_id,
        "operation_type": operation_type,
        "stage_id": stage_id,
        "operation_key": operation_key,
        "task_id": task_id,
        "fanout_id": str(payload.get("fanout_id") or ""),
        "child_id": str(payload.get("child_id") or ""),
        "target_ref": str(payload.get("target_ref") or ""),
        "target_commit": str(payload.get("target_commit") or ""),
        "contract_snapshot_digest": str(payload.get("contract_snapshot_digest") or ""),
        "target_snapshot_digest": str(payload.get("target_snapshot_digest") or ""),
        "source_manifest_digest": str(source_descriptor.get("sha256") or ""),
        "read_policy_digest": str(payload.get("input_consumption_policy_digest") or ""),
        "required_reads": list(required_reads) if isinstance(required_reads, list) else [],
        "skills": list(payload.get("skills") or []),
    }
    service = workflow_operation_service(runtime)
    ensured = service.ensure_operation(
        workflow_run_id=workflow_run_id,
        operation_id=operation_id,
        operation_type=operation_type,
        request=request,
        parent_operation_id=str(payload.get("parent_operation_id") or ""),
        parent_stage_id=stage_id,
        parent_attempt_id=str(payload.get("parent_attempt_id") or ""),
        task_id=task_id,
        child_task_ids=[task_id] if task_id else [],
        causation_id=causation_id,
        correlation_id=correlation_id or workflow_run_id,
    )
    if ensured.status == "divergent":
        raise WorkflowOperationError(
            f"workflow operation {operation_id} request diverged"
        )
    payload["request_hash"] = ensured.request_hash
    payload["operation_request_status"] = ensured.status
    if ensured.admitted_call_result_ref:
        payload["admitted_call_result_ref"] = ensured.admitted_call_result_ref
        payload["admitted_call_result_digest"] = ensured.admitted_call_result_digest
    # A settled operation is immutable and must never be dispatched again.
    # A running replay is left to provider-session resume rather than a second
    # prompt. A requested operation may have crashed before send and is safe to
    # dispatch once more with the same request hash.
    should_dispatch = ensured.status == "requested"
    return PreparedCallOperation(
        mode=mode,
        workflow_run_id=workflow_run_id,
        operation_id=operation_id,
        request_hash=ensured.request_hash,
        attempt_id=attempt_id,
        should_dispatch=should_dispatch,
        ensure_status=ensured.status,
        replay_hit=ensured.replay_hit,
        admitted_call_result_ref=ensured.admitted_call_result_ref,
        admitted_call_result_digest=ensured.admitted_call_result_digest,
    )


def mark_call_operation_started(
    runtime: Any,
    prepared: PreparedCallOperation,
    *,
    task_id: str,
    dispatch_id: str,
    causation_id: str = "",
    correlation_id: str = "",
) -> None:
    workflow_operation_service(runtime).mark_started(
        operation_id=prepared.operation_id,
        request_hash=prepared.request_hash,
        workflow_run_id=prepared.workflow_run_id,
        task_id=task_id,
        dispatch_id=dispatch_id,
        causation_id=causation_id,
        correlation_id=correlation_id or prepared.workflow_run_id,
    )


def admit_runtime_call_result(
    runtime: Any,
    event: ZfEvent,
    *,
    merged_payload: Mapping[str, Any] | None = None,
    mode: str = "",
    dispatch_correction: bool = True,
) -> CallResultAdmissionOutcome:
    payload = {
        **(event.payload if isinstance(event.payload, dict) else {}),
        **dict(merged_payload or {}),
    }
    source = replace(event, payload=payload)
    effective_mode = mode or result_protocol_mode(runtime.config, payload)
    outcome = call_result_admission_service(runtime).report_legacy_result(
        source,
        mode=effective_mode,
        operation={
            "workflow_run_id": str(payload.get("workflow_run_id") or ""),
            "parent_operation_id": str(payload.get("parent_operation_id") or ""),
            "operation_id": str(payload.get("operation_id") or ""),
            "request_hash": str(payload.get("request_hash") or ""),
        },
    )
    if (
        dispatch_correction
        and outcome.repair_requested
        and outcome.correction_dispatch_required
    ):
        dispatch_call_result_correction(
            runtime,
            source_event=source,
            outcome=outcome,
        )
    return outcome


def hydrate_admitted_control_result(
    state_dir: Path,
    envelope_descriptor: Mapping[str, Any],
) -> dict[str, Any]:
    envelope = hydrate_call_result_envelope(state_dir, envelope_descriptor)
    control = envelope.get("control_result")
    if not isinstance(control, Mapping):
        # Early shadow artifacts used the verbose key. Keep them readable,
        # while call-result-envelope.v1 continues to write ``control_result``.
        control = envelope.get("control_result_ref")
    if not isinstance(control, Mapping):
        raise WorkflowOperationError("admitted envelope has no control-result ref")
    hydrated = hydrate_sidecar_ref(state_dir, dict(control))
    if not isinstance(hydrated.payload, dict):
        raise WorkflowOperationError("control-result sidecar must contain a JSON object")
    return dict(hydrated.payload)


def workflow_operation_service(runtime: Any) -> WorkflowOperationService:
    service = getattr(runtime, "_workflow_operation_service_v1", None)
    if service is None:
        service = WorkflowOperationService(
            state_dir=runtime.state_dir,
            event_log=runtime.event_log,
            event_writer=runtime.event_writer,
        )
        runtime._workflow_operation_service_v1 = service
    return service


def call_result_admission_service(runtime: Any) -> CallResultAdmissionService:
    service = getattr(runtime, "_call_result_admission_service_v1", None)
    if service is None:
        service = CallResultAdmissionService(
            state_dir=runtime.state_dir,
            event_log=runtime.event_log,
            event_writer=runtime.event_writer,
            operation_service=workflow_operation_service(runtime),
        )
        runtime._call_result_admission_service_v1 = service
    return service


__all__ = [
    "PreparedCallOperation",
    "admit_runtime_call_result",
    "call_result_admission_service",
    "hydrate_admitted_control_result",
    "mark_call_operation_started",
    "prepare_call_operation",
    "workflow_operation_service",
]
