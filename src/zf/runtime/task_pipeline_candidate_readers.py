"""Bind frozen Task Pipeline candidates to configured candidate reader roles."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from zf.core.config.schema import FanoutAssignmentConfig, WorkflowStageConfig
from zf.core.events.model import ZfEvent
from zf.runtime.task_pipeline_contexts import CANDIDATE_FREEZE_RECEIPT_SCHEMA
from zf.runtime.task_pipeline_reconciler import task_pipeline_blocking


def candidate_reader_stages(runtime: Any, event: ZfEvent) -> list[WorkflowStageConfig]:
    return [
        frozen_candidate_reader_stage(runtime, stage, event)
        for stage in runtime.config.workflow.stages
        if stage.topology == "fanout_reader" and stage.trigger == event.type
        and runtime._fanout_stage_matches_trigger_event(stage, event)
    ]


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
