"""Keep task readers scoped when a wired discovery stage owns global closure."""

from __future__ import annotations

from typing import Any, Mapping

from zf.core.workflow.flow_metadata import flow_metadata_for


def candidate_bound_task_verify(operation: Mapping[str, Any]) -> bool:
    authority = operation.get("handoff_authority_contract")
    return (
        operation.get("output_profile_id") == "task-verify"
        and isinstance(authority, Mapping)
        and bool(authority.get("requires_candidate_lineage"))
    )


def verify_uses_independent_target(operation: Mapping[str, Any]) -> bool:
    """Pre-integration Task Verify and continuation Rescan pin their own target."""
    return (
        operation.get("output_profile_id") in {"task-verify", "global-rescan"}
        and not candidate_bound_task_verify(operation)
    ) or (
        operation.get("operation_type") == "task-stage"
        and operation.get("task_pipeline_stage") == "verify"
    )


def uses_scoped_candidate_verify(
    config: Any,
    *,
    payload: Mapping[str, Any],
    operation_type: str,
    stage_id: str,
) -> bool:
    """Select an existing Task Verify contract, never waive global acceptance."""

    if operation_type != "fanout_reader_child":
        return False
    task_id = str(payload.get("task_id") or "")
    if (
        not task_id
        or payload.get("upstream_task_id") != task_id
        or not payload.get("candidate_head_commit")
        or payload.get("assignment_strategy") != "affinity_stage_slots"
        or payload.get("stage_slot") != "verify"
        or payload.get("output_profile_id") not in (None, "", "task-verify")
    ):
        return False
    metadata = flow_metadata_for(config, payload=payload)
    kind = str(metadata.get("flow_kind") or "")
    if kind not in {"prd", "issue"} or not metadata.get("post_verify_discovery"):
        return False
    stages = list(getattr(getattr(config, "workflow", None), "stages", []) or [])
    stage = next((item for item in stages if item.id == stage_id), None)
    if (
        stage is None
        or stage.topology != "fanout_reader"
        or stage.trigger != "candidate.ready"
        or stage.assignment.strategy != "affinity_stage_slots"
        or stage.assignment.stage_slot != "verify"
    ):
        return False
    return any(
        item.id == f"{kind}-post-verify-discovery"
        and item.trigger == "flow.discovery.requested"
        and item.topology == "fanout_reader"
        and bool(item.roles)
        and item.aggregate.child_success_event == "flow.discovery.child.completed"
        and item.aggregate.success_event == "flow.discovery.completed"
        for item in stages
    )
