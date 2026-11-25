"""Bind frozen Task Pipeline candidates to configured candidate reader roles."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from zf.core.config.schema import FanoutAssignmentConfig, WorkflowStageConfig
from zf.core.events.model import ZfEvent
from zf.runtime.task_pipeline_contexts import CANDIDATE_FREEZE_RECEIPT_SCHEMA
from zf.runtime.task_pipeline_reconciler import task_pipeline_blocking


def candidate_reader_stages(runtime: Any, event: ZfEvent) -> list[WorkflowStageConfig]:
    from zf.runtime.stage_definition_binding import bound_stage

    frozen = bound_stage(runtime, event.payload or {})
    if frozen is not None:
        stages = [frozen] if frozen.trigger == event.type else []
    else:
        stages = [
            frozen_candidate_reader_stage(runtime, stage, event)
            for stage in runtime.config.workflow.stages
            if stage.topology == "fanout_reader" and stage.trigger == event.type
            and runtime._fanout_stage_matches_trigger_event(stage, event)
        ]
    selected = []
    for stage in stages:
        try:
            selected.append(_assigned_reader_stage(runtime, stage, event))
        except ValueError as exc:
            reject_candidate_assignment(runtime, event, stage.id, str(exc))
    return selected


def reject_candidate_assignment(runtime: Any, source: ZfEvent, stage_id: str, error: str) -> None:
    events = runtime.event_log.read_all()
    if any(e.type == "task.fanout.rejected" and e.causation_id == source.id
           and e.payload.get("stage_id") == stage_id for e in events):
        return
    runtime.event_writer.append(ZfEvent(
        type="task.fanout.rejected", actor="zf-cli", origin="kernel", task_id=source.task_id,
        causation_id=source.id, correlation_id=source.correlation_id,
        payload={"reason": "invalid_candidate_reader_assignment", "error": error,
                 "stage_id": stage_id, "source_event_id": source.id,
                 "workflow_run_id": source.payload.get("workflow_run_id") or source.correlation_id,
                 "recovery_owner": "run_manager", "diagnosis_status": "required"},
    ))


def _assigned_reader_stage(runtime: Any, stage: WorkflowStageConfig, event: ZfEvent) -> WorkflowStageConfig:
    from zf.core.workflow.flow_metadata import flow_metadata_for
    from zf.runtime.candidate_reader_assignments import assigned_spec
    from zf.runtime.call_result_admission import result_protocol_mode

    if (event.type != "candidate.ready" or stage.topology != "fanout_reader"
            or stage.aggregate.success_event not in {"test.passed", "verify.passed"}):
        return stage
    metadata = flow_metadata_for(runtime.config, payload=event.payload)
    teams = metadata.get("stage_teams") or {}
    strategy = teams.get("candidate_verify")
    body = event.payload
    if body.get("candidate_snapshot_event_id"):
        from zf.runtime.candidate_verification_authority import frozen_candidate_for_payload

        native = frozen_candidate_for_payload(runtime.event_log.read_all(),
            str(body.get("workflow_run_id") or event.correlation_id or ""), payload=body)
        if native is None:
            raise ValueError("Candidate Reader retrigger lacks its frozen candidate")
        body = native.payload
    if body.get("schema_version") != CANDIDATE_FREEZE_RECEIPT_SCHEMA:
        if strategy:
            raise ValueError("approved Candidate team requires a frozen Task Pipeline receipt")
        return stage
    spec = assigned_spec(Path(runtime.state_dir), body)
    if not spec:
        if strategy:
            raise ValueError("frozen candidate lacks the required Candidate Reader assignments")
        return stage
    if strategy and len(spec["candidate_reader_assignments"]) != strategy["width"]:
        raise ValueError("frozen Candidate Reader roster differs from approved verification width")
    kind = spec["flow_kind"]
    discovery = next((s for s in runtime.config.workflow.stages if s.id == f"{kind}-post-verify-discovery"
                      and s.trigger == "flow.discovery.requested" and s.topology == "fanout_reader"
                      and s.aggregate.child_success_event == "flow.discovery.child.completed"
                      and s.aggregate.success_event == "flow.discovery.completed"), None)
    discovery_roles = runtime._fanout_roles(discovery.roles) if discovery else []
    specialists = (teams.get("judge") or {}).get("members", [])
    required_roles = {row["role"] for row in specialists if row.get("required", True)}
    discovery_ids = {r.instance_id for r in discovery_roles}
    if (kind not in {"prd", "issue"} or metadata.get("flow_kind") != kind
            or metadata.get("post_verify_discovery") != "product_completeness" or discovery is None
            or stage.children or (discovery.children and not specialists) or discovery.assignment.strategy != "static_index"
            or stage.aggregate.review_strategy not in {"", "all_approve_or_one_rejects"}
            or stage.aggregate.mode != "wait_for_all" or not required_roles <= discovery_ids
            or len(discovery_ids) != 1 + len(required_roles)):
        raise ValueError("assigned Candidate readers require wait_for_all and a wired independent product_completeness Reader")
    submit_modes = (metadata.get("result_protocol") or {}).get("semantic_submit_profiles") or {}
    if (result_protocol_mode(runtime.config, body) != "blocking"
            or any(submit_modes.get(profile) != "blocking" for profile in ("candidate-verify", "workflow-read"))):
        raise ValueError("assigned Candidate readers require blocking Candidate and product discovery result admission")
    if stage.assignment.strategy == "affinity_stage_slots":
        eligible = {r.instance_id for _, r in runtime._fanout_affinity_lane_roles(stage)}
    else:
        eligible = {r.instance_id for r in runtime._fanout_roles(stage.roles)}
    if strategy:
        from zf.runtime.candidate_reader_assignments import approved_roster_ref
        binding = approved_roster_ref(Path(runtime.state_dir), runtime.event_log.read_all(), body, spec)
        if body.get("candidate_reader_approval") != binding:
            raise ValueError("Candidate Reader approval differs from the frozen decision")
        pool = metadata["task_pipeline"]["pools"]["verify"]["role_instances"]
        eligible = {r.instance_id for r in runtime.config.roles if r.role_kind == "reader" and r.instance_id in pool}
    roles = [r["role_instance"] for r in spec["candidate_reader_assignments"]]
    if set(roles) - eligible or (discovery_ids | {m["role"] for m in specialists}).intersection(roles):
        raise ValueError("assigned Candidate Reader requires a configured seat and independent discovery")
    return replace(stage, roles=roles, assignment=FanoutAssignmentConfig())


def frozen_candidate_reader_stage(
    runtime: Any, stage: WorkflowStageConfig, event: ZfEvent,
) -> WorkflowStageConfig:
    """Use the frozen candidate, not the planning fanout, as reader scope.

    Task Pipeline already verified each task before integration. The candidate
    receipt's fanout_id names planning provenance, not legacy writer children.
    Retain the configured reader pool and all result/target gates; only the
    per-task lane-affinity derivation is inapplicable to this aggregate target.
    """
    payload = event.payload if isinstance(event.payload, dict) else {}
    if not (
        event.type == "candidate.ready"
        and payload.get("schema_version") == CANDIDATE_FREEZE_RECEIPT_SCHEMA
        and payload.get("status") == "frozen"
        and payload.get("generation_id")
        and payload.get("candidate_head_commit")
        and stage.assignment.strategy == "affinity_stage_slots"
        and task_pipeline_blocking(runtime.config, flow_kind=str(
            payload.get("flow_kind") or payload.get("request_kind") or "",
        ))
    ):
        return stage
    roles = [role.instance_id for _lane, role in runtime._fanout_affinity_lane_roles(stage)]
    return replace(
        stage, roles=list(dict.fromkeys(roles)), assignment=FanoutAssignmentConfig(),
    )
