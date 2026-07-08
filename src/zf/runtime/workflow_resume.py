"""Workflow breakpoint resume projection and deterministic recovery actions."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.atomic_io import atomic_write_text
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.core.workflow.graph import compile_workflow_graph
from zf.runtime.candidate_rework import _feedback_lines_from_payload
from zf.runtime.workflow_reconciler import WorkflowGraphReconciler


WORKFLOW_RESUME_SCHEMA_VERSION = "workflow-resume.v0"
WORKFLOW_RESUME_EVENT = "workflow.resume.checkpoint"
WORKFLOW_RESUME_PLANNED_EVENT = "workflow.resume.planned"
WORKFLOW_RESUME_APPLIED_EVENT = "workflow.resume.applied"
WORKFLOW_RESUME_REJECTED_EVENT = "workflow.resume.rejected"
STAGE_TRANSITION_STALLED_EVENT = "stage.transition.stalled"
TASK_REF_REPAIR_REQUESTED_EVENT = "task.ref.repair.requested"
_CONTROL_REWORK_ROLES = {"", "orchestrator"}
_DESIGN_REWORK_ROLES = {"arch", "critic"}
_GENERIC_IMPL_ROLES = {"dev", "impl", "writer", "coding", "coding-agent"}

@dataclass(frozen=True)
class WorkflowResumeCheckpoint:
    task_id: str
    last_trusted_event_id: str
    last_completed_stage: str
    expected_next_stage: str
    expected_next_role: str
    blocking_event_id: str
    safe_resume_action: str
    idempotency_key: str
    evidence_event_ids: list[str] = field(default_factory=list)
    reason: str = ""
    source_event_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkflowBatchResumeCheckpoint:
    checkpoint_id: str
    source_event_id: str
    source_event_type: str
    blocking_event_id: str
    safe_resume_action: str
    pdd_id: str = ""
    feature_id: str = ""
    fanout_id: str = ""
    stage_id: str = ""
    trace_id: str = ""
    task_map_ref: str = ""
    source_index_ref: str = ""
    source_commit: str = ""
    target_ref: str = ""
    candidate_ref: str = ""
    candidate_base_commit: str = ""
    candidate_head_commit: str = ""
    diff_ref: str = ""
    upstream_fanout_id: str = ""
    completed_task_ids: list[str] = field(default_factory=list)
    failed_children: list[str] = field(default_factory=list)
    pending_children: list[str] = field(default_factory=list)
    evidence_event_ids: list[str] = field(default_factory=list)
    # avbs-r4 F2: reviewer findings 必须随 rework 走,否则盲 rework
    rework_feedback: list[str] = field(default_factory=list)
    reason: str = ""
    escalated: bool = False
    mutating_resume_supported: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_workflow_resume_projection(
    state_dir: Path,
    config: object,
    *,
    events: list[ZfEvent] | None = None,
    tasks: list[Task] | None = None,
) -> dict[str, Any]:
    checkpoints = build_workflow_resume_checkpoints(
        state_dir,
        config,
        events=events,
        tasks=tasks,
    )
    pending = [
        item for item in checkpoints
        if item.safe_resume_action != "no_action"
    ]
    event_list = list(events if events is not None else _read_events(Path(state_dir)))
    batch_checkpoints = build_workflow_batch_resume_checkpoints(
        state_dir,
        events=event_list,
    )
    pending_batch = [
        item for item in batch_checkpoints
        if item.safe_resume_action != "no_action"
    ]
    stale_workers = build_stale_worker_diagnostics(state_dir)
    return {
        "schema_version": WORKFLOW_RESUME_SCHEMA_VERSION,
        "is_derived_projection": True,
        "state_dir": str(Path(state_dir)),
        "summary": {
            "tasks": len(checkpoints),
            "pending": len(pending),
            "batch_checkpoints": len(batch_checkpoints),
            "batch_pending": len(pending_batch),
            "stale_workers": len(stale_workers),
            "by_action": _count_by_action(checkpoints),
            "by_batch_action": _count_batch_by_action(batch_checkpoints),
        },
        "checkpoints": [item.to_dict() for item in checkpoints],
        "batch_checkpoints": [item.to_dict() for item in batch_checkpoints],
        "worker_registry": {
            "source": "role_sessions.yaml",
            "stale": stale_workers,
        },
    }


def write_workflow_resume_projection(
    state_dir: Path,
    projection: dict[str, Any],
) -> Path:
    path = Path(state_dir) / "projections" / "workflow_resume.json"
    atomic_write_text(
        path,
        json.dumps(projection, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return path


def build_workflow_resume_checkpoints(
    state_dir: Path,
    config: object,
    *,
    events: list[ZfEvent] | None = None,
    tasks: list[Task] | None = None,
) -> list[WorkflowResumeCheckpoint]:
    state_dir = Path(state_dir)
    event_list = list(events if events is not None else _read_events(state_dir))
    task_list = list(tasks if tasks is not None else _read_tasks(state_dir))
    graph = compile_workflow_graph(config)  # type: ignore[arg-type]
    progress_events = set(graph.event_sets.stage_progress_events)
    progress_events.update(graph.event_sets.terminal_success_events)
    reconciler = WorkflowGraphReconciler()
    checkpoints: list[WorkflowResumeCheckpoint] = []
    checkpoints.extend(_reader_stage_replan_checkpoints(event_list, task_list, config))
    for task in task_list:
        if task.status in {"done", "cancelled"}:
            continue
        task_events = _events_for_task(event_list, task.id)
        ref_checkpoint = _task_ref_repair_checkpoint(task, task_events)
        if ref_checkpoint is not None:
            checkpoints.append(ref_checkpoint)
            continue
        progress = _latest_progress_event(task_events, progress_events)
        if progress is None:
            continue
        assignment_correction = _assignment_correction_checkpoint(task, progress, config)
        if assignment_correction is not None:
            checkpoints.append(assignment_correction)
            continue
        if _progress_waiting_for_task_ref_repair(task_events, progress):
            continue
        decisions = reconciler.plan(
            graph=graph,
            events=task_events,
            task=task,
            trigger_event=progress,
        )
        ready = [
            decision for decision in decisions
            if decision.ready and decision.action_plan is not None
        ]
        if not ready:
            if progress.type in graph.event_sets.handoff_success_events:
                already_done = _action_already_done(
                    task=task,
                    events=task_events,
                    source_event=progress,
                    action="needs_gate_dispatch",
                    target_role="",
                )
                checkpoints.append(
                    _no_action_checkpoint(task, progress, decisions)
                    if already_done
                    else _stalled_checkpoint(task, progress, decisions)
                )
            continue
        ready.sort(key=lambda item: _action_priority(
            item.action_plan.action_type if item.action_plan else ""
        ))
        decision = ready[0]
        plan = decision.action_plan
        if plan is None:
            continue
        action = _safe_action_for_plan(plan.action_type)
        target_role = str(plan.payload.get("target_role") or "")
        already_done = _action_already_done(
            task=task,
            events=task_events,
            all_events=event_list,
            source_event=progress,
            action=action,
            target_role=target_role,
        )
        invalid_existing_action = False
        if already_done and action == "needs_rework_dispatch":
            invalid_existing_action = _existing_rework_action_targets_non_lane_role(
                task=task,
                events=task_events,
                source_event=progress,
                config=config,
            )
        if already_done:
            action = "needs_rework_dispatch" if invalid_existing_action else "no_action"
        # 131-P2-4:RM 只对 attempt-ready failure 派 rework。avbs-r5 活锁:
        # 旧 task.assigned 触发 silent_stall → 重派 rework,把 assignment
        # 之后已到达的 completion 应答直接丢弃(task.rework.requested 251 次)。
        if action == "needs_rework_dispatch" and not _rework_attempt_ready(
            task_events, task.id,
        ):
            action = "no_action"
        checkpoints.append(WorkflowResumeCheckpoint(
            task_id=task.id,
            last_trusted_event_id=progress.id,
            last_completed_stage=_stage_from_event(progress.type),
            expected_next_stage=decision.stage_id,
            expected_next_role=target_role,
            blocking_event_id="" if already_done and not invalid_existing_action else progress.id,
            safe_resume_action=action,
            idempotency_key=_idempotency_key(
                task.id,
                progress.id,
                action,
                (
                    f"{target_role or decision.stage_id}:invalid-existing-action"
                    if invalid_existing_action
                    else target_role or decision.stage_id
                ),
            ),
            evidence_event_ids=[progress.id],
            reason=(
                "existing next action targets non-runnable lane role"
                if invalid_existing_action else
                "next action already exists after checkpoint"
                if already_done else decision.reason
            ),
            source_event_type=progress.type,
        ))
    return checkpoints


def build_workflow_batch_resume_checkpoints(
    state_dir: Path,
    *,
    events: list[ZfEvent] | None = None,
) -> list[WorkflowBatchResumeCheckpoint]:
    state_dir = Path(state_dir)
    event_list = list(events if events is not None else _read_events(state_dir))
    anchors = _batch_resume_anchors(event_list)
    aggregates: dict[str, WorkflowBatchResumeCheckpoint] = {}
    integrations: dict[str, WorkflowBatchResumeCheckpoint] = {}
    integration_fanout_ids: set[str] = set()
    escalated_integration_ids: set[str] = set()
    escalations: list[WorkflowBatchResumeCheckpoint] = []

    for event in event_list:
        payload = _payload(event)
        if event.type == "fanout.aggregate.completed":
            status = str(payload.get("status") or "completed")
            if status == "completed":
                continue
            checkpoint = _batch_checkpoint_from_event(
                event,
                source_event_type=event.type,
                safe_resume_action=_batch_safe_action(payload),
                evidence_event_ids=[event.id],
                anchor=_anchor_for_event(event, anchors),
            )
            if checkpoint.fanout_id:
                aggregates[checkpoint.fanout_id] = checkpoint
            continue

        if event.type == "integration.failed":
            fanout_id = str(payload.get("fanout_id") or "").strip()
            aggregate = aggregates.get(fanout_id) if fanout_id else None
            checkpoint = _batch_checkpoint_from_event(
                event,
                source_event_type=event.type,
                safe_resume_action=_batch_safe_action(payload, aggregate=aggregate),
                evidence_event_ids=[
                    item for item in [
                        aggregate.source_event_id if aggregate else "",
                        event.id,
                    ] if item
                ],
                aggregate=aggregate,
                anchor=_anchor_for_event(event, anchors),
            )
            integrations[event.id] = checkpoint
            if checkpoint.fanout_id:
                integration_fanout_ids.add(checkpoint.fanout_id)
            continue

        if event.type == "fanout.cancelled":
            if not _fanout_cancelled_resume_supported(payload):
                continue
            checkpoint = _batch_checkpoint_from_event(
                event,
                source_event_type=event.type,
                safe_resume_action=_batch_safe_action(payload),
                evidence_event_ids=[event.id],
                anchor=_anchor_for_event(event, anchors),
            )
            if checkpoint.fanout_id:
                aggregates[checkpoint.fanout_id] = checkpoint
            continue

        if event.type != "human.escalate":
            continue
        source_id = str(
            payload.get("rework_of")
            or payload.get("source_event_id")
            or payload.get("trigger_event_id")
            or ""
        ).strip()
        base = integrations.get(source_id) if source_id else None
        if base is None and str(payload.get("rework_source") or "") == "integration.failed":
            base = _latest_matching_integration(integrations, event)
        if base is None:
            continue
        escalated_integration_ids.add(base.source_event_id)
        escalations.append(_escalated_batch_checkpoint(event, base))

    out: list[WorkflowBatchResumeCheckpoint] = []
    out.extend(escalations)
    out.extend(
        checkpoint for event_id, checkpoint in integrations.items()
        if event_id not in escalated_integration_ids
    )
    out.extend(
        checkpoint for fanout_id, checkpoint in aggregates.items()
        if fanout_id not in integration_fanout_ids
    )
    return [
        checkpoint for checkpoint in out
        if not _batch_checkpoint_recovered(event_list, checkpoint)
        and not _batch_checkpoint_superseded(event_list, checkpoint)
    ]


def _task_ref_repair_checkpoint(
    task: Task,
    events: list[ZfEvent],
) -> WorkflowResumeCheckpoint | None:
    latest_rejection: ZfEvent | None = None
    for event in events:
        if event.type != "task.ref.rejected":
            continue
        trigger_id = str(_payload(event).get("trigger_event_id") or "")
        if _task_ref_rejection_recovered(events, event, trigger_id):
            continue
        latest_rejection = event
    if latest_rejection is None:
        return None
    trigger_event_id = str(_payload(latest_rejection).get("trigger_event_id") or "")
    evidence = [latest_rejection.id]
    if trigger_event_id:
        evidence.insert(0, trigger_event_id)
    return WorkflowResumeCheckpoint(
        task_id=task.id,
        last_trusted_event_id=trigger_event_id or latest_rejection.id,
        last_completed_stage="impl",
        expected_next_stage="task_ref_repair",
        expected_next_role="dev",
        blocking_event_id=latest_rejection.id,
        safe_resume_action="needs_task_ref_repair",
        idempotency_key=_idempotency_key(
            task.id,
            trigger_event_id or latest_rejection.id,
            "needs_task_ref_repair",
            "task_ref",
        ),
        evidence_event_ids=evidence,
        reason=str(_payload(latest_rejection).get("reason") or "task ref rejected"),
        source_event_type="task.ref.rejected",
    )


def _task_ref_rejection_recovered(
    events: list[ZfEvent],
    rejection: ZfEvent,
    trigger_id: str,
) -> bool:
    seen_rejection = False
    for event in events:
        if event.id == rejection.id:
            seen_rejection = True
            continue
        if not seen_rejection:
            continue
        if event.type == "dev.build.done" and event.id != trigger_id:
            return True
        payload = _payload(event)
        event_key = str(
            payload.get("trigger_event_id")
            or payload.get("source_event_id")
            or ""
        )
        if trigger_id and event_key != trigger_id:
            continue
        if event.type in {
            "task.ref.updated",
            TASK_REF_REPAIR_REQUESTED_EVENT,
            WORKFLOW_RESUME_APPLIED_EVENT,
            "task.rework.requested",
        }:
            return True
        if event.type == "task.dispatched" and str(
            payload.get("trigger_event") or payload.get("trigger_event_type") or ""
        ) == TASK_REF_REPAIR_REQUESTED_EVENT:
            return True
    return False


def _batch_checkpoint_from_event(
    event: ZfEvent,
    *,
    source_event_type: str,
    safe_resume_action: str,
    evidence_event_ids: list[str],
    aggregate: WorkflowBatchResumeCheckpoint | None = None,
    anchor: dict[str, str] | None = None,
) -> WorkflowBatchResumeCheckpoint:
    payload = _payload(event)
    anchor = anchor or {}
    fanout_id = _first_nonempty(payload.get("fanout_id"), aggregate.fanout_id if aggregate else "")
    pdd_id = _first_nonempty(payload.get("pdd_id"), aggregate.pdd_id if aggregate else "")
    feature_id = _first_nonempty(payload.get("feature_id"), aggregate.feature_id if aggregate else "")
    stage_id = _first_nonempty(payload.get("stage_id"), aggregate.stage_id if aggregate else "")
    source_commit = _first_nonempty(
        payload.get("source_commit"),
        anchor.get("source_commit"),
        aggregate.source_commit if aggregate else "",
    )
    candidate_base_commit = _first_nonempty(
        payload.get("candidate_base_commit"),
        payload.get("base_commit"),
        anchor.get("candidate_base_commit"),
        anchor.get("source_commit"),
        aggregate.candidate_base_commit if aggregate else "",
        source_commit,
    )
    candidate_head_commit = _first_nonempty(
        payload.get("candidate_head_commit"),
        payload.get("head_commit"),
        anchor.get("candidate_head_commit"),
        aggregate.candidate_head_commit if aggregate else "",
    )
    candidate_ref = _first_nonempty(
        payload.get("candidate_ref"),
        payload.get("target_ref"),
        anchor.get("candidate_ref"),
        aggregate.candidate_ref if aggregate else "",
    )
    completed_task_ids = _unique_strings([
        *_string_list(aggregate.completed_task_ids if aggregate else []),
        *_string_list(payload.get("completed_task_ids")),
        *_string_list(payload.get("task_ids")),
    ])
    failed_children = _unique_strings([
        *_string_list(aggregate.failed_children if aggregate else []),
        *_string_list(payload.get("failed_children")),
        *_string_list(payload.get("failed_task_ids")),
    ])
    pending_children = _unique_strings([
        *_string_list(aggregate.pending_children if aggregate else []),
        *_string_list(payload.get("pending_children")),
    ])
    action = (
        "repair_failed_children"
        if failed_children and safe_resume_action == "blocked_external_gate"
        else safe_resume_action
    )
    return WorkflowBatchResumeCheckpoint(
        checkpoint_id=_idempotency_key(
            "__batch__",
            event.id,
            action,
            fanout_id or pdd_id or source_event_type,
        ),
        source_event_id=event.id,
        source_event_type=source_event_type,
        blocking_event_id=event.id,
        safe_resume_action=action,
        pdd_id=pdd_id,
        feature_id=feature_id,
        fanout_id=fanout_id,
        upstream_fanout_id=_first_nonempty(
            _payload_upstream_fanout_id(payload),
            aggregate.upstream_fanout_id if aggregate else "",
            fanout_id,
        ),
        stage_id=stage_id,
        trace_id=_first_nonempty(
            payload.get("trace_id"),
            event.correlation_id,
            anchor.get("trace_id"),
            aggregate.trace_id if aggregate else "",
        ),
        task_map_ref=_first_nonempty(
            payload.get("task_map_ref"),
            anchor.get("task_map_ref"),
            aggregate.task_map_ref if aggregate else "",
        ),
        source_index_ref=_first_nonempty(
            payload.get("source_index_ref"),
            anchor.get("source_index_ref"),
            aggregate.source_index_ref if aggregate else "",
        ),
        source_commit=source_commit,
        target_ref=_first_nonempty(
            payload.get("target_ref"),
            anchor.get("target_ref"),
            aggregate.target_ref if aggregate else "",
        ),
        candidate_ref=candidate_ref,
        candidate_base_commit=candidate_base_commit,
        candidate_head_commit=candidate_head_commit,
        diff_ref=_first_nonempty(
            payload.get("diff_ref"),
            anchor.get("diff_ref"),
            aggregate.diff_ref if aggregate else "",
            (
                f"{candidate_base_commit}..{candidate_head_commit}"
                if candidate_base_commit and candidate_head_commit
                else ""
            ),
        ),
        completed_task_ids=completed_task_ids,
        failed_children=failed_children,
        pending_children=pending_children,
        evidence_event_ids=_unique_strings(evidence_event_ids),
        rework_feedback=_unique_strings([
            *(aggregate.rework_feedback if aggregate else []),
            *_feedback_lines_from_payload(payload),
        ])[:40],
        reason=_first_nonempty(
            payload.get("reason"),
            payload.get("status"),
            aggregate.reason if aggregate else "",
        ),
    )


def _escalated_batch_checkpoint(
    event: ZfEvent,
    base: WorkflowBatchResumeCheckpoint,
) -> WorkflowBatchResumeCheckpoint:
    payload = _payload(event)
    evidence = _unique_strings([*base.evidence_event_ids, event.id])
    return WorkflowBatchResumeCheckpoint(
        checkpoint_id=_idempotency_key(
            "__batch__",
            event.id,
            base.safe_resume_action,
            base.fanout_id or base.pdd_id or base.source_event_id,
        ),
        source_event_id=base.source_event_id,
        source_event_type="human.escalate",
        blocking_event_id=event.id,
        safe_resume_action=base.safe_resume_action,
        pdd_id=_first_nonempty(payload.get("pdd_id"), base.pdd_id),
        feature_id=_first_nonempty(payload.get("feature_id"), base.feature_id),
        fanout_id=_first_nonempty(payload.get("fanout_id"), base.fanout_id),
        upstream_fanout_id=_first_nonempty(
            _payload_upstream_fanout_id(payload),
            base.upstream_fanout_id,
            base.fanout_id,
        ),
        stage_id=base.stage_id,
        trace_id=_first_nonempty(payload.get("trace_id"), event.correlation_id, base.trace_id),
        task_map_ref=base.task_map_ref,
        source_index_ref=base.source_index_ref,
        source_commit=base.source_commit,
        target_ref=base.target_ref,
        candidate_ref=base.candidate_ref,
        candidate_base_commit=base.candidate_base_commit,
        candidate_head_commit=base.candidate_head_commit,
        diff_ref=base.diff_ref,
        completed_task_ids=list(base.completed_task_ids),
        failed_children=list(base.failed_children),
        pending_children=list(base.pending_children),
        evidence_event_ids=evidence,
        rework_feedback=_unique_strings([
            *base.rework_feedback,
            *_feedback_lines_from_payload(payload),
        ])[:40],
        reason=_first_nonempty(payload.get("reason"), base.reason),
        escalated=True,
    )


def _batch_safe_action(
    payload: dict[str, Any],
    *,
    aggregate: WorkflowBatchResumeCheckpoint | None = None,
) -> str:
    failed_children = _string_list(payload.get("failed_children"))
    failed_task_ids = _string_list(payload.get("failed_task_ids"))
    if aggregate is not None:
        failed_children.extend(aggregate.failed_children)
    if failed_children or failed_task_ids:
        return "repair_failed_children"
    pending_children = _string_list(payload.get("pending_children"))
    if aggregate is not None:
        pending_children.extend(aggregate.pending_children)
    if pending_children:
        return "wait_for_children"
    completed_task_ids = _string_list(payload.get("completed_task_ids"))
    candidate_ref = str(payload.get("candidate_ref") or payload.get("target_ref") or "").strip()
    candidate_head = str(payload.get("candidate_head_commit") or payload.get("head_commit") or "").strip()
    if aggregate is not None:
        completed_task_ids.extend(aggregate.completed_task_ids)
        candidate_ref = candidate_ref or aggregate.candidate_ref
        candidate_head = candidate_head or aggregate.candidate_head_commit
    if candidate_ref and candidate_head and completed_task_ids:
        return "reemit_candidate_ready"
    if str(payload.get("task_map_ref") or "").strip() or (
        aggregate is not None and aggregate.task_map_ref
    ):
        return "trigger_rework"
    return "blocked_external_gate"


def _fanout_cancelled_resume_supported(payload: dict[str, Any]) -> bool:
    reason = str(payload.get("reason") or payload.get("error") or "")
    if not str(payload.get("task_map_ref") or "").strip():
        return False
    if "task_map validation failed" in reason:
        return False
    if "writer fanout task_map has no tasks" in reason:
        return False
    return "writer fanout has more tasks than writer role instances" in reason


def _latest_matching_integration(
    integrations: dict[str, WorkflowBatchResumeCheckpoint],
    event: ZfEvent,
) -> WorkflowBatchResumeCheckpoint | None:
    payload = _payload(event)
    pdd_id = str(payload.get("pdd_id") or "").strip()
    fanout_id = str(payload.get("fanout_id") or "").strip()
    for checkpoint in reversed(list(integrations.values())):
        if fanout_id and checkpoint.fanout_id == fanout_id:
            return checkpoint
        if pdd_id and checkpoint.pdd_id == pdd_id:
            return checkpoint
    return None


def _batch_checkpoint_recovered(
    events: list[ZfEvent],
    checkpoint: WorkflowBatchResumeCheckpoint,
) -> bool:
    recovered = False
    invalidated = False
    for event in events:
        payload = _payload(event)
        if (
            _payload_has_resume_marker(payload, checkpoint.checkpoint_id)
            and event.type in {
            "workflow.resume.applied",
            "task_map.ready",
            "candidate.ready",
            "lane.stage.completed",
            "fanout.aggregate.completed",
            "verify.passed",
            "test.passed",
            "judge.passed",
            "run.completed",
            "orchestrator.replan_requested",
            }
        ):
            recovered = True
            continue
        if event.type in {
            "lane.stage.completed",
            "fanout.aggregate.completed",
            "candidate.ready",
            "verify.passed",
            "test.passed",
            "judge.passed",
            "run.completed",
        } and _batch_success_matches_checkpoint(payload, checkpoint):
            recovered = True
            continue
        if not recovered:
            continue
        if event.type != "fanout.cancelled":
            continue
        reason = str(payload.get("reason") or payload.get("error") or "")
        stage_id = str(payload.get("stage_id") or "")
        if (
            "task_map validation failed" in reason
            and (not stage_id or stage_id == checkpoint.stage_id)
        ):
            invalidated = True
        if "writer fanout task_map has no tasks" in reason:
            invalidated = True
    return recovered and not invalidated


def _payload_has_resume_marker(payload: dict[str, Any], checkpoint_id: str) -> bool:
    checkpoint_id = str(checkpoint_id or "").strip()
    if not checkpoint_id:
        return False
    markers = {
        str(payload.get("idempotency_key") or "").strip(),
        str(payload.get("resume_checkpoint_ref") or "").strip(),
    }
    return checkpoint_id in markers or any(
        marker.startswith(checkpoint_id + ":")
        for marker in markers
        if marker
    )


def _batch_checkpoint_superseded(
    events: list[ZfEvent],
    checkpoint: WorkflowBatchResumeCheckpoint,
) -> bool:
    return bool(_batch_checkpoint_superseded_reason(events, checkpoint))


def _batch_checkpoint_superseded_reason(
    events: list[ZfEvent],
    checkpoint: WorkflowBatchResumeCheckpoint,
) -> str:
    if checkpoint.safe_resume_action in {"repair_failed_children", "trigger_rework"}:
        return _repair_checkpoint_superseded_reason(events, checkpoint)
    if checkpoint.safe_resume_action != "reemit_candidate_ready":
        return ""
    if not checkpoint.candidate_head_commit:
        return ""
    source_idx = next(
        (idx for idx, event in enumerate(events) if event.id == checkpoint.source_event_id),
        -1,
    )
    if source_idx < 0:
        return ""
    for event in events[source_idx + 1:]:
        payload = _payload(event)
        if event.type == "candidate.quality.passed":
            commit = str(payload.get("commit") or payload.get("candidate_head_commit") or "").strip()
            if (
                commit
                and commit != checkpoint.candidate_head_commit
                and _same_candidate_scope(payload, checkpoint, branch_key="branch")
            ):
                return (
                    "stale batch checkpoint superseded by newer candidate "
                    f"quality pass {event.id}"
                )
            continue
        if event.type != "candidate.ready":
            continue
        if str(payload.get("source") or "") == "workflow_resume_batch":
            continue
        head = str(payload.get("candidate_head_commit") or "").strip()
        if (
            head
            and head != checkpoint.candidate_head_commit
            and _same_candidate_scope(payload, checkpoint)
        ):
            return (
                "stale batch checkpoint superseded by newer candidate.ready "
                f"{event.id}"
            )
    return ""


def _repair_checkpoint_superseded_reason(
    events: list[ZfEvent],
    checkpoint: WorkflowBatchResumeCheckpoint,
) -> str:
    """BF-2(r6.1 断点复盘):同一失败周期的重复 repair 去重。

    human.escalate 与 integration.failed 会对同一个失败 fanout 各生成
    一个 repair 批检查点,6 分钟内起两个 fanout(后者立即 supersede
    前者)。source 事件之后同 stage 同 pdd 域已有新 fanout.started,
    说明这次失败的修复已经在跑——第二个检查点判 superseded。
    """
    if not checkpoint.stage_id:
        return ""
    source_idx = next(
        (idx for idx, event in enumerate(events) if event.id == checkpoint.source_event_id),
        -1,
    )
    if source_idx < 0:
        return ""
    later_success = _repair_checkpoint_later_success_reason(
        events,
        checkpoint,
        source_idx,
    )
    if later_success:
        return later_success
    known_fanouts = {checkpoint.fanout_id, checkpoint.upstream_fanout_id} - {""}
    # A4(prd-goal e2e finding-9,BF-2 同秒盲区):时序守卫挡不住同秒
    # 并发的多源检查点。补一刀:同 stage 同域存在**进行中**(已 started
    # 无终局)的 fanout,无论先后一律拒——修复已在跑,重复 repair 只会
    # 互噬(30 秒三连 replan 实弹)。
    inflight: dict[str, dict] = {}
    for event in events:
        payload = _payload(event)
        fanout_id = str(payload.get("fanout_id") or "")
        if not fanout_id:
            continue
        if event.type == "fanout.started":
            inflight[fanout_id] = payload
        elif event.type in {
            "fanout.aggregate.completed", "fanout.timed_out", "fanout.cancelled",
        }:
            inflight.pop(fanout_id, None)
    for fanout_id, payload in inflight.items():
        if fanout_id in known_fanouts:
            continue
        if str(payload.get("stage_id") or "") != checkpoint.stage_id:
            continue
        pdd_id = str(payload.get("pdd_id") or payload.get("feature_id") or "")
        checkpoint_scope = {checkpoint.pdd_id, checkpoint.feature_id} - {""}
        if checkpoint_scope and pdd_id and pdd_id not in checkpoint_scope:
            continue
        return (
            f"repair for stage {checkpoint.stage_id} already in flight: "
            f"{fanout_id}"
        )
    for event in events[source_idx + 1:]:
        if event.type != "fanout.started":
            continue
        payload = _payload(event)
        if str(payload.get("stage_id") or "") != checkpoint.stage_id:
            continue
        fanout_id = str(payload.get("fanout_id") or "")
        if not fanout_id or fanout_id in known_fanouts:
            continue
        pdd_id = str(payload.get("pdd_id") or payload.get("feature_id") or "")
        checkpoint_scope = {checkpoint.pdd_id, checkpoint.feature_id} - {""}
        if checkpoint_scope and pdd_id and pdd_id not in checkpoint_scope:
            continue
        return (
            f"repair for stage {checkpoint.stage_id} already started by "
            f"{fanout_id} ({event.id})"
        )
    return ""


def _repair_checkpoint_later_success_reason(
    events: list[ZfEvent],
    checkpoint: WorkflowBatchResumeCheckpoint,
    source_idx: int,
) -> str:
    """Detect a repair checkpoint already closed by later durable progress.

    A dirty-workdir handoff can emit ``integration.failed`` and then be fixed
    by a later ``task.ref.updated`` / ``candidate.ready`` before Run Manager
    consumes the batch checkpoint. In that case a second repair fanout is
    duplicate work and can race the already-progressing flow.
    """
    known_fanouts = {checkpoint.fanout_id, checkpoint.upstream_fanout_id} - {""}
    failed_children = set(checkpoint.failed_children)
    for event in events[source_idx + 1:]:
        payload = _payload(event)
        if event.type == "task.ref.updated" and _task_ref_update_matches_repair(
            events,
            event,
            checkpoint,
            known_fanouts,
            failed_children,
        ):
            return f"repair checkpoint superseded by task.ref.updated {event.id}"
        if event.type == "fanout.child.completed":
            fanout_id = str(payload.get("fanout_id") or "")
            child_id = str(payload.get("child_id") or "")
            if fanout_id in known_fanouts and (
                not failed_children or child_id in failed_children
            ):
                return (
                    "repair checkpoint superseded by completed fanout child "
                    f"{event.id}"
                )
        if event.type in {
            "candidate.ready",
            "test.passed",
            "verify.passed",
            "flow.discovery.completed",
            "judge.passed",
            "run.completed",
        } and _batch_success_matches_checkpoint(payload, checkpoint):
            return f"repair checkpoint superseded by {event.type} {event.id}"
    return ""


def _task_ref_update_matches_repair(
    events: list[ZfEvent],
    event: ZfEvent,
    checkpoint: WorkflowBatchResumeCheckpoint,
    known_fanouts: set[str],
    failed_children: set[str],
) -> bool:
    payload = _payload(event)
    trigger_id = str(payload.get("trigger_event_id") or "")
    task_id = str(event.task_id or payload.get("task_id") or "")
    trigger_payload: dict[str, Any] = {}
    if trigger_id:
        for candidate in events:
            if candidate.id == trigger_id:
                trigger_payload = _payload(candidate)
                break
    fanout_id = str(
        trigger_payload.get("fanout_id")
        or payload.get("fanout_id")
        or ""
    )
    child_id = str(
        trigger_payload.get("child_id")
        or payload.get("child_id")
        or ""
    )
    if fanout_id and known_fanouts and fanout_id not in known_fanouts:
        return False
    if failed_children and child_id:
        return child_id in failed_children
    if failed_children and task_id:
        return any(
            failed == task_id or failed.endswith(f"-{task_id}")
            for failed in failed_children
        )
    return bool(fanout_id and fanout_id in known_fanouts)


def _batch_success_matches_checkpoint(
    payload: dict[str, Any],
    checkpoint: WorkflowBatchResumeCheckpoint,
) -> bool:
    status = str(payload.get("status") or payload.get("quality_status") or "").lower()
    if status in {"failed", "failure", "rejected", "blocked"}:
        return False
    if _string_list(payload.get("failed_children")):
        return False
    scope = {
        checkpoint.pdd_id,
        checkpoint.feature_id,
        checkpoint.trace_id,
        checkpoint.candidate_ref,
        checkpoint.task_map_ref,
    } - {""}
    if not scope:
        return False
    payload_scope = {
        str(payload.get("pdd_id") or ""),
        str(payload.get("feature_id") or ""),
        str(payload.get("trace_id") or ""),
        str(payload.get("candidate_ref") or payload.get("target_ref") or ""),
        str(payload.get("task_map_ref") or ""),
    } - {""}
    if not scope.intersection(payload_scope):
        return False
    completed_task_ids = set(_string_list(payload.get("completed_task_ids")))
    if checkpoint.failed_children and completed_task_ids:
        return any(
            failed == completed
            or failed.endswith(f"-{completed}")
            or completed.endswith(f"-{failed}")
            for failed in checkpoint.failed_children
            for completed in completed_task_ids
        )
    return True


def _same_candidate_scope(
    payload: dict[str, Any],
    checkpoint: WorkflowBatchResumeCheckpoint,
    *,
    branch_key: str = "candidate_ref",
) -> bool:
    pdd_id = str(payload.get("pdd_id") or payload.get("feature_id") or "").strip()
    branch = str(payload.get(branch_key) or payload.get("candidate_ref") or "").strip()
    fanout_id = str(payload.get("fanout_id") or payload.get("upstream_fanout_id") or "").strip()
    pdd_matches = bool(
        pdd_id
        and checkpoint.pdd_id
        and pdd_id in {checkpoint.pdd_id, checkpoint.feature_id}
    )
    branch_matches = bool(
        branch
        and checkpoint.candidate_ref
        and branch == checkpoint.candidate_ref
    )
    fanout_matches = bool(
        fanout_id
        and checkpoint.fanout_id
        and fanout_id in {checkpoint.fanout_id, checkpoint.upstream_fanout_id}
    )
    return branch_matches or (pdd_matches and (not fanout_id or fanout_matches))


def _batch_resume_anchors(events: list[ZfEvent]) -> dict[str, dict[str, str]]:
    anchors: dict[str, dict[str, str]] = {}
    anchor_keys = (
        "pdd_id",
        "feature_id",
        "fanout_id",
        "trace_id",
        "task_map_ref",
        "source_index_ref",
        "source_commit",
        "target_ref",
        "candidate_ref",
        "candidate_base_commit",
        "candidate_head_commit",
        "diff_ref",
    )

    def merge(key: str, payload: dict[str, Any], event: ZfEvent) -> None:
        if not key:
            return
        row = anchors.setdefault(key, {})
        for field in anchor_keys:
            value = str(payload.get(field) or "").strip()
            if value:
                row[field] = value
        if event.correlation_id and not row.get("trace_id"):
            row["trace_id"] = str(event.correlation_id)

    for event in events:
        if event.type not in {
            "task_map.ready",
            "fanout.started",
            "fanout.aggregate.completed",
            "candidate.ready",
            "integration.failed",
        }:
            continue
        payload = _payload(event)
        pdd_id = str(payload.get("pdd_id") or payload.get("feature_id") or "").strip()
        fanout_id = str(payload.get("fanout_id") or "").strip()
        trace_id = str(payload.get("trace_id") or event.correlation_id or "").strip()
        for key in (
            f"pdd:{pdd_id}" if pdd_id else "",
            f"fanout:{fanout_id}" if fanout_id else "",
            f"trace:{trace_id}" if trace_id else "",
        ):
            merge(key, payload, event)
    return anchors


def _anchor_for_event(
    event: ZfEvent,
    anchors: dict[str, dict[str, str]],
) -> dict[str, str]:
    payload = _payload(event)
    keys = [
        f"fanout:{payload.get('fanout_id')}"
        if str(payload.get("fanout_id") or "").strip() else "",
        f"pdd:{payload.get('pdd_id') or payload.get('feature_id')}"
        if str(payload.get("pdd_id") or payload.get("feature_id") or "").strip() else "",
        f"trace:{payload.get('trace_id') or event.correlation_id}"
        if str(payload.get("trace_id") or event.correlation_id or "").strip() else "",
    ]
    merged: dict[str, str] = {}
    for key in keys:
        if key and key in anchors:
            merged.update(anchors[key])
    return merged


def _latest_progress_event(
    events: list[ZfEvent],
    progress_events: set[str],
) -> ZfEvent | None:
    latest: ZfEvent | None = None
    for event in events:
        if event.type in progress_events:
            latest = event
    return latest


def _progress_waiting_for_task_ref_repair(
    events: list[ZfEvent],
    progress: ZfEvent,
) -> bool:
    if progress.type != "dev.build.done":
        return False
    seen_progress = False
    rejected_or_repair = False
    for event in events:
        if event.id == progress.id:
            seen_progress = True
            continue
        if not seen_progress:
            continue
        payload = _payload(event)
        source_id = str(
            payload.get("trigger_event_id")
            or payload.get("source_event_id")
            or ""
        )
        if source_id != progress.id:
            continue
        if event.type == "task.ref.updated":
            return False
        if event.type in {"task.ref.rejected", TASK_REF_REPAIR_REQUESTED_EVENT}:
            rejected_or_repair = True
    return rejected_or_repair


def _stalled_checkpoint(
    task: Task,
    event: ZfEvent,
    decisions: list[Any],
) -> WorkflowResumeCheckpoint:
    expected_stage = ""
    reason = "no ready next-stage decision"
    if decisions:
        expected_stage = str(getattr(decisions[0], "stage_id", "") or "")
        reason = "; ".join(
            str(getattr(item, "reason", "") or "")
            for item in decisions[:3]
            if str(getattr(item, "reason", "") or "")
        ) or reason
    return WorkflowResumeCheckpoint(
        task_id=task.id,
        last_trusted_event_id=event.id,
        last_completed_stage=_stage_from_event(event.type),
        expected_next_stage=expected_stage,
        expected_next_role="",
        blocking_event_id=event.id,
        safe_resume_action="blocked_external_gate",
        idempotency_key=_idempotency_key(
            task.id,
            event.id,
            "blocked_external_gate",
            expected_stage,
        ),
        evidence_event_ids=[event.id],
        reason=reason,
        source_event_type=event.type,
    )


def _no_action_checkpoint(
    task: Task,
    event: ZfEvent,
    decisions: list[Any],
) -> WorkflowResumeCheckpoint:
    expected_stage = ""
    if decisions:
        expected_stage = str(getattr(decisions[0], "stage_id", "") or "")
    return WorkflowResumeCheckpoint(
        task_id=task.id,
        last_trusted_event_id=event.id,
        last_completed_stage=_stage_from_event(event.type),
        expected_next_stage=expected_stage,
        expected_next_role="",
        blocking_event_id="",
        safe_resume_action="no_action",
        idempotency_key=_idempotency_key(
            task.id,
            event.id,
            "no_action",
            expected_stage,
        ),
        evidence_event_ids=[event.id],
        reason="next action already exists after checkpoint",
        source_event_type=event.type,
    )


def _assignment_correction_checkpoint(
    task: Task,
    event: ZfEvent,
    config: object,
) -> WorkflowResumeCheckpoint | None:
    current = str(task.assigned_to or "").strip()
    if (
        not current
        or not _is_lane_task(task, config)
        or _role_allowed_for_lane_rework(current, config)
    ):
        return None
    return WorkflowResumeCheckpoint(
        task_id=task.id,
        last_trusted_event_id=event.id,
        last_completed_stage=_stage_from_event(event.type),
        expected_next_stage="assignment_correction",
        expected_next_role="",
        blocking_event_id=event.id,
        safe_resume_action="needs_assignment_correction",
        idempotency_key=_idempotency_key(
            task.id,
            event.id,
            "needs_assignment_correction",
            current,
        ),
        evidence_event_ids=[event.id],
        reason=f"current assignment targets non-runnable lane role: {current}",
        source_event_type=event.type,
    )


def _action_already_done(
    *,
    task: Task,
    events: list[ZfEvent],
    all_events: list[ZfEvent] | None = None,
    source_event: ZfEvent,
    action: str,
    target_role: str,
) -> bool:
    source_idx = next(
        (idx for idx, event in enumerate(events) if event.id == source_event.id),
        -1,
    )
    tail = events[source_idx + 1:] if source_idx >= 0 else events
    if action == "needs_stage_dispatch":
        for event in tail:
            if event.type not in {"task.assigned", "task.dispatched"}:
                continue
            payload = _payload(event)
            assignee = str(payload.get("assignee") or payload.get("role") or "")
            if _role_equivalent(assignee, target_role):
                return True
        return _role_equivalent(str(task.assigned_to or ""), target_role)
    if action == "needs_rework_dispatch":
        return any(event.type == "task.rework.requested" for event in tail)
    if action == "needs_terminal_closeout":
        return task.status == "done" or any(
            event.type in {"task.done.evidence", "task.done"}
            for event in tail
        )
    if action == "needs_task_ref_repair":
        return any(
            event.type in {TASK_REF_REPAIR_REQUESTED_EVENT, "task.ref.updated"}
            for event in tail
        )
    if action == "needs_gate_dispatch":
        gate_events = list(all_events or events)
        source_idx = next(
            (idx for idx, event in enumerate(gate_events) if event.id == source_event.id),
            -1,
        )
        tail = gate_events[source_idx + 1:] if source_idx >= 0 else gate_events
        source_ids = _linked_event_ids(source_event)
        source_ids.add(source_event.id)
        for event in tail:
            if _downstream_gate_progress_for_task(event, task.id, source_event):
                return True
            event_ids = _linked_event_ids(event)
            if not event_ids:
                continue
            if event_ids.isdisjoint(source_ids):
                continue
            if event.type in {
                "static_gate.passed",
                "static_gate.failed",
                "static_gate.skipped",
                "fanout.started",
                "fanout.child.completed",
                "fanout.child.failed",
                "fanout.aggregate.completed",
            }:
                return True
        return False
    return False


def _downstream_gate_progress_for_task(
    event: ZfEvent,
    task_id: str,
    source_event: ZfEvent,
) -> bool:
    if not task_id or event.id == source_event.id:
        return False
    payload = _payload(event)
    if event.type == "lane.stage.completed":
        return _event_task_match(event, task_id)
    if event.type in {
        "fanout.aggregate.completed",
        "candidate.ready",
        "test.passed",
        "judge.passed",
    }:
        completed = {
            str(value) for value in payload.get("completed_task_ids") or []
            if str(value).strip()
        }
        return task_id in completed
    return False


def _existing_rework_action_targets_non_lane_role(
    *,
    task: Task,
    events: list[ZfEvent],
    source_event: ZfEvent,
    config: object,
) -> bool:
    if not _is_lane_task(task, config):
        return False
    current = str(task.assigned_to or "").strip()
    if current and not _role_allowed_for_lane_rework(current, config):
        return True
    source_idx = next(
        (idx for idx, event in enumerate(events) if event.id == source_event.id),
        -1,
    )
    tail = events[source_idx + 1:] if source_idx >= 0 else events
    for event in reversed(tail):
        if event.type not in {"task.rework.requested", "task.assigned"}:
            continue
        payload = _payload(event)
        role = str(payload.get("assignee") or payload.get("role") or "").strip()
        if not role:
            continue
        return not _role_allowed_for_lane_rework(role, config)
    return False


def _is_lane_task(task: Task, config: object) -> bool:
    workflow = getattr(config, "workflow", None)
    if not getattr(workflow, "affinity_lanes", None):
        return False
    contract = getattr(task, "contract", None)
    evidence = getattr(contract, "evidence_contract", {}) or {}
    if not isinstance(evidence, dict):
        evidence = {}
    source = str(evidence.get("source") or "").strip()
    affinity_tag = str(evidence.get("affinity_tag") or "").strip()
    owner_role = str(getattr(contract, "owner_role", "") or "").strip()
    return bool(
        affinity_tag
        or source == "refactor_task_map"
        or owner_role.startswith("dev-")
        or owner_role in _GENERIC_IMPL_ROLES
    )


def _role_allowed_for_lane_rework(role: str, config: object) -> bool:
    name = str(role or "").strip()
    if not name or name in _CONTROL_REWORK_ROLES or name in _DESIGN_REWORK_ROLES:
        return False
    role_config = _role_config(config, name)
    if role_config is None:
        return False
    return str(getattr(role_config, "role_kind", "") or "") != "reader"


def _role_config(config: object, name: str):
    for role in list(getattr(config, "roles", []) or []):
        if name in {
            str(getattr(role, "instance_id", "") or ""),
            str(getattr(role, "name", "") or ""),
        }:
            return role
    return None


def _linked_event_ids(event: ZfEvent) -> set[str]:
    payload = _payload(event)
    out = {
        str(value)
        for value in (
            payload.get("trigger_event_id"),
            payload.get("source_event_id"),
            payload.get("result_event_id"),
            event.causation_id,
        )
        if value
    }
    return out


def _safe_action_for_plan(action_type: str) -> str:
    if action_type == "dispatch_role":
        return "needs_stage_dispatch"
    if action_type == "route_rework":
        return "needs_rework_dispatch"
    if action_type == "complete_task":
        return "needs_terminal_closeout"
    if action_type in {"run_gate", "start_fanout", "aggregate_fanout"}:
        return "needs_gate_dispatch"
    return "blocked_external_gate"


def _reader_stage_replan_checkpoints(
    events: list[ZfEvent],
    tasks: list[Task],
    config: object,
) -> list[WorkflowResumeCheckpoint]:
    try:
        from zf.runtime.stage_failure_replan import (
            plan_reader_stage_replan,
            reader_stage_failure_events,
        )
    except Exception:
        return []
    stage_by_failure = reader_stage_failure_events(config)
    if not stage_by_failure:
        return []
    anchor_task_id = _single_active_workflow_anchor_task_id(tasks)
    out: list[WorkflowResumeCheckpoint] = []
    for event in events:
        if event.type not in stage_by_failure:
            continue
        replan_event, note = plan_reader_stage_replan(config, events, event)
        if replan_event is None:
            continue
        stage = stage_by_failure[event.type]
        stage_id = str(getattr(stage, "id", "") or event.type)
        task_id = str(event.task_id or anchor_task_id or "")
        out.append(WorkflowResumeCheckpoint(
            task_id=task_id,
            last_trusted_event_id=event.id,
            last_completed_stage=_stage_from_event(event.type),
            expected_next_stage=f"replan:{stage_id}",
            expected_next_role="",
            blocking_event_id=event.id,
            safe_resume_action="needs_stage_replan",
            idempotency_key=_idempotency_key(
                task_id or "workflow",
                event.id,
                "needs_stage_replan",
                stage_id,
            ),
            evidence_event_ids=[event.id],
            reason=note,
            source_event_type=event.type,
        ))
    return out


def _single_active_workflow_anchor_task_id(tasks: list[Task]) -> str:
    try:
        from zf.runtime.workflow_anchor import is_workflow_fanout_anchor_task
    except Exception:
        return ""
    matches = [
        task.id
        for task in tasks
        if task.status not in {"done", "cancelled"}
        and is_workflow_fanout_anchor_task(task)
    ]
    return matches[0] if len(matches) == 1 else ""


def _action_priority(action_type: str) -> int:
    return {
        "route_rework": 0,
        "complete_task": 1,
        "dispatch_role": 2,
        "run_gate": 3,
        "start_fanout": 4,
        "aggregate_fanout": 5,
    }.get(action_type, 99)


def _stage_from_event(event_type: str) -> str:
    if event_type in {"design.critique.done", "arch.proposal.done"}:
        return "design"
    if event_type in {"dev.build.done", "static_gate.passed", "static_gate.skipped"}:
        return "impl"
    if event_type.startswith("review."):
        return "review"
    if event_type.startswith(("verify.", "test.")):
        return "verify"
    if event_type.startswith("judge."):
        return "judge"
    return event_type.rsplit(".", 1)[0]


def _events_for_task(events: list[ZfEvent], task_id: str) -> list[ZfEvent]:
    return [
        event for event in events
        if _event_task_match(event, task_id)
    ]


def _event_task_match(event: ZfEvent, task_id: str) -> bool:
    if not task_id:
        return False
    if event.task_id:
        return event.task_id == task_id
    return _payload_task_id_match(event.payload, task_id)


def _payload_task_id_match(payload: Any, needle: str) -> bool:
    if not needle:
        return False
    if isinstance(payload, dict):
        for key in (
            "task_id",
            "source_task_id",
            "target_task_id",
            "active_task_id",
        ):
            if str(payload.get(key) or "") == needle:
                return True
        nested = payload.get("task")
        if isinstance(nested, dict) and str(nested.get("id") or "") == needle:
            return True
    return False


def _read_events(state_dir: Path) -> list[ZfEvent]:
    try:
        return EventLog(state_dir / "events.jsonl").read_all()
    except Exception:
        return []


def _read_tasks(state_dir: Path) -> list[Task]:
    try:
        return TaskStore(state_dir / "kanban.json").list_all()
    except Exception:
        return []


def build_stale_worker_diagnostics(state_dir: Path) -> list[dict[str, Any]]:
    """Return stale worker registry rows without treating them as task truth."""
    path = Path(state_dir) / "role_sessions.yaml"
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return [{
            "instance_id": "",
            "reason": "role_sessions_unreadable",
            "detail": str(exc),
        }]
    meta = data.get("instance_meta", {}) if isinstance(data, dict) else {}
    if not isinstance(meta, dict):
        return []
    stale: list[dict[str, Any]] = []
    for instance_id, raw in sorted(meta.items()):
        if not isinstance(raw, dict):
            continue
        reasons = _stale_worker_reasons(raw)
        if reasons:
            stale.append({
                "instance_id": str(instance_id),
                "reasons": reasons,
                "is_task_truth": False,
            })
    return stale


def _stale_worker_reasons(meta: dict[str, Any]) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    for key in ("pid", "process_pid", "runner_pid"):
        value = meta.get(key)
        if value in (None, ""):
            continue
        pid = _parse_pid(value)
        if pid is None:
            reasons.append({
                "code": "pid_invalid",
                "field": key,
                "value": str(value),
            })
        elif not _pid_alive(pid):
            reasons.append({
                "code": "pid_dead",
                "field": key,
                "value": str(pid),
            })
        break
    for key in ("workdir", "workdir_path", "project_path", "cwd"):
        value = str(meta.get(key) or "")
        if not value:
            continue
        if not Path(value).exists():
            reasons.append({
                "code": "workdir_missing",
                "field": key,
                "value": value,
            })
            break
    return reasons


def _parse_pid(value: object) -> int | None:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


_COMPLETION_ANSWER_EVENTS = frozenset({
    "workflow.child.completed",
    "dev.build.done",
    "fanout.child.completed",
})


def _rework_attempt_ready(task_events: list[ZfEvent], task_id: str) -> bool:
    """attempt 账本锚定(131-P2-4):两种情形不许派 rework。

    (a) 最后一次 assignment 之后已有 completion 族应答,且该应答之后
        没有新的 rework 触发族失败——此时 rework 会把有效完成丢弃
        (avbs-r5 SCENE-001 实案:silent_stall 是旧 assignment 的过期
        信号,不是质量失败);
    (b) attempt_ledger 最后一条 attempt 未终结——worker 仍持有 lease,
        重派即双写(false-stuck 家族)。
    """
    from zf.runtime.housekeeping import _REWORK_FAILURE_TYPES

    last_assign_idx = -1
    for idx, event in enumerate(task_events):
        if event.type in {"task.assigned", "task.rework.requested"}:
            last_assign_idx = idx
    if last_assign_idx >= 0:
        completion_idx = -1
        for idx in range(last_assign_idx + 1, len(task_events)):
            if task_events[idx].type in _COMPLETION_ANSWER_EVENTS:
                completion_idx = idx
        if completion_idx >= 0 and not any(
            event.type in _REWORK_FAILURE_TYPES
            for event in task_events[completion_idx + 1:]
        ):
            return False
    try:
        from zf.runtime.attempt_ledger import derive_task_ledger
        ledger = derive_task_ledger(task_events, task_id)
        if ledger.attempts and not ledger.attempts[-1].terminal_type:
            return False
    except Exception:
        pass
    return True


def _payload(event: ZfEvent) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return payload


def _payload_upstream_fanout_id(payload: dict[str, Any]) -> str:
    operator_recovery = payload.get("operator_recovery")
    if isinstance(operator_recovery, dict):
        value = str(operator_recovery.get("upstream_fanout_id") or "").strip()
        if value:
            return value
    return _first_nonempty(payload.get("upstream_fanout_id"), payload.get("fanout_id"))


def _role_equivalent(a: str, b: str) -> bool:
    if a == b:
        return True
    return bool(a and b and (a.startswith(f"{b}-") or b.startswith(f"{a}-")))


def _idempotency_key(
    task_id: str,
    event_id: str,
    action: str,
    target: str,
) -> str:
    raw = "|".join([task_id, event_id, action, target])
    return "wfres-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _count_by_action(checkpoints: list[WorkflowResumeCheckpoint]) -> dict[str, int]:
    out: dict[str, int] = {}
    for checkpoint in checkpoints:
        out[checkpoint.safe_resume_action] = (
            out.get(checkpoint.safe_resume_action, 0) + 1
        )
    return dict(sorted(out.items()))


def _count_batch_by_action(
    checkpoints: list[WorkflowBatchResumeCheckpoint],
) -> dict[str, int]:
    out: dict[str, int] = {}
    for checkpoint in checkpoints:
        out[checkpoint.safe_resume_action] = (
            out.get(checkpoint.safe_resume_action, 0) + 1
        )
    return dict(sorted(out.items()))


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _unique_strings(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _first_nonempty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


from zf.runtime.workflow_resume_apply import (  # noqa: E402
    WorkflowResumeApplyResult,
    apply_workflow_resume,
)


__all__ = [
    "STAGE_TRANSITION_STALLED_EVENT",
    "TASK_REF_REPAIR_REQUESTED_EVENT",
    "WORKFLOW_RESUME_APPLIED_EVENT",
    "WORKFLOW_RESUME_EVENT",
    "WORKFLOW_RESUME_PLANNED_EVENT",
    "WORKFLOW_RESUME_REJECTED_EVENT",
    "WORKFLOW_RESUME_SCHEMA_VERSION",
    "WorkflowResumeApplyResult",
    "WorkflowBatchResumeCheckpoint",
    "WorkflowResumeCheckpoint",
    "apply_workflow_resume",
    "build_workflow_batch_resume_checkpoints",
    "build_workflow_resume_checkpoints",
    "build_workflow_resume_projection",
    "build_stale_worker_diagnostics",
    "write_workflow_resume_projection",
]
