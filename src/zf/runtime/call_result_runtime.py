"""Runtime wiring for durable provider-call results.

This module keeps the orchestrator integration small.  It prepares one stable
operation before dispatch, records attempt-local input manifests, and admits a
terminal provider result without changing semantic lane routing.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.artifact_read_ledger import (
    build_input_consumption_policy,
    canonical_required_reads,
    write_input_consumption_policy,
)
from zf.runtime.artifact_query.handoff import (
    CanonicalHandoffResolver,
    build_handoff_authority_contract,
)
from zf.runtime.call_result_admission import (
    CallResultAdmissionOutcome,
    CallResultAdmissionService,
    dispatch_call_result_correction,
    result_protocol_mode,
)
from zf.runtime.call_result_envelope import hydrate_call_result_envelope
from zf.runtime.candidate_verification_authority import prepare_candidate_verification_authority
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import (
    WorkflowOperationError,
    WorkflowOperationService,
    load_workflow_operation,
    stable_operation_id,
)


@dataclass(frozen=True)
class PreparedCallOperation:
    mode: str
    workflow_run_id: str
    operation_id: str
    request_hash: str
    attempt_id: str
    lease_id: str
    role_instance: str
    task_id: str
    parent_task_id: str
    output_profile_id: str
    output_profile_revision: str
    result_scratch_ref: str
    should_dispatch: bool
    ensure_status: str
    replay_hit: bool = False
    admitted_call_result_ref: str = ""
    admitted_call_result_digest: str = ""
    provider_session_id: str = ""
    context_delivery_envelope: dict[str, Any] = field(default_factory=dict)
    context_delivery_envelope_ref: dict[str, Any] = field(default_factory=dict)


def prepare_call_operation(
    runtime: Any,
    *,
    payload: dict[str, Any],
    operation_type: str,
    operation_key: str,
    stage_id: str,
    task_id: str,
    dispatch_id: str,
    attempt_id_override: str = "",
    causation_id: str = "",
    correlation_id: str = "",
    workdir_write_scopes: list[str] | None = None,
    scheduler_attempt_id: str = "",
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
    from zf.runtime.workflow_lineage import bind_workflow_task_lineage

    task_id, parent_task_id = bind_workflow_task_lineage(
        runtime.event_log.read_all(),
        workflow_run_id=workflow_run_id,
        payload=payload,
        task_id=task_id,
    )
    # ZF-REVIEW-140-B3(2026-07-16 实弹):verify child 的 payload 派生自
    # 上游 impl manifest child,曾继承 impl 的 operation_id/attempt_id;
    # retrigger fanout 复用 task/child payload 同病。继承身份 + 本段新
    # request → request_hash_divergence → 预注册 fail-closed → candidate
    # rework 环(有界于 cap=2 但流程死)。修复:调用身份一律本段派生,
    # 不信 payload 携带值;dispatch_id(本次派发)优先于继承 attempt_id;
    # rework_of 把返工重派限定为新 operation(140 裁决 10:rework 不是
    # replay)。同 dispatch 重放输入相同 → 派生结果相同,replay 语义不变。
    attempt_id = str(
        attempt_id_override
        or scheduler_attempt_id
        or dispatch_id
        or payload.get("attempt_id")
        or payload.get("run_id")
        or ""
    )
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
    context_delivery_enabled, context_inheritance = (
        _context_protocol_for_operation(
            runtime,
            operation_id=operation_id,
            requested=payload.get("context_inheritance"),
        )
    )
    payload.update({
        "workflow_run_id": workflow_run_id,
        "operation_id": operation_id,
        "attempt_id": attempt_id,
        "result_protocol_mode": mode,
        "_context_delivery_enabled": context_delivery_enabled,
    })
    feedback_revision = str(
        payload.get("feedback_revision")
        or payload.get("rework_feedback_digest")
        or ""
    ).strip()
    if feedback_revision:
        payload["feedback_revision"] = feedback_revision
    if context_delivery_enabled:
        payload["context_inheritance"] = context_inheritance
    from zf.runtime.attempt_domain import infer_attempt_domain

    payload["attempt_domain"] = infer_attempt_domain(
        payload,
        operation_type=operation_type,
        stage_id=stage_id,
    )
    _pin_plan_candidate_validation_context(
        runtime,
        payload=payload,
        operation_type=operation_type,
        stage_id=stage_id,
    )
    from zf.runtime.call_result_adapters import call_result_profile_identity
    from zf.runtime.scoped_candidate_verify import uses_scoped_candidate_verify

    scoped_candidate_verify = uses_scoped_candidate_verify(
        runtime.config, payload=payload,
        operation_type=operation_type, stage_id=stage_id,
    )
    if scoped_candidate_verify:
        payload["output_profile_id"] = "task-verify"
        payload["verification_owner"] = "task_verify"

    output_profile_id, output_profile_revision = call_result_profile_identity(
        operation_type=operation_type,
        stage_id=stage_id,
        payload=payload,
    )
    role_instance = str(payload.get("role_instance") or "")
    task = (
        runtime.task_store.get(task_id)
        if task_id and getattr(runtime, "task_store", None) is not None
        else None
    )
    contract = (
        payload.get("task_contract")
        if isinstance(payload.get("task_contract"), Mapping)
        else getattr(task, "contract", None)
    )
    from zf.runtime.execution_profiles import resolve_execution_profile

    resolved_execution_profile = resolve_execution_profile(
        runtime.config,
        role_instance=role_instance or str(getattr(task, "assigned_to", "") or ""),
        contract=contract,
    )
    execution_profile_projection = resolved_execution_profile.projection()
    result_scratch_ref = (
        (
            Path("tmp") / "result-submit" / operation_id / "result.json"
        )
        if str(payload.get("task_pipeline_stage") or "").strip()
        else (
            Path("tmp")
            / "result-submit"
            / operation_id
            / (attempt_id or "attempt")
            / "result.json"
        )
    ).as_posix()
    payload.update({
        "output_profile_id": output_profile_id,
        "output_profile_revision": output_profile_revision,
        "execution_profile_id": resolved_execution_profile.profile_id,
        "execution_profile_digest": resolved_execution_profile.profile_digest,
        "execution_profile_shadow": dict(
            execution_profile_projection["shadow"]
        ),
        "result_scratch_ref": result_scratch_ref,
    })
    if output_profile_id == "candidate-verify":
        prepare_candidate_verification_authority(
            runtime, payload=payload, workflow_run_id=workflow_run_id,
            task_id=task_id, source_event_id=causation_id,
        )
    payload["handoff_authority_contract"] = build_handoff_authority_contract(
        payload,
        output_profile_id=output_profile_id,
        stage_id=stage_id,
        operation_type=operation_type,
    )
    if scoped_candidate_verify:
        payload["handoff_authority_contract"]["requires_candidate_lineage"] = True
    semantic_submit_mode = _semantic_submit_mode(
        runtime.config,
        profile_id=output_profile_id,
        role_instance=role_instance,
        payload=payload,
    )
    payload["semantic_result_submit_mode"] = semantic_submit_mode

    if output_profile_id == "artifact-delivery":
        from zf.runtime.generic_workflow_outputs import (
            resolve_declared_output_artifacts,
        )

        output_artifacts = resolve_declared_output_artifacts(
            runtime.state_dir,
            input_result_refs=[
                str(item) for item in payload.get("input_result_refs") or []
                if str(item).strip()
            ],
            required_artifacts=[
                dict(item)
                for item in payload.get("required_delivery_artifacts") or []
                if isinstance(item, Mapping)
            ],
        )
        if output_artifacts:
            payload["artifact_refs"] = [
                *list(payload.get("artifact_refs") or []),
                *output_artifacts,
            ]
        controlled_inputs = list(payload.get("input_refs") or [])
        for index, ref in enumerate(payload.get("input_result_refs") or []):
            normalized_ref = str(ref or "").strip()
            if normalized_ref:
                controlled_inputs.append({
                    "source_id": f"admitted-input-result-{index + 1}",
                    "kind": "call_result_envelope",
                    "ref": normalized_ref,
                    "sha256": Path(normalized_ref).stem,
                })
        claim_ref = str(payload.get("goal_claim_set_ref") or "").strip()
        claim_digest = str(
            payload.get("goal_claim_set_digest") or ""
        ).strip()
        if claim_ref and claim_digest:
            controlled_inputs.append({
                "source_id": "goal-claim-set",
                "kind": "goal_claim_set",
                "ref": claim_ref,
                "sha256": claim_digest,
            })
        run_contract_ref = str(payload.get("run_contract_ref") or "").strip()
        if run_contract_ref:
            controlled_inputs.append({
                "source_id": "run-contract",
                "kind": "run_contract_snapshot",
                "ref": run_contract_ref,
            })
        if controlled_inputs:
            payload["input_refs"] = controlled_inputs

    from zf.runtime.critical_context import critical_context_sources

    critical_sources = critical_context_sources(
        runtime.state_dir, runtime.event_log.read_all(), workflow_run_id,
    )
    if critical_sources:
        critical_ids = {source["source_id"] for source in critical_sources}
        payload["input_refs"] = [
            *(item for item in (payload.get("input_refs") or [])
              if not isinstance(item, dict) or item.get("source_id") not in critical_ids),
            *critical_sources,
        ]

    source_manifest, source_descriptor = CanonicalHandoffResolver(
        state_dir=runtime.state_dir,
        project_root=runtime.project_root,
        config=runtime.config,
    ).resolve_payload(
        payload=payload,
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        attempt_id=attempt_id,
        dispatch_id=dispatch_id,
        source_event_id=causation_id,
    )
    payload.pop("_context_delivery_enabled", None)
    payload.update({
        "attempt_source_manifest_ref": str(source_descriptor.get("ref") or ""),
        "attempt_source_manifest_digest": str(source_descriptor.get("sha256") or ""),
        "attempt_source_manifest": source_descriptor,
    })
    explicit_required_reads = payload.get("required_reads")
    if payload.get("workflow_template") == "bounded-agent-v1":
        # The bounded executor must consume its admitted request, just as its
        # verifier consumes the admitted report. Pin this before registration.
        explicit_required_reads = [
            *(explicit_required_reads if isinstance(explicit_required_reads, list) else []),
            *({"source_id": source["source_id"], "artifact_id": source["artifact_id"],
               "artifact_sha256": source["sha256"], "json_path": path,
               "min_returned_bytes": 1, "allow_truncated": False}
              for source in source_manifest.get("sources", [])
              for path in source.get("allowed_paths", ["$"])),
        ]
    required_reads = canonical_required_reads(
        source_manifest,
        output_profile_id=output_profile_id,
        explicit=(
            explicit_required_reads
            if isinstance(explicit_required_reads, list)
            else ()
        ),
    )
    if required_reads:
        payload["required_reads"] = required_reads
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

    context_envelope: dict[str, Any] = {}
    context_envelope_descriptor: dict[str, Any] = {}
    provider_session_id = ""

    request = {
        "workflow_run_id": workflow_run_id,
        "operation_type": operation_type,
        "stage_id": stage_id,
        "operation_key": operation_key,
        "attempt_domain": str(payload.get("attempt_domain") or ""),
        "handoff_authority_contract": dict(
            payload["handoff_authority_contract"]
        ),
        "task_id": task_id,
        "parent_task_id": parent_task_id,
        "fanout_id": str(payload.get("fanout_id") or ""),
        "child_id": str(payload.get("child_id") or ""),
        "target_ref": str(payload.get("target_ref") or ""),
        "target_commit": str(payload.get("target_commit") or ""),
        "contract_snapshot_digest": str(payload.get("contract_snapshot_digest") or ""),
        "target_snapshot_digest": str(payload.get("target_snapshot_digest") or ""),
        "effective_config_ref": (
            dict(payload["effective_config_ref"])
            if isinstance(payload.get("effective_config_ref"), Mapping)
            else {}
        ),
        "effective_config_digest": str(
            payload.get("effective_config_digest") or ""
        ),
        "source_manifest_digest": str(source_descriptor.get("sha256") or ""),
        "read_policy_digest": str(payload.get("input_consumption_policy_digest") or ""),
        "input_consumption_policy_ref": (
            dict(payload["input_consumption_policy_ref"])
            if isinstance(payload.get("input_consumption_policy_ref"), Mapping)
            else {}
        ),
        "required_reads": list(required_reads) if isinstance(required_reads, list) else [],
        "skills": list(payload.get("skills") or []),
        "role_instance": role_instance,
        "active_attempt_id": attempt_id,
        "lease_id": str(payload.get("lease_id") or dispatch_id or attempt_id),
        "output_profile_id": output_profile_id,
        "output_profile_revision": output_profile_revision,
        "semantic_result_submit_mode": semantic_submit_mode,
        "execution_profile": execution_profile_projection,
        "operation_limits": (
            dict(payload["operation_limits"])
            if isinstance(payload.get("operation_limits"), Mapping)
            else {}
        ),
        "canonical_success_event": str(payload.get("canonical_success_event") or ""),
        "canonical_failure_event": str(payload.get("canonical_failure_event") or ""),
        "result_scratch_ref": result_scratch_ref,
        "task_pipeline_stage": str(payload.get("task_pipeline_stage") or ""),
        "operation_generation": int(payload.get("operation_generation") or 0),
        "task_map_generation": str(payload.get("task_map_generation") or ""),
        "workspace_generation": int(payload.get("workspace_generation") or 0),
        "placement_epoch": int(payload.get("placement_epoch") or 0),
        "pipeline_key": str(payload.get("pipeline_key") or ""),
        "task_stage_session_binding": str(
            payload.get("task_stage_session_binding") or ""
        ),
        "result_identity": {
            key: payload.get(key)
            for key in (
                "workflow_run_id",
                "task_id",
                "parent_task_id",
                "fanout_id",
                "stage_id",
                "child_id",
                "run_id",
                "role_instance",
                "attempt_id",
                "attempt_domain",
                "plan_revision",
                "plan_synth_contract_ref",
                "plan_synth_contract_digest",
                "reviewed_plan_candidate_digest",
                "pdd_id",
                "feature_id",
                "task_map_ref",
                "source_index_ref",
                "scope",
                "source_branch",
                "workdir",
                "base_git_head",
                "contract_authority_revision",
                "execution_owner",
                "workflow_request_id",
                "workflow_request_revision",
                "origin_binding_digest",
                "contract_revision",
                "task_map_generation",
                "feedback_revision",
                "workflow_generation",
                "request_revision",
                "generic_workflow_contract_digest",
                "workflow_intent",
                "workflow_template",
                "completion_profile",
                "required_delivery_artifacts",
                "input_result_refs",
                "generic_workflow_operation",
                "result_semantics",
                "workflow_dependencies",
                "workflow_input_ports",
                "workflow_output_ports",
                "workflow_dependency_barrier_id",
                "workflow_dependency_barrier_digest",
                "plan_artifact_package_id",
                "plan_artifact_package_ref",
                "plan_artifact_package_digest",
                "run_contract_ref",
                "run_contract_digest",
                "effective_config_ref",
                "effective_config_digest",
                "base_commit",
                "task_ref",
                "contract_snapshot_ref",
                "contract_snapshot_digest",
                "target_snapshot_ref",
                "target_commit",
                "target_snapshot_digest",
                "impl_self_check_ref",
                "impl_self_check_digest",
                "goal_id",
                "flow_kind",
                "objective_ref",
                "goal_claim_set_ref",
                "goal_claim_set_digest",
                "product_acceptance_required",
                "product_acceptance_spec_ref",
                "product_acceptance_spec_digest",
                "product_acceptance_report_ref",
                "product_acceptance_report_digest",
                "product_acceptance_verdict",
                "provider_qualification_required",
                "provider_qualification_status",
                "execution_profile_id",
                "execution_profile_digest",
                "planning_result_ref",
                "candidate_ref",
                "closure_fact_ref",
                "closure_fact_digest",
                "task_pipeline_stage",
                "operation_generation",
                "workspace_generation",
                "placement_epoch",
                "pipeline_key",
                "task_stage_session_binding",
                "role_binding_resolution",
                "risk_class",
                "integration_admission_profile",
                "exact_task_target_commit",
                "verification_result_ref",
                "verification_result_digest",
                "risk_review_timeout_seconds",
                "risk_review_max_turns",
                "risk_review_budget_usd",
            )
            if payload.get(key) not in (None, "")
            and not (
                stage_id == "flow-plan"
                and key in {
                    "plan_revision",
                    "task_map_generation",
                    "plan_artifact_package_id",
                    "plan_artifact_package_ref",
                    "plan_artifact_package_digest",
                }
            )
        },
    }
    pinned_write_scopes = list(dict.fromkeys(
        str(scope).strip()
        for scope in (workdir_write_scopes or [])
        if str(scope).strip()
    ))
    if pinned_write_scopes:
        request["workdir_write_scopes"] = pinned_write_scopes
    plan_candidate_validation = payload.get("plan_candidate_validation")
    if isinstance(plan_candidate_validation, Mapping):
        request["plan_candidate_validation"] = dict(
            plan_candidate_validation
        )
    if context_delivery_enabled:
        from zf.runtime.context_delivery import CONTEXT_RENDERER_VERSION

        request.update({
            "context_inheritance": dict(context_inheritance),
            "context_renderer_version": CONTEXT_RENDERER_VERSION,
        })
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
        parent_task_id=parent_task_id,
        role_instance=role_instance,
        active_attempt_id=attempt_id,
        lease_id=str(payload.get("lease_id") or dispatch_id or attempt_id),
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
    if role_instance:
        from zf.runtime.artifact_read_capability import (
            bind_attempt_artifact_read_capability,
        )
        from zf.runtime.result_submit import bind_operation_submit_capability

        bind_attempt_artifact_read_capability(
            runtime.state_dir,
            operation_id=operation_id,
            attempt_id=attempt_id,
            role_instance=role_instance,
            manifest=source_manifest,
        )
        bind_operation_submit_capability(
            runtime.state_dir,
            operation_id=operation_id,
            role_instance=role_instance,
            attempt_id=attempt_id,
            lease_id=str(payload.get("lease_id") or dispatch_id or attempt_id),
        )
    # A settled operation is immutable and must never be dispatched again.
    # A running replay is left to provider-session resume rather than a second
    # prompt. A requested operation may have crashed before send and is safe to
    # dispatch once more with the same request hash.
    should_dispatch = ensured.status == "requested"
    if context_delivery_enabled and should_dispatch:
        from zf.runtime.context_delivery import prepare_runtime_context_delivery

        (
            provider_session_id,
            context_envelope,
            context_envelope_descriptor,
        ) = prepare_runtime_context_delivery(
            runtime,
            payload=payload,
            source_manifest=source_manifest,
            source_descriptor=source_descriptor,
            workflow_run_id=workflow_run_id,
            operation_id=operation_id,
            attempt_id=attempt_id,
            dispatch_id=dispatch_id,
            role_instance=role_instance,
            causation_id=causation_id,
        )
    return PreparedCallOperation(
        mode=mode,
        workflow_run_id=workflow_run_id,
        operation_id=operation_id,
        request_hash=ensured.request_hash,
        attempt_id=attempt_id,
        lease_id=str(payload.get("lease_id") or dispatch_id or attempt_id),
        role_instance=role_instance,
        task_id=task_id,
        parent_task_id=parent_task_id,
        output_profile_id=output_profile_id,
        output_profile_revision=output_profile_revision,
        result_scratch_ref=result_scratch_ref,
        should_dispatch=should_dispatch,
        ensure_status=ensured.status,
        replay_hit=ensured.replay_hit,
        admitted_call_result_ref=ensured.admitted_call_result_ref,
        admitted_call_result_digest=ensured.admitted_call_result_digest,
        provider_session_id=provider_session_id,
        context_delivery_envelope=context_envelope,
        context_delivery_envelope_ref=context_envelope_descriptor,
    )


def _pin_plan_candidate_validation_context(
    runtime: Any,
    *,
    payload: dict[str, Any],
    operation_type: str,
    stage_id: str,
) -> None:
    if (
        operation_type != "fanout_reader_child"
        or isinstance(payload.get("plan_candidate_validation"), Mapping)
    ):
        return
    stage = next((
        item for item in getattr(runtime.config.workflow, "stages", []) or []
        if str(getattr(item, "id", "") or "") == stage_id
    ), None)
    if str(getattr(stage, "attempt_domain", "") or "") != "plan":
        return
    manifest_loader = getattr(runtime, "_fanout_manifest", None)
    manifest = (
        manifest_loader(str(payload.get("fanout_id") or "")) or {}
        if callable(manifest_loader)
        else {}
    )
    trigger_payload = (
        manifest.get("trigger_payload")
        if isinstance(manifest.get("trigger_payload"), dict)
        else payload.get("trigger_payload")
        if isinstance(payload.get("trigger_payload"), dict)
        else {}
    )
    from zf.core.workflow.flow_metadata import flow_metadata_for
    from zf.runtime.plan_candidate_preflight import plan_candidate_writer_policy

    payload["plan_candidate_validation"] = {
        "schema_version": "plan-candidate-validation-context.v1",
        "manifest": {
            "fanout_id": str(payload.get("fanout_id") or ""),
            "stage_id": stage_id,
            "trigger_payload": dict(trigger_payload),
        },
        "metadata": flow_metadata_for(
            runtime.config,
            payload=trigger_payload,
        ),
        "writer_policy": plan_candidate_writer_policy(runtime.config),
    }


def mark_call_operation_started(
    runtime: Any,
    prepared: PreparedCallOperation,
    *,
    task_id: str,
    dispatch_id: str,
    causation_id: str = "",
    correlation_id: str = "",
) -> None:
    from zf.runtime.workflow_budget_guard import usage_meter_snapshot

    receipt_descriptor: dict[str, Any] = {}
    receipt_error = ""
    if (
        prepared.context_delivery_envelope
        and prepared.context_delivery_envelope_ref
    ):
        from zf.runtime.context_delivery import write_context_delivery_receipt

        try:
            receipt_descriptor = write_context_delivery_receipt(
                runtime.state_dir,
                envelope=prepared.context_delivery_envelope,
                envelope_descriptor=prepared.context_delivery_envelope_ref,
                source_event_id=causation_id,
            )
        except Exception as exc:
            # H1 remains shadow-only. Missing delivery evidence must not turn a
            # successfully sent provider call into a duplicate dispatch.
            receipt_error = f"{type(exc).__name__}: {exc}"[:512]
    workflow_operation_service(runtime).mark_started(
        operation_id=prepared.operation_id,
        request_hash=prepared.request_hash,
        workflow_run_id=prepared.workflow_run_id,
        task_id=task_id or prepared.task_id,
        dispatch_id=dispatch_id,
        role_instance=prepared.role_instance,
        active_attempt_id=prepared.attempt_id,
        lease_id=prepared.lease_id,
        provider_session_id=prepared.provider_session_id,
        context_delivery_envelope_ref=prepared.context_delivery_envelope_ref,
        context_delivery_receipt_ref=receipt_descriptor,
        context_delivery_receipt_error=receipt_error,
        budget_snapshot=usage_meter_snapshot(
            runtime,
            instance_id=prepared.role_instance,
        ),
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
    operation_request = _pinned_operation_request(
        runtime,
        operation_id=str(payload.get("operation_id") or ""),
        request_hash=str(payload.get("request_hash") or ""),
    )
    operation_result_identity = (
        dict(operation_request.get("result_identity") or {})
        if isinstance(operation_request.get("result_identity"), Mapping)
        else {}
    )
    # Provider terminals are allowed to omit mechanical dispatch identity.  The
    # immutable operation request is authoritative for omitted fields, while an
    # explicitly returned value remains visible to mismatch/currentness checks.
    for key, value in operation_result_identity.items():
        if value not in (None, ""):
            payload.setdefault(key, value)

    source = replace(event, payload=payload)
    from zf.runtime.call_result_adapters import hydrate_profiled_control_result_event

    source = hydrate_profiled_control_result_event(runtime.state_dir, source)
    require_semantic_submit = (
        str(operation_request.get("semantic_result_submit_mode") or "")
        == "blocking"
    )
    semantic_submit = _has_semantic_submit_provenance(
        runtime,
        event,
        operation_request=operation_request,
    )
    effective_mode = (
        "blocking"
        if require_semantic_submit
        else mode or result_protocol_mode(runtime.config, payload)
    )
    # Verified semantic submissions are already admitted even when that path
    # is optional. Re-adapting their projection can change the verdict on replay.
    if semantic_submit:
        return CallResultAdmissionOutcome(
            status="admitted",
            mode=effective_mode,
            operation_id=str(payload.get("operation_id") or ""),
            request_hash=str(payload.get("request_hash") or ""),
            envelope_ref=dict(payload.get("call_result_envelope_ref") or {}),
            control_result_ref=dict(payload.get("control_result_ref") or {}),
            admitted_event_id=str(
                payload.get("semantic_submit_admission_event_id") or ""
            ),
        )
    outcome = call_result_admission_service(runtime).report_legacy_result(
        source,
        mode=effective_mode,
        operation={
            "workflow_run_id": str(payload.get("workflow_run_id") or ""),
            "parent_operation_id": str(payload.get("parent_operation_id") or ""),
            "operation_id": str(payload.get("operation_id") or ""),
            "request_hash": str(payload.get("request_hash") or ""),
            "result_identity": operation_result_identity,
            "output_profile_id": str(operation_request.get("output_profile_id") or ""),
            "output_profile_revision": str(operation_request.get("output_profile_revision") or ""),
            "canonical_success_event": str(operation_request.get("canonical_success_event") or ""),
            "handoff_authority_contract": dict(operation_request.get("handoff_authority_contract") or {}),
        },
        require_semantic_submit=require_semantic_submit,
        semantic_submit=semantic_submit,
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


def _pinned_operation_result_identity(
    runtime: Any,
    *,
    operation_id: str,
    request_hash: str,
) -> dict[str, Any]:
    request = _pinned_operation_request(
        runtime,
        operation_id=operation_id,
        request_hash=request_hash,
    )
    identity = request.get("result_identity")
    return dict(identity) if isinstance(identity, Mapping) else {}


def _pinned_operation_request(
    runtime: Any,
    *,
    operation_id: str,
    request_hash: str,
) -> dict[str, Any]:
    if not operation_id:
        return {}
    operation = load_workflow_operation(runtime.event_log, operation_id)
    if operation is None or (
        request_hash
        and str(operation.get("request_hash") or "") != request_hash
    ):
        return {}
    descriptor = operation.get("request_ref")
    if not isinstance(descriptor, Mapping):
        return {}
    try:
        stored = hydrate_sidecar_ref(runtime.state_dir, dict(descriptor)).payload
    except Exception:
        return {}
    request = stored.get("request") if isinstance(stored, Mapping) else None
    return dict(request) if isinstance(request, Mapping) else {}


def _has_semantic_submit_provenance(
    runtime: Any,
    event: ZfEvent,
    *,
    operation_request: Mapping[str, Any],
) -> bool:
    payload = event.payload if isinstance(event.payload, dict) else {}
    profile = payload.get("semantic_result_profile")
    control_ref = payload.get("control_result_ref")
    envelope_ref = payload.get("call_result_envelope_ref")
    if not all(
        isinstance(item, Mapping)
        for item in (profile, control_ref, envelope_ref)
    ):
        return False
    if (
        str(profile.get("profile_id") or "")
        != str(operation_request.get("output_profile_id") or "")
        or str(profile.get("revision") or "")
        != str(operation_request.get("output_profile_revision") or "")
    ):
        return False
    operation_id = str(payload.get("operation_id") or "")
    request_hash = str(payload.get("request_hash") or "")
    admission_event_id = str(
        payload.get("semantic_submit_admission_event_id") or ""
    )
    if not operation_id or not request_hash or not admission_event_id:
        return False
    candidate = _event_by_id(runtime, admission_event_id)
    if (
        candidate is None
        or candidate.type != "workflow.call.result.admitted"
        or candidate.actor != "zf-cli"
    ):
        return False
    body = candidate.payload if isinstance(candidate.payload, dict) else {}
    if (
        str(body.get("source_event_id") or "") != event.id
        or str(body.get("operation_id") or "") != operation_id
        or str(body.get("request_hash") or "") != request_hash
    ):
        return False
    return (
        _descriptor_identity(body.get("control_result_ref"))
        == _descriptor_identity(control_ref)
        and _descriptor_identity(body.get("envelope_ref"))
        == _descriptor_identity(envelope_ref)
    )


def has_admitted_semantic_submit_provenance(
    runtime: Any,
    event: ZfEvent,
) -> bool:
    """Return whether a canonical result is bound to its admitted call."""

    payload = event.payload if isinstance(event.payload, dict) else {}
    operation_request = _pinned_operation_request(
        runtime,
        operation_id=str(payload.get("operation_id") or ""),
        request_hash=str(payload.get("request_hash") or ""),
    )
    if not operation_request:
        return False
    return _has_semantic_submit_provenance(
        runtime,
        event,
        operation_request=operation_request,
    )


def _event_by_id(runtime: Any, event_id: str) -> ZfEvent | None:
    index = getattr(runtime.event_log, "index", None)
    if index is not None:
        cached = index.lookup_event(event_id)
        if cached is not None:
            return cached
    return next(
        (
            event
            for event in reversed(runtime.event_log.read_all())
            if event.id == event_id
        ),
        None,
    )


def _descriptor_identity(value: Any) -> tuple[str, str]:
    if not isinstance(value, Mapping):
        return "", ""
    return str(value.get("ref") or ""), str(value.get("sha256") or "")


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


def hydrate_runtime_call_result_event(runtime: Any, event: ZfEvent) -> ZfEvent | None:
    """Hydrate a ref-backed result or record one deterministic invalid event."""

    from zf.runtime.call_result_adapters import (
        ControlResultAdapterError,
        hydrate_profiled_control_result_event,
    )

    try:
        return hydrate_profiled_control_result_event(runtime.state_dir, event)
    except ControlResultAdapterError as exc:
        runtime.event_writer.append(ZfEvent(
            type="workflow.call.result.invalid",
            actor="zf-cli",
            task_id=event.task_id,
            payload={
                "schema_version": "call-result-admission.v1",
                "source_event_id": event.id,
                "source_event_type": event.type,
                "reason": "control_result_hydration_failed",
                "error": str(exc),
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        return None


def _context_protocol_for_operation(
    runtime: Any,
    *,
    operation_id: str,
    requested: Any,
) -> tuple[bool, dict[str, Any]]:
    from zf.runtime.context_delivery import normalize_context_inheritance

    operation = load_workflow_operation(runtime.event_log, operation_id)
    if operation is None:
        return True, normalize_context_inheritance(requested)
    descriptor = operation.get("request_ref")
    if not isinstance(descriptor, Mapping):
        raise WorkflowOperationError(
            f"workflow operation {operation_id} has no immutable request"
        )
    try:
        stored = hydrate_sidecar_ref(runtime.state_dir, dict(descriptor)).payload
    except Exception as exc:
        raise WorkflowOperationError(
            f"workflow operation {operation_id} request is unreadable"
        ) from exc
    request = stored.get("request") if isinstance(stored, Mapping) else None
    if not isinstance(request, Mapping):
        raise WorkflowOperationError(
            f"workflow operation {operation_id} request is invalid"
        )
    pinned = request.get("context_inheritance")
    if not isinstance(pinned, Mapping):
        # Pre-H0 operations retain their original manifest and request hash.
        return False, {}
    return True, normalize_context_inheritance(pinned)


def _semantic_submit_mode(
    config: Any,
    *,
    profile_id: str,
    role_instance: str,
    payload: Mapping[str, Any] | None = None,
) -> str:
    from zf.core.workflow.flow_metadata import flow_metadata_for

    metadata = flow_metadata_for(config, payload=payload)
    protocol = metadata.get("result_protocol")
    protocol = protocol if isinstance(protocol, Mapping) else {}
    configured = protocol.get("semantic_submit_profiles")
    configured = configured if isinstance(configured, Mapping) else {}
    mode = str(configured.get(profile_id) or "off").strip().lower()
    if mode not in {"shadow", "blocking"}:
        return "off"
    return mode


__all__ = [
    "PreparedCallOperation",
    "admit_runtime_call_result",
    "call_result_admission_service",
    "hydrate_admitted_control_result",
    "hydrate_runtime_call_result_event",
    "mark_call_operation_started",
    "prepare_call_operation",
    "workflow_operation_service",
]
