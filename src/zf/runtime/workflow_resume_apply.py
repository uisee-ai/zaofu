"""Workflow resume deterministic apply actions."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.atomic_io import atomic_write_text
from zf.core.task.store import TaskStore
from zf.runtime.workflow_resume import (
    STAGE_TRANSITION_STALLED_EVENT,
    TASK_REF_REPAIR_REQUESTED_EVENT,
    WORKFLOW_RESUME_APPLIED_EVENT,
    WORKFLOW_RESUME_EVENT,
    WORKFLOW_RESUME_PLANNED_EVENT,
    WORKFLOW_RESUME_REJECTED_EVENT,
    WorkflowBatchResumeCheckpoint,
    WorkflowResumeCheckpoint,
    _batch_checkpoint_superseded_reason,
    _payload_has_resume_marker,
    build_workflow_resume_projection,
    write_workflow_resume_projection,
)


@dataclass(frozen=True)
class WorkflowResumeApplyResult:
    checkpoint: Any
    applied: bool
    reason: str
    emitted_event_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        checkpoint = (
            self.checkpoint.to_dict()
            if hasattr(self.checkpoint, "to_dict")
            else dict(self.checkpoint)
            if isinstance(self.checkpoint, dict)
            else {}
        )
        return {
            "checkpoint": checkpoint,
            "applied": self.applied,
            "reason": self.reason,
            "emitted_event_ids": list(self.emitted_event_ids),
        }


def apply_workflow_resume(
    state_dir: Path,
    config: object,
    *,
    event_writer: EventWriter | None = None,
    task_store: TaskStore | None = None,
    project_root: Path | None = None,
    dry_run: bool = False,
    gate_dispatcher=None,
    checkpoint_id: str = "",
    override_task_map_ref: str = "",
) -> dict[str, Any]:
    state_dir = Path(state_dir)
    event_log = (
        event_writer.event_log
        if event_writer is not None else EventLog(state_dir / "events.jsonl")
    )
    writer = event_writer or EventWriter(event_log)
    store = task_store or TaskStore(state_dir / "kanban.json")
    events = event_log.read_all()
    rejections = _resume_context_rejections(
        state_dir,
        config,
        project_root=project_root,
    )
    if rejections:
        emitted_event_ids: list[str] = []
        if not dry_run:
            rejected = writer.append(ZfEvent(
                type=WORKFLOW_RESUME_REJECTED_EVENT,
                actor="zf-cli",
                payload={
                    "source": "workflow_resume",
                    "reason": "resume context rejected",
                    "rejections": rejections,
                    "state_dir": str(state_dir),
                    "project_root": str(project_root) if project_root else "",
                },
            ))
            emitted_event_ids.append(rejected.id)
        return {
            "schema_version": "workflow-resume.apply.v0",
            "projection_path": "",
            "projection": {
                "schema_version": "workflow-resume.rejected.v0",
                "state_dir": str(state_dir),
                "summary": {"tasks": 0, "pending": 0, "by_action": {}},
                "checkpoints": [],
            },
            "applied": 0,
            "rejected": len(rejections),
            "rejections": rejections,
            "results": [],
            "emitted_event_ids": emitted_event_ids,
        }
    projection = build_workflow_resume_projection(
        state_dir,
        config,
        events=events,
        tasks=store.list_all(),
    )
    projection_path = write_workflow_resume_projection(state_dir, projection)
    checkpoints = [
        WorkflowResumeCheckpoint(**item)
        for item in projection.get("checkpoints", [])
        if isinstance(item, dict)
    ]
    batch_checkpoints = [
        WorkflowBatchResumeCheckpoint(**item)
        for item in projection.get("batch_checkpoints", [])
        if isinstance(item, dict)
    ]
    checkpoint_filter = str(checkpoint_id or "").strip()
    if checkpoint_filter:
        checkpoints = [
            checkpoint for checkpoint in checkpoints
            if checkpoint.idempotency_key == checkpoint_filter
        ]
        batch_checkpoints = [
            checkpoint for checkpoint in batch_checkpoints
            if checkpoint.checkpoint_id == checkpoint_filter
        ]
    results: list[WorkflowResumeApplyResult] = []
    for checkpoint in checkpoints:
        if checkpoint.safe_resume_action == "no_action":
            results.append(WorkflowResumeApplyResult(
                checkpoint=checkpoint,
                applied=False,
                reason="no pending resume action",
            ))
            continue
        if (
            _idempotency_seen(events, checkpoint.idempotency_key)
            and _idempotent_resume_effect_seen(store, events, checkpoint)
        ):
            results.append(WorkflowResumeApplyResult(
                checkpoint=checkpoint,
                applied=False,
                reason="resume action already applied",
            ))
            continue
        if dry_run:
            results.append(WorkflowResumeApplyResult(
                checkpoint=checkpoint,
                applied=False,
                reason="dry run",
            ))
            continue
        result = _apply_checkpoint(
            store,
            writer,
            checkpoint,
            config=config,
            state_dir=state_dir,
            gate_dispatcher=gate_dispatcher,
            events=events,
        )
        results.append(result)
        events.extend(
            event for event in writer.event_log.read_all()
            if event.id in set(result.emitted_event_ids)
        )
    batch_results: list[WorkflowResumeApplyResult] = []
    batch_checkpoints, collapsed = _collapse_batch_checkpoints(batch_checkpoints)
    for checkpoint in collapsed:
        batch_results.append(WorkflowResumeApplyResult(
            checkpoint=checkpoint,
            applied=False,
            reason="collapsed into newer checkpoint for same pdd/action",
        ))
    for checkpoint in batch_checkpoints:
        if checkpoint.safe_resume_action == "no_action":
            batch_results.append(WorkflowResumeApplyResult(
                checkpoint=checkpoint,
                applied=False,
                reason="no pending resume action",
            ))
            continue
        if _batch_idempotency_seen(events, checkpoint):
            batch_results.append(WorkflowResumeApplyResult(
                checkpoint=checkpoint,
                applied=False,
                reason="resume action already applied",
            ))
            continue
        if dry_run:
            batch_results.append(WorkflowResumeApplyResult(
                checkpoint=checkpoint,
                applied=False,
                reason="dry run",
            ))
            continue
        result = _apply_batch_checkpoint(
            writer,
            checkpoint,
            state_dir=state_dir,
            gate_dispatcher=gate_dispatcher,
            override_task_map_ref=override_task_map_ref,
            events=events,
        )
        batch_results.append(result)
        events.extend(
            event for event in writer.event_log.read_all()
            if event.id in set(result.emitted_event_ids)
        )
    applied_count = sum(1 for item in results if item.applied) + sum(
        1 for item in batch_results if item.applied
    )
    rejected_count = sum(
        1
        for item in [*results, *batch_results]
        if not item.applied and item.reason.startswith("rejected:")
    )
    pending_count = sum(
        1 for checkpoint in checkpoints
        if checkpoint.safe_resume_action != "no_action"
    )
    batch_pending_count = sum(
        1 for checkpoint in batch_checkpoints
        if checkpoint.safe_resume_action != "no_action"
    )
    return {
        "schema_version": "workflow-resume.apply.v0",
        "projection_path": str(projection_path),
        "projection": projection,
        "checkpoint_id": checkpoint_filter,
        "applied": applied_count,
        "rejected": rejected_count,
        "no_op_reason": (
            "checkpoint not found"
            if checkpoint_filter and not checkpoints and not batch_checkpoints
            else
            "no pending resume action"
            if pending_count == 0 and batch_pending_count == 0
            else ""
        ),
        "results": [item.to_dict() for item in results],
        "batch_results": [item.to_dict() for item in batch_results],
    }


def _apply_checkpoint(
    store: TaskStore,
    writer: EventWriter,
    checkpoint: WorkflowResumeCheckpoint,
    *,
    config=None,
    state_dir: Path | None = None,
    gate_dispatcher=None,
    events=None,
) -> WorkflowResumeApplyResult:
    emitted: list[str] = []

    planned_event = writer.append(_planned_event(checkpoint))
    emitted.append(planned_event.id)

    checkpoint_event = writer.append(ZfEvent(
        type=WORKFLOW_RESUME_EVENT,
        actor="zf-cli",
        task_id=checkpoint.task_id,
        payload=checkpoint.to_dict(),
        causation_id=planned_event.id,
    ))
    emitted.append(checkpoint_event.id)

    if checkpoint.safe_resume_action == "needs_stage_dispatch":
        return _apply_stage_dispatch(store, writer, checkpoint, emitted)

    if checkpoint.safe_resume_action == "needs_task_ref_repair":
        return _apply_task_ref_repair(writer, checkpoint, emitted)

    if checkpoint.safe_resume_action == "needs_assignment_correction":
        return _apply_assignment_correction(
            store,
            writer,
            checkpoint,
            emitted,
            config=config,
            state_dir=state_dir,
        )

    if checkpoint.safe_resume_action == "needs_rework_dispatch":
        return _apply_rework_dispatch(
            store,
            writer,
            checkpoint,
            emitted,
            config=config,
            state_dir=state_dir,
        )

    if checkpoint.safe_resume_action == "needs_terminal_closeout":
        return _apply_terminal_closeout(store, writer, checkpoint, emitted)

    if (
        checkpoint.safe_resume_action == "needs_gate_dispatch"
        and gate_dispatcher is not None
    ):
        # B7 (doc 91 P4 / R25 ISSUE-006): out-of-band 直接执行缺失
        # handoff — 不再只发标记事件回灌瘫痪的主循环 cursor(R25:
        # resume.applied 发了,消费者还是同一条积压路径,恢复无效;
        # 当时靠人工构造 Orchestrator 调 _maybe_start_reader_fanout)。
        # 幂等由孵化侧 _fanout_started(trigger_event_id 判重)保证。
        blocking = None
        for event in events or []:
            if getattr(event, "id", "") == checkpoint.blocking_event_id:
                blocking = event
                break
        if blocking is None:
            _emit_gate_unroutable(writer, checkpoint, emitted,
                                  reason="blocking event missing from log")
            return _apply_stalled(writer, checkpoint, emitted)
        try:
            gate_dispatcher(blocking)
        except Exception as exc:
            return WorkflowResumeApplyResult(
                checkpoint,
                False,
                f"gate dispatch failed: {exc}",
                _append_rejected(
                    writer, checkpoint, emitted,
                    f"gate dispatch failed: {exc}",
                ),
            )
        applied_event = writer.append(ZfEvent(
            type=WORKFLOW_RESUME_APPLIED_EVENT,
            actor="zf-cli",
            task_id=checkpoint.task_id,
            payload={
                **checkpoint.to_dict(),
                "mode": "out_of_band_gate_dispatch",
            },
            causation_id=checkpoint.blocking_event_id,
        ))
        emitted.append(applied_event.id)
        return WorkflowResumeApplyResult(
            checkpoint, True, "out-of-band gate dispatch executed", emitted,
        )

    if checkpoint.safe_resume_action in {
        "blocked_external_gate",
        "needs_gate_dispatch",
    }:
        if checkpoint.safe_resume_action == "needs_gate_dispatch":
            # 131 后续(r5 SCENE-001):completion 被认账、计划 aggregate,
            # 但无 gate dispatcher 可执行 → 以前静默 stalled,监工只能
            # 事后考古"为什么没动"。显式可见。
            _emit_gate_unroutable(writer, checkpoint, emitted,
                                  reason="no gate dispatcher available in this context")
        return _apply_stalled(writer, checkpoint, emitted)

    return WorkflowResumeApplyResult(
        checkpoint,
        False,
        f"rejected: unsupported resume action {checkpoint.safe_resume_action}",
        _append_rejected(
            writer,
            checkpoint,
            emitted,
            f"unsupported resume action {checkpoint.safe_resume_action}",
        ),
    )


def _apply_batch_checkpoint(
    writer: EventWriter,
    checkpoint: WorkflowBatchResumeCheckpoint,
    *,
    state_dir: Path,
    gate_dispatcher=None,
    override_task_map_ref: str = "",
    events: list[ZfEvent] | None = None,
) -> WorkflowResumeApplyResult:
    emitted: list[str] = []
    superseded_reason = _batch_checkpoint_superseded_reason(events or [], checkpoint)
    if superseded_reason:
        return _reject_batch(
            writer,
            checkpoint,
            emitted,
            superseded_reason,
        )
    if (
        override_task_map_ref
        and checkpoint.safe_resume_action in {
            "repair_failed_children",
            "trigger_rework",
        }
        and not Path(override_task_map_ref).is_file()
    ):
        reason = f"override task_map_ref not found: {override_task_map_ref}"
        return WorkflowResumeApplyResult(
            checkpoint,
            False,
            f"rejected: {reason}",
            _append_batch_rejected(writer, checkpoint, emitted, reason),
        )
    planned_event = writer.append(_batch_planned_event(checkpoint))
    emitted.append(planned_event.id)
    checkpoint_event = writer.append(_batch_checkpoint_event(checkpoint, planned_event.id))
    emitted.append(checkpoint_event.id)

    if checkpoint.safe_resume_action == "repair_failed_children":
        task_ids = _task_ids_from_failed_children(
            checkpoint.failed_children,
            state_dir=state_dir,
            fanout_id=checkpoint.fanout_id,
            task_map_ref=override_task_map_ref or checkpoint.task_map_ref,
        )
        resume_scope = "failed_children_only" if task_ids else "all_tasks_rework"
        return _apply_batch_task_map_ready(
            writer,
            checkpoint,
            emitted,
            reason=(
                "failed children rework requested"
                if task_ids
                else "reader fanout failure rework requested"
            ),
            task_ids=task_ids,
            resume_scope=resume_scope,
            state_dir=state_dir,
            gate_dispatcher=gate_dispatcher,
            override_task_map_ref=override_task_map_ref,
        )
    if checkpoint.safe_resume_action == "trigger_rework":
        return _apply_batch_task_map_ready(
            writer,
            checkpoint,
            emitted,
            reason="candidate rework requested",
            task_ids=[],
            resume_scope="all_tasks_rework",
            state_dir=state_dir,
            gate_dispatcher=gate_dispatcher,
            override_task_map_ref=override_task_map_ref,
        )
    if checkpoint.safe_resume_action == "reemit_candidate_ready":
        return _apply_batch_candidate_ready(
            writer,
            checkpoint,
            emitted,
            gate_dispatcher=gate_dispatcher,
        )
    return WorkflowResumeApplyResult(
        checkpoint,
        False,
        f"rejected: unsupported batch resume action {checkpoint.safe_resume_action}",
        _append_batch_rejected(
            writer,
            checkpoint,
            emitted,
            f"unsupported batch resume action {checkpoint.safe_resume_action}",
        ),
    )


def _collapse_batch_checkpoints(
    batch_checkpoints: list[WorkflowBatchResumeCheckpoint],
) -> tuple[list[WorkflowBatchResumeCheckpoint], list[WorkflowBatchResumeCheckpoint]]:
    """avbs-r4 F12: rescan 把账本里每条未处理拒绝各自展开成 checkpoint,
    重启后同 pdd 4 连发 resume batch,全靠 fanout supersede 兜底(浪费
    记账并诱发 cap 污染)。同 (pdd, action) 只保留最后一个——投影按事件
    序构建,最后即最新。
    """
    latest: dict[tuple[str, str], int] = {}
    for idx, checkpoint in enumerate(batch_checkpoints):
        key = (
            str(checkpoint.pdd_id or checkpoint.fanout_id or checkpoint.checkpoint_id),
            str(checkpoint.safe_resume_action),
        )
        latest[key] = idx
    kept_indexes = set(latest.values())
    kept: list[WorkflowBatchResumeCheckpoint] = []
    collapsed: list[WorkflowBatchResumeCheckpoint] = []
    for idx, checkpoint in enumerate(batch_checkpoints):
        (kept if idx in kept_indexes else collapsed).append(checkpoint)
    return kept, collapsed


def _apply_batch_task_map_ready(
    writer: EventWriter,
    checkpoint: WorkflowBatchResumeCheckpoint,
    emitted: list[str],
    *,
    reason: str,
    task_ids: list[str],
    resume_scope: str,
    state_dir: Path,
    gate_dispatcher=None,
    override_task_map_ref: str = "",
) -> WorkflowResumeApplyResult:
    if not checkpoint.pdd_id:
        return _reject_batch(writer, checkpoint, emitted, "missing pdd_id")
    if not checkpoint.task_map_ref:
        return _reject_batch(writer, checkpoint, emitted, "missing task_map_ref")
    if resume_scope == "failed_children_only" and not task_ids:
        return _reject_batch(writer, checkpoint, emitted, "missing failed task_ids")
    source_commit = checkpoint.source_commit or checkpoint.candidate_base_commit
    candidate_base = checkpoint.candidate_base_commit or source_commit
    if not source_commit or not candidate_base:
        # A plan/reader-stage rework (e.g. refactor flow-plan whose gate rejected
        # the synth's plan) has no candidate yet, so candidate_base_commit is
        # empty. Its rework base is the workflow target_ref (e.g. master), not a
        # candidate commit — without this fallback the resume dead-ends with
        # "missing source_commit/candidate_base_commit" and the rework never fires.
        fallback_base = str(checkpoint.target_ref or "").strip()
        if fallback_base:
            source_commit = source_commit or fallback_base
            candidate_base = candidate_base or fallback_base
        else:
            return _reject_batch(
                writer,
                checkpoint,
                emitted,
                "missing source_commit/candidate_base_commit",
            )
    task_map_ref, task_map_repair = _task_map_ref_for_batch_resume(
        state_dir,
        checkpoint,
        override_task_map_ref=override_task_map_ref,
    )
    payload: dict[str, Any] = {
        "pdd_id": checkpoint.pdd_id,
        "feature_id": checkpoint.feature_id or checkpoint.pdd_id,
        "trace_id": checkpoint.trace_id or checkpoint.fanout_id,
        "upstream_fanout_id": checkpoint.upstream_fanout_id or checkpoint.fanout_id,
        "task_map_ref": task_map_ref,
        "source_index_ref": checkpoint.source_index_ref,
        "source_commit": source_commit,
        "candidate_base_commit": candidate_base,
        "target_ref": checkpoint.target_ref,
        "candidate_ref": checkpoint.candidate_ref,
        "candidate_head_commit": checkpoint.candidate_head_commit,
        "rework_of": checkpoint.source_event_id,
        "rework_attempt": 1,
        "rework_source": checkpoint.source_event_type,
        "source": "workflow_resume_batch",
        "resume_scope": resume_scope,
        "resume_checkpoint_ref": checkpoint.checkpoint_id,
        "idempotency_key": checkpoint.checkpoint_id,
        "failed_children": list(checkpoint.failed_children),
        "completed_task_ids": list(checkpoint.completed_task_ids),
        "operator_authorized": bool(checkpoint.escalated),
        "operator_recovery": {
            "upstream_fanout_id": checkpoint.upstream_fanout_id or checkpoint.fanout_id,
            "source": "workflow_resume_batch",
        },
    }
    if task_map_repair:
        payload["task_map_repair"] = task_map_repair
    if task_ids:
        payload["task_ids"] = list(task_ids)
    # avbs-r4 F2: findings 随 rework 走(orchestrator_fanout 已有
    # rework_feedback → child briefing 管线,此前 batch 不填该键 = 盲 rework)
    if getattr(checkpoint, "rework_feedback", None):
        payload["rework_feedback"] = list(checkpoint.rework_feedback)
    event = writer.append(ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        payload=payload,
        causation_id=checkpoint.blocking_event_id or checkpoint.source_event_id,
        correlation_id=checkpoint.trace_id or None,
    ))
    emitted.append(event.id)
    dispatcher_error = _dispatch_resume_event(gate_dispatcher, event)
    if dispatcher_error:
        return WorkflowResumeApplyResult(
            checkpoint,
            False,
            f"rejected: batch dispatcher failed: {dispatcher_error}",
            _append_batch_rejected(
                writer,
                checkpoint,
                emitted,
                f"batch dispatcher failed: {dispatcher_error}",
            ),
        )
    applied = writer.append(_batch_applied_event(checkpoint, reason, event.id))
    emitted.append(applied.id)
    return WorkflowResumeApplyResult(checkpoint, True, reason, emitted)


def _apply_batch_candidate_ready(
    writer: EventWriter,
    checkpoint: WorkflowBatchResumeCheckpoint,
    emitted: list[str],
    *,
    gate_dispatcher=None,
) -> WorkflowResumeApplyResult:
    missing = [
        name for name, value in {
            "fanout_id": checkpoint.fanout_id,
            "pdd_id": checkpoint.pdd_id,
            "candidate_ref": checkpoint.candidate_ref,
            "candidate_base_commit": checkpoint.candidate_base_commit,
            "candidate_head_commit": checkpoint.candidate_head_commit,
            "completed_task_ids": checkpoint.completed_task_ids,
        }.items()
        if not value
    ]
    if missing:
        return _reject_batch(
            writer,
            checkpoint,
            emitted,
            "missing candidate.ready fields: " + ", ".join(missing),
        )
    payload = {
        "fanout_id": checkpoint.fanout_id,
        "upstream_fanout_id": checkpoint.upstream_fanout_id or checkpoint.fanout_id,
        "pdd_id": checkpoint.pdd_id,
        "feature_id": checkpoint.feature_id or checkpoint.pdd_id,
        "trace_id": checkpoint.trace_id or checkpoint.fanout_id,
        "candidate_ref": checkpoint.candidate_ref,
        "candidate_base_commit": checkpoint.candidate_base_commit,
        "candidate_head_commit": checkpoint.candidate_head_commit,
        "diff_ref": checkpoint.diff_ref or (
            f"{checkpoint.candidate_base_commit}.."
            f"{checkpoint.candidate_head_commit}"
        ),
        "completed_task_ids": list(checkpoint.completed_task_ids),
        "task_map_ref": checkpoint.task_map_ref,
        "source_index_ref": checkpoint.source_index_ref,
        "source": "workflow_resume_batch",
        "resume_checkpoint_ref": checkpoint.checkpoint_id,
        "idempotency_key": checkpoint.checkpoint_id,
        "rework_of": checkpoint.source_event_id,
        "rework_source": checkpoint.source_event_type,
        "operator_recovery": {
            "upstream_fanout_id": checkpoint.upstream_fanout_id or checkpoint.fanout_id,
            "source": "workflow_resume_batch",
        },
    }
    event = writer.append(ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        payload=payload,
        causation_id=checkpoint.blocking_event_id or checkpoint.source_event_id,
        correlation_id=checkpoint.trace_id or None,
    ))
    emitted.append(event.id)
    dispatcher_error = _dispatch_resume_event(gate_dispatcher, event)
    if dispatcher_error:
        return WorkflowResumeApplyResult(
            checkpoint,
            False,
            f"rejected: batch dispatcher failed: {dispatcher_error}",
            _append_batch_rejected(
                writer,
                checkpoint,
                emitted,
                f"batch dispatcher failed: {dispatcher_error}",
            ),
        )
    applied = writer.append(_batch_applied_event(
        checkpoint,
        "candidate.ready re-emitted",
        event.id,
    ))
    emitted.append(applied.id)
    return WorkflowResumeApplyResult(
        checkpoint,
        True,
        "candidate.ready re-emitted",
        emitted,
    )


def _apply_stage_dispatch(
    store: TaskStore,
    writer: EventWriter,
    checkpoint: WorkflowResumeCheckpoint,
    emitted: list[str],
) -> WorkflowResumeApplyResult:
    task = store.get(checkpoint.task_id)
    if task is None:
        return WorkflowResumeApplyResult(
            checkpoint,
            False,
            "rejected: task not found",
            _append_rejected(writer, checkpoint, emitted, "task not found"),
        )
    target_status = _target_status(checkpoint.expected_next_role, task.status)
    if target_status != task.status:
        store.update(checkpoint.task_id, status=target_status)
        status_event = writer.append(ZfEvent(
            type="task.status_changed",
            actor="zf-cli",
            task_id=checkpoint.task_id,
            payload={
                "from": task.status,
                "to": target_status,
                "source": "workflow_resume",
                "trigger_event": checkpoint.source_event_type,
                "trigger_event_id": checkpoint.last_trusted_event_id,
                "idempotency_key": checkpoint.idempotency_key,
            },
            causation_id=checkpoint.last_trusted_event_id or None,
        ))
        emitted.append(status_event.id)
    store.update(checkpoint.task_id, assigned_to=checkpoint.expected_next_role)
    assigned = writer.append(ZfEvent(
        type="task.assigned",
        actor="zf-cli",
        task_id=checkpoint.task_id,
        payload={
            "role": checkpoint.expected_next_role,
            "assignee": checkpoint.expected_next_role,
            "source": "workflow_resume",
            "trigger_event": checkpoint.source_event_type,
            "trigger_event_id": checkpoint.last_trusted_event_id,
            "effective_trigger_event": checkpoint.source_event_type,
            "idempotency_key": checkpoint.idempotency_key,
        },
        causation_id=checkpoint.last_trusted_event_id or None,
    ))
    emitted.append(assigned.id)
    dispatched = writer.append(ZfEvent(
        type="task.dispatched",
        actor="zf-cli",
        task_id=checkpoint.task_id,
        payload={
            "role": checkpoint.expected_next_role,
            "assignee": checkpoint.expected_next_role,
            "source": "workflow_resume",
            "trigger_event": checkpoint.source_event_type,
            "trigger_event_id": checkpoint.last_trusted_event_id,
            "effective_trigger_event": checkpoint.source_event_type,
            "idempotency_key": checkpoint.idempotency_key,
        },
        causation_id=assigned.id,
    ))
    emitted.append(dispatched.id)
    applied = writer.append(_applied_event(checkpoint, "task dispatched"))
    emitted.append(applied.id)
    return WorkflowResumeApplyResult(checkpoint, True, "task dispatched", emitted)


def _apply_task_ref_repair(
    writer: EventWriter,
    checkpoint: WorkflowResumeCheckpoint,
    emitted: list[str],
) -> WorkflowResumeApplyResult:
    repair = writer.append(ZfEvent(
        type=TASK_REF_REPAIR_REQUESTED_EVENT,
        actor="zf-cli",
        task_id=checkpoint.task_id,
        payload={
            "task_id": checkpoint.task_id,
            "failure_class": "task_ref_handoff_deadend",
            "source_event_id": checkpoint.last_trusted_event_id,
            "blocking_event_id": checkpoint.blocking_event_id,
            "resume_checkpoint_ref": checkpoint.idempotency_key,
            "idempotency_key": checkpoint.idempotency_key,
            "reason": checkpoint.reason,
        },
        causation_id=checkpoint.last_trusted_event_id or None,
    ))
    emitted.append(repair.id)
    applied = writer.append(_applied_event(checkpoint, "task ref repair requested"))
    emitted.append(applied.id)
    return WorkflowResumeApplyResult(
        checkpoint,
        True,
        "task ref repair requested",
        emitted,
    )


def _apply_assignment_correction(
    store: TaskStore,
    writer: EventWriter,
    checkpoint: WorkflowResumeCheckpoint,
    emitted: list[str],
    *,
    config=None,
    state_dir: Path | None = None,
) -> WorkflowResumeApplyResult:
    task = store.get(checkpoint.task_id)
    target_role, target_reason = _resolve_rework_target_role(
        checkpoint,
        task,
        config=config,
        state_dir=state_dir,
    )
    if not target_role:
        return WorkflowResumeApplyResult(
            checkpoint,
            False,
            f"rejected: {target_reason}",
            _append_rejected(writer, checkpoint, emitted, target_reason),
        )
    if task is not None:
        store.update(
            checkpoint.task_id,
            status="in_progress" if task.status != "done" else task.status,
            assigned_to=target_role,
        )
    assigned = writer.append(ZfEvent(
        type="task.assigned",
        actor="zf-cli",
        task_id=checkpoint.task_id,
        payload={
            "role": target_role,
            "assignee": target_role,
            "source": "workflow_resume_assignment_correction",
            "target_resolution": target_reason,
            "reason": checkpoint.reason,
            "trigger_event": checkpoint.source_event_type,
            "trigger_event_id": checkpoint.last_trusted_event_id,
            "idempotency_key": checkpoint.idempotency_key,
        },
        causation_id=checkpoint.last_trusted_event_id or None,
    ))
    emitted.append(assigned.id)
    applied = writer.append(_applied_event(checkpoint, "assignment corrected"))
    emitted.append(applied.id)
    return WorkflowResumeApplyResult(
        checkpoint,
        True,
        "assignment corrected",
        emitted,
    )


def _apply_rework_dispatch(
    store: TaskStore,
    writer: EventWriter,
    checkpoint: WorkflowResumeCheckpoint,
    emitted: list[str],
    *,
    config=None,
    state_dir: Path | None = None,
) -> WorkflowResumeApplyResult:
    task = store.get(checkpoint.task_id)
    target_role, target_reason = _resolve_rework_target_role(
        checkpoint,
        task,
        config=config,
        state_dir=state_dir,
    )
    if not target_role:
        return WorkflowResumeApplyResult(
            checkpoint,
            False,
            f"rejected: {target_reason}",
            _append_rejected(writer, checkpoint, emitted, target_reason),
        )
    if task is not None:
        store.update(
            checkpoint.task_id,
            status="in_progress",
            assigned_to=target_role,
        )
    rework = writer.append(ZfEvent(
        type="task.rework.requested",
        actor="zf-cli",
        task_id=checkpoint.task_id,
        payload={
            "task_id": checkpoint.task_id,
            "role": target_role,
            "assignee": target_role,
            "source": "workflow_resume",
            "target_resolution": target_reason,
            "reason": checkpoint.reason or "workflow resume rework",
            "trigger_event_type": checkpoint.source_event_type,
            "trigger_event_id": checkpoint.last_trusted_event_id,
            "expected_next_stage": checkpoint.expected_next_stage,
            "idempotency_key": checkpoint.idempotency_key,
        },
        causation_id=checkpoint.last_trusted_event_id or None,
    ))
    emitted.append(rework.id)
    assigned = writer.append(ZfEvent(
        type="task.assigned",
        actor="zf-cli",
        task_id=checkpoint.task_id,
        payload={
            "role": target_role,
            "assignee": target_role,
            "source": "workflow_resume_rework",
            "target_resolution": target_reason,
            "trigger_event": checkpoint.source_event_type,
            "trigger_event_id": checkpoint.last_trusted_event_id,
            "rework_request_event_id": rework.id,
            "idempotency_key": checkpoint.idempotency_key,
        },
        causation_id=rework.id,
    ))
    emitted.append(assigned.id)
    applied = writer.append(_applied_event(checkpoint, "rework requested"))
    emitted.append(applied.id)
    return WorkflowResumeApplyResult(checkpoint, True, "rework requested", emitted)


_CONTROL_REWORK_ROLES = {"", "orchestrator"}
_DESIGN_REWORK_ROLES = {"arch", "critic"}
_GENERIC_IMPL_ROLES = {"dev", "impl", "writer", "coding", "coding-agent"}


def _resolve_rework_target_role(
    checkpoint: WorkflowResumeCheckpoint,
    task,
    *,
    config=None,
    state_dir: Path | None = None,
) -> tuple[str, str]:
    requested = str(checkpoint.expected_next_role or "").strip()
    if not requested:
        requested = "dev"
    lane_task = _is_lane_task(task, config=config)
    if _role_allowed_for_resume_rework(requested, config=config, lane_task=lane_task):
        return requested, "checkpoint.expected_next_role"

    if lane_task or requested in _CONTROL_REWORK_ROLES:
        lane_role = _lane_impl_role_for_task(
            task,
            config=config,
            state_dir=state_dir,
        )
        if lane_role and _role_allowed_for_resume_rework(
            lane_role,
            config=config,
            lane_task=True,
        ):
            return lane_role, "lane_affinity.impl"

    contract = getattr(task, "contract", None)
    for source, candidate in (
        ("task.contract.owner_instance", getattr(contract, "owner_instance", "")),
        ("task.contract.owner_role", getattr(contract, "owner_role", "")),
        ("task.contract.rework_to", getattr(contract, "rework_to", "")),
    ):
        role = str(candidate or "").strip()
        if _role_allowed_for_resume_rework(role, config=config, lane_task=lane_task):
            return role, source

    if requested == "dev" and _role_allowed_for_resume_rework(
        "dev",
        config=config,
        lane_task=False,
    ):
        return "dev", "legacy.default_dev"

    return "", (
        "workflow resume rework target is not a runnable implementation role: "
        f"{requested or '<empty>'}"
    )


def _role_allowed_for_resume_rework(
    role: str,
    *,
    config=None,
    lane_task: bool,
) -> bool:
    name = str(role or "").strip()
    if not name or name in _CONTROL_REWORK_ROLES:
        return False
    if lane_task and name in _DESIGN_REWORK_ROLES:
        return False
    role_config = _role_config(config, name)
    if role_config is None:
        return config is None and name not in _DESIGN_REWORK_ROLES
    if lane_task and str(getattr(role_config, "role_kind", "") or "") == "reader":
        return False
    return True


def _role_config(config, name: str):
    for role in list(getattr(config, "roles", []) or []):
        if name in {
            str(getattr(role, "instance_id", "") or ""),
            str(getattr(role, "name", "") or ""),
        }:
            return role
    return None


def _is_lane_task(task, *, config=None) -> bool:
    if task is None:
        return False
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


def _lane_impl_role_for_task(
    task,
    *,
    config=None,
    state_dir: Path | None = None,
) -> str:
    if task is None or config is None:
        return ""
    contract = getattr(task, "contract", None)
    evidence = getattr(contract, "evidence_contract", {}) or {}
    if not isinstance(evidence, dict):
        evidence = {}
    task_map_ref = _evidence_task_map_ref(evidence)
    task_map = _read_task_map(task_map_ref, state_dir=state_dir)
    affinity_key = str(task_map.get("affinity_key") or "affinity_tag").strip()
    affinity_tag = str(evidence.get(affinity_key) or evidence.get("affinity_tag") or "").strip()
    task_item = _task_map_item(task_map, str(getattr(task, "id", "") or ""))
    if not affinity_tag and task_item:
        affinity_tag = str(task_item.get(affinity_key) or task_item.get("affinity_tag") or "").strip()
    lane_id = ""
    if task_item:
        lane_id = str(task_item.get("lane_id") or task_item.get("lane") or "").strip()
    lane_map = task_map.get("lane_affinity_map")
    if not lane_id and isinstance(lane_map, dict) and affinity_tag:
        lane_id = str(lane_map.get(affinity_tag) or "").strip()
    if not lane_id and affinity_tag.startswith("lane"):
        lane_id = affinity_tag
    profile_name = str(task_map.get("lane_profile") or "").strip()
    profiles = getattr(getattr(config, "workflow", None), "affinity_lanes", {}) or {}
    profile = profiles.get(profile_name) if profile_name else None
    if profile is None and len(profiles) == 1:
        profile = next(iter(profiles.values()))
    if profile is None:
        return ""
    for lane in list(getattr(profile, "lanes", []) or []):
        if str(getattr(lane, "id", "") or "") == lane_id:
            return str(getattr(lane, "impl", "") or "").strip()
    dispatch_role = _impl_role_from_active_dispatch(task, profile)
    if dispatch_role:
        return dispatch_role
    return ""


def _impl_role_from_active_dispatch(task, profile) -> str:
    dispatch_id = str(getattr(task, "active_dispatch_id", "") or "").strip()
    if not dispatch_id:
        return ""
    for lane in list(getattr(profile, "lanes", []) or []):
        impl_role = str(getattr(lane, "impl", "") or "").strip()
        if impl_role and impl_role in dispatch_id:
            return impl_role
    return ""


def _evidence_task_map_ref(evidence: dict) -> str:
    refs = evidence.get("source_refs")
    if isinstance(refs, dict):
        return str(refs.get("task_map_ref") or "").strip()
    return ""


def _read_task_map(ref: str, *, state_dir: Path | None = None) -> dict:
    text = str(ref or "").strip()
    if not text:
        return {}
    path = Path(text)
    if not path.is_absolute() and state_dir is not None:
        path = Path(state_dir) / path
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _task_map_item(task_map: dict, task_id: str) -> dict:
    tasks = task_map.get("tasks")
    if not isinstance(tasks, list):
        return {}
    for item in tasks:
        if not isinstance(item, dict):
            continue
        if str(item.get("task_id") or item.get("id") or "") == task_id:
            return item
    return {}


def _apply_terminal_closeout(
    store: TaskStore,
    writer: EventWriter,
    checkpoint: WorkflowResumeCheckpoint,
    emitted: list[str],
) -> WorkflowResumeApplyResult:
    task = store.get(checkpoint.task_id)
    if task is None:
        return WorkflowResumeApplyResult(
            checkpoint,
            False,
            "rejected: task not found",
            _append_rejected(writer, checkpoint, emitted, "task not found"),
        )

    previous_status = task.status
    if task.status != "done":
        updated = store.update(checkpoint.task_id, status="done")
        if updated is None:
            return WorkflowResumeApplyResult(
                checkpoint,
                False,
                "rejected: task not found",
                _append_rejected(writer, checkpoint, emitted, "task not found"),
            )
        status_changed = writer.append(ZfEvent(
            type="task.status_changed",
            actor="zf-cli",
            task_id=checkpoint.task_id,
            payload={
                "from": previous_status,
                "to": "done",
                "source": "workflow_resume",
                "trigger_event": checkpoint.source_event_type,
                "trigger_event_id": checkpoint.last_trusted_event_id,
                "idempotency_key": checkpoint.idempotency_key,
            },
            causation_id=checkpoint.last_trusted_event_id or None,
        ))
        emitted.append(status_changed.id)

    done_evidence = writer.append(ZfEvent(
        type="task.done.evidence",
        actor="zf-cli",
        task_id=checkpoint.task_id,
        payload={
            "task_id": checkpoint.task_id,
            "source": "workflow_resume",
            "trigger_event": checkpoint.source_event_type,
            "trigger_event_id": checkpoint.last_trusted_event_id,
            "source_event_id": checkpoint.last_trusted_event_id,
            "safe_resume_action": checkpoint.safe_resume_action,
            "idempotency_key": checkpoint.idempotency_key,
            "evidence_event_ids": list(checkpoint.evidence_event_ids),
            "reason": checkpoint.reason,
        },
        causation_id=checkpoint.last_trusted_event_id or None,
    ))
    emitted.append(done_evidence.id)
    applied = writer.append(_applied_event(checkpoint, "task terminal closeout"))
    emitted.append(applied.id)
    return WorkflowResumeApplyResult(
        checkpoint,
        True,
        "task terminal closeout",
        emitted,
    )


def _emit_gate_unroutable(
    writer: EventWriter,
    checkpoint: WorkflowResumeCheckpoint,
    emitted: list[str],
    *,
    reason: str,
) -> None:
    try:
        event = writer.append(ZfEvent(
            type="workflow.resume.gate_unroutable",
            actor="zf-cli",
            task_id=checkpoint.task_id,
            payload={
                "task_id": checkpoint.task_id,
                "expected_next_stage": checkpoint.expected_next_stage,
                "expected_next_role": checkpoint.expected_next_role,
                "checkpoint_idempotency_key": checkpoint.idempotency_key,
                "reason": reason,
            },
            causation_id=checkpoint.last_trusted_event_id or None,
        ))
        emitted.append(event.id)
    except Exception:
        pass


def _apply_stalled(
    writer: EventWriter,
    checkpoint: WorkflowResumeCheckpoint,
    emitted: list[str],
) -> WorkflowResumeApplyResult:
    stalled = writer.append(ZfEvent(
        type=STAGE_TRANSITION_STALLED_EVENT,
        actor="zf-cli",
        task_id=checkpoint.task_id,
        payload={
            "task_id": checkpoint.task_id,
            "source_event_id": checkpoint.last_trusted_event_id,
            "expected_next_stage": checkpoint.expected_next_stage,
            "expected_role_family": checkpoint.expected_next_role,
            "safe_resume_action": checkpoint.safe_resume_action,
            "idempotency_key": checkpoint.idempotency_key,
            "reason": checkpoint.reason,
        },
        causation_id=checkpoint.last_trusted_event_id or None,
    ))
    emitted.append(stalled.id)
    applied = writer.append(_applied_event(checkpoint, "stage transition stalled"))
    emitted.append(applied.id)
    return WorkflowResumeApplyResult(
        checkpoint,
        True,
        "stage transition stalled",
        emitted,
    )


def _batch_planned_event(checkpoint: WorkflowBatchResumeCheckpoint) -> ZfEvent:
    return ZfEvent(
        type=WORKFLOW_RESUME_PLANNED_EVENT,
        actor="zf-cli",
        payload={
            **checkpoint.to_dict(),
            "checkpoint_kind": "batch",
            "idempotency_key": checkpoint.checkpoint_id,
        },
        causation_id=checkpoint.blocking_event_id or checkpoint.source_event_id or None,
    )


def _batch_checkpoint_event(
    checkpoint: WorkflowBatchResumeCheckpoint,
    planned_event_id: str,
) -> ZfEvent:
    return ZfEvent(
        type=WORKFLOW_RESUME_EVENT,
        actor="zf-cli",
        payload={
            **checkpoint.to_dict(),
            "checkpoint_kind": "batch",
            "idempotency_key": checkpoint.checkpoint_id,
        },
        causation_id=planned_event_id,
    )


def _batch_applied_event(
    checkpoint: WorkflowBatchResumeCheckpoint,
    reason: str,
    emitted_event_id: str,
) -> ZfEvent:
    return ZfEvent(
        type=WORKFLOW_RESUME_APPLIED_EVENT,
        actor="zf-cli",
        payload={
            **checkpoint.to_dict(),
            "checkpoint_kind": "batch",
            "idempotency_key": checkpoint.checkpoint_id,
            "emitted_event_id": emitted_event_id,
            "reason": reason,
        },
        causation_id=checkpoint.blocking_event_id or checkpoint.source_event_id or None,
    )


def _applied_event(checkpoint: WorkflowResumeCheckpoint, reason: str) -> ZfEvent:
    return ZfEvent(
        type=WORKFLOW_RESUME_APPLIED_EVENT,
        actor="zf-cli",
        task_id=checkpoint.task_id,
        payload={
            "task_id": checkpoint.task_id,
            "safe_resume_action": checkpoint.safe_resume_action,
            "idempotency_key": checkpoint.idempotency_key,
            "source_event_id": checkpoint.last_trusted_event_id,
            "reason": reason,
        },
        causation_id=checkpoint.last_trusted_event_id or None,
    )


def _planned_event(checkpoint: WorkflowResumeCheckpoint) -> ZfEvent:
    return ZfEvent(
        type=WORKFLOW_RESUME_PLANNED_EVENT,
        actor="zf-cli",
        task_id=checkpoint.task_id,
        payload={
            "task_id": checkpoint.task_id,
            "safe_resume_action": checkpoint.safe_resume_action,
            "expected_next_stage": checkpoint.expected_next_stage,
            "expected_next_role": checkpoint.expected_next_role,
            "idempotency_key": checkpoint.idempotency_key,
            "source_event_id": checkpoint.last_trusted_event_id,
            "blocking_event_id": checkpoint.blocking_event_id,
            "reason": checkpoint.reason,
        },
        causation_id=checkpoint.last_trusted_event_id or None,
    )


def _append_rejected(
    writer: EventWriter,
    checkpoint: WorkflowResumeCheckpoint,
    emitted: list[str],
    reason: str,
) -> list[str]:
    rejected = writer.append(ZfEvent(
        type=WORKFLOW_RESUME_REJECTED_EVENT,
        actor="zf-cli",
        task_id=checkpoint.task_id,
        payload={
            "task_id": checkpoint.task_id,
            "safe_resume_action": checkpoint.safe_resume_action,
            "idempotency_key": checkpoint.idempotency_key,
            "source_event_id": checkpoint.last_trusted_event_id,
            "reason": reason,
        },
        causation_id=checkpoint.last_trusted_event_id or None,
    ))
    emitted.append(rejected.id)
    return emitted


def _append_batch_rejected(
    writer: EventWriter,
    checkpoint: WorkflowBatchResumeCheckpoint,
    emitted: list[str],
    reason: str,
) -> list[str]:
    rejected = writer.append(ZfEvent(
        type=WORKFLOW_RESUME_REJECTED_EVENT,
        actor="zf-cli",
        payload={
            **checkpoint.to_dict(),
            "checkpoint_kind": "batch",
            "idempotency_key": checkpoint.checkpoint_id,
            "reason": reason,
        },
        causation_id=checkpoint.blocking_event_id or checkpoint.source_event_id or None,
    ))
    emitted.append(rejected.id)
    return emitted


def _task_map_ref_for_batch_resume(
    state_dir: Path,
    checkpoint: WorkflowBatchResumeCheckpoint,
    *,
    override_task_map_ref: str = "",
) -> tuple[str, dict[str, Any]]:
    override = str(override_task_map_ref or "").strip()
    if override:
        override_path = Path(override)
        if not override_path.is_file():
            raise FileNotFoundError(
                f"override task_map_ref does not exist: {override}"
            )
        return str(override_path), {
            "kind": "operator_task_map_override",
            "original_task_map_ref": checkpoint.task_map_ref,
            "repaired_task_map_ref": str(override_path),
        }
    original_ref = checkpoint.task_map_ref
    path = Path(original_ref)
    if not path.is_file():
        return original_ref, {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return original_ref, {}
    if not isinstance(payload, dict):
        return original_ref, {}
    changed = _quote_task_map_filter_globs(payload)
    if not changed:
        return original_ref, {}
    out_path = (
        Path(state_dir)
        / "artifacts"
        / "workflow-resume"
        / checkpoint.checkpoint_id
        / "task_map.json"
    )
    atomic_write_text(
        out_path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return str(out_path), {
        "kind": "quote_unquoted_shell_glob_filters",
        "original_task_map_ref": original_ref,
        "repaired_task_map_ref": str(out_path),
    }


_UNQUOTED_FILTER_GLOB_RE = re.compile(
    r"(?P<sep>^|\s)(?P<option>--filter\s+)"
    r"(?P<value>(?!['\"])[^\s;|&]+[*?][^\s;|&]*)"
)


def _quote_task_map_filter_globs(payload: dict[str, Any]) -> bool:
    changed = False
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        return False
    for task in tasks:
        if not isinstance(task, dict):
            continue
        command = task.get("verification")
        if not isinstance(command, str):
            continue
        fixed = _UNQUOTED_FILTER_GLOB_RE.sub(
            lambda match: (
                f"{match.group('sep')}{match.group('option')}"
                f"'{match.group('value')}'"
            ),
            command,
        )
        if fixed == command:
            continue
        task["verification"] = fixed
        changed = True
    return changed


def _reject_batch(
    writer: EventWriter,
    checkpoint: WorkflowBatchResumeCheckpoint,
    emitted: list[str],
    reason: str,
) -> WorkflowResumeApplyResult:
    return WorkflowResumeApplyResult(
        checkpoint,
        False,
        f"rejected: {reason}",
        _append_batch_rejected(writer, checkpoint, emitted, reason),
    )


def _batch_idempotency_seen(
    events: list[ZfEvent],
    checkpoint: WorkflowBatchResumeCheckpoint,
) -> bool:
    seen = False
    invalidated = False
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            _payload_has_resume_marker(payload, checkpoint.checkpoint_id)
            and event.type in {
            WORKFLOW_RESUME_APPLIED_EVENT,
            WORKFLOW_RESUME_REJECTED_EVENT,
            "task_map.ready",
            "candidate.ready",
            "orchestrator.replan_requested",
            }
        ):
            seen = True
            continue
        if not seen or event.type != "fanout.cancelled":
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
    return seen and not invalidated


def _idempotency_seen(events: list[ZfEvent], key: str) -> bool:
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("idempotency_key") or "") == key:
            if event.type in {
                WORKFLOW_RESUME_APPLIED_EVENT,
                WORKFLOW_RESUME_REJECTED_EVENT,
                "task.assigned",
                "task.rework.requested",
                "task_map.ready",
                "candidate.ready",
                "orchestrator.replan_requested",
                TASK_REF_REPAIR_REQUESTED_EVENT,
                STAGE_TRANSITION_STALLED_EVENT,
            }:
                return True
    return False


def _idempotent_resume_effect_seen(
    store: TaskStore,
    events: list[ZfEvent],
    checkpoint: WorkflowResumeCheckpoint,
) -> bool:
    """Return whether an existing idempotency marker has the intended effect.

    Older ``needs_terminal_closeout`` recovery emitted only
    ``stage.transition.stalled`` plus ``workflow.resume.applied``. That marker
    must not permanently block a later deterministic closeout when the task is
    still active.
    """

    if checkpoint.safe_resume_action != "needs_terminal_closeout":
        return True
    task = store.get(checkpoint.task_id)
    if task is not None and task.status == "done":
        return True
    for event in events:
        if event.task_id != checkpoint.task_id:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("idempotency_key") or "") != checkpoint.idempotency_key:
            continue
        if event.type in {"task.done", "task.done.evidence"}:
            return True
        if (
            event.type == "task.status_changed"
            and str(payload.get("to") or payload.get("status") or "") == "done"
        ):
            return True
    return False


_QUEUED_CHILD_TASK_ID_RE = re.compile(
    r"^queued-(?P<task_id>[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d+)-\d+$"
)
_TASK_ID_SUFFIX_RE = re.compile(
    r"(?P<task_id>[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d+)$"
)


def _task_id_from_failed_child(raw: str) -> str:
    text = str(raw or "").strip()
    queued_match = _QUEUED_CHILD_TASK_ID_RE.match(text)
    if queued_match:
        return queued_match.group("task_id")
    suffix_match = _TASK_ID_SUFFIX_RE.search(text)
    if suffix_match:
        return suffix_match.group("task_id")
    return ""


def _task_ids_from_failed_children(
    failed_children: list[str],
    *,
    state_dir: Path | None = None,
    fanout_id: str = "",
    task_map_ref: str = "",
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    fanout_child_tasks = _task_ids_by_fanout_child(
        state_dir=state_dir,
        fanout_id=fanout_id,
    )
    task_map_tasks = _task_map_tasks_by_child_hint(task_map_ref)
    for raw in failed_children:
        text = str(raw or "").strip()
        if not text or text.startswith("candidate:"):
            continue
        task_id = (
            _task_id_from_failed_child(text)
            or fanout_child_tasks.get(text, "")
            or _task_id_from_task_map_hint(text, task_map_tasks)
        )
        if task_id and task_id not in seen:
            seen.add(task_id)
            out.append(task_id)
    return out


def _task_ids_by_fanout_child(
    *,
    state_dir: Path | None,
    fanout_id: str,
) -> dict[str, str]:
    if state_dir is None or not fanout_id:
        return {}
    manifest_path = Path(state_dir) / "fanouts" / fanout_id / "manifest.json"
    if not manifest_path.is_file():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    children = manifest.get("children")
    if not isinstance(children, list):
        return {}
    out: dict[str, str] = {}
    for child in children:
        if not isinstance(child, dict):
            continue
        child_id = str(child.get("child_id") or "").strip()
        if not child_id:
            continue
        payload = child.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        task_id = _first_task_id(
            child.get("task_id"),
            payload.get("task_id"),
            payload.get("upstream_task_id"),
        )
        if task_id:
            out[child_id] = task_id
    return out


def _task_map_tasks_by_child_hint(task_map_ref: str) -> list[dict[str, str]]:
    ref = str(task_map_ref or "").strip()
    if not ref:
        return []
    path = Path(ref)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    tasks = payload.get("tasks") if isinstance(payload, dict) else None
    if not isinstance(tasks, list):
        return []
    out: list[dict[str, str]] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = _first_task_id(task.get("task_id"), task.get("id"))
        if not task_id:
            continue
        hints = {
            "task_id": task_id,
            "affinity_tag": str(task.get("affinity_tag") or "").strip(),
            "child_id": str(task.get("child_id") or "").strip(),
            "upstream_child_id": str(task.get("upstream_child_id") or "").strip(),
        }
        out.append(hints)
    return out


def _task_id_from_task_map_hint(
    failed_child: str,
    tasks: list[dict[str, str]],
) -> str:
    normalized_child = _normalize_child_hint(failed_child)
    if not normalized_child:
        return ""
    for task in tasks:
        task_id = task.get("task_id", "")
        for key in ("task_id", "child_id", "upstream_child_id"):
            hint = _normalize_child_hint(task.get(key, ""))
            if hint and hint == normalized_child:
                return task_id
        affinity = _normalize_child_hint(task.get("affinity_tag", ""))
        if affinity and (
            normalized_child == affinity
            or normalized_child.endswith(f"-{affinity}")
            or f"-{affinity}-" in normalized_child
        ):
            return task_id
    return ""


def _normalize_child_hint(value: object) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _first_task_id(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if _TASK_ID_SUFFIX_RE.fullmatch(text):
            return text
    return ""


def _dispatch_resume_event(gate_dispatcher, event: ZfEvent) -> str:
    if gate_dispatcher is None:
        return ""
    try:
        gate_dispatcher(event)
    except Exception as exc:
        return str(exc)
    return ""


def _target_status(role: str, current: str) -> str:
    normalized = role.strip().lower()
    if normalized.startswith(("review", "critic", "code_review")):
        return "review"
    if normalized.startswith(
        ("verify", "verifier", "test", "tester", "qa", "judge")
    ):
        return "testing"
    if normalized.startswith(("dev", "developer", "builder", "writer", "arch")):
        return "in_progress"
    return current


def _resume_context_rejections(
    state_dir: Path,
    config: object,
    *,
    project_root: Path | None,
) -> list[dict[str, str]]:
    if project_root is None:
        return []
    root = Path(project_root).resolve()
    actual_state_dir = Path(state_dir).resolve()
    rejections: list[dict[str, str]] = []
    session_path = actual_state_dir / "session.yaml"
    session_root: Path | None = None
    if session_path.exists():
        try:
            from zf.core.state.session import SessionStore

            session = SessionStore(session_path).load()
            session_root = (
                Path(session.project_root).resolve()
                if session.project_root else None
            )
        except Exception as exc:
            rejections.append({
                "code": "session_unreadable",
                "reason": str(exc),
                "session_path": str(session_path),
            })

    configured_state_dir = str(
        getattr(getattr(config, "project", None), "state_dir", "") or ""
    )
    if configured_state_dir:
        expected = Path(configured_state_dir)
        if not expected.is_absolute():
            expected = root / expected
        expected = expected.resolve()
        runtime_override_allowed = (
            _state_dir_matches_env_override(actual_state_dir, root)
            or (session_root is not None and session_root == root)
        )
        if expected != actual_state_dir and not runtime_override_allowed:
            rejections.append({
                "code": "state_dir_mismatch",
                "reason": "state dir does not match zf.yaml project.state_dir",
                "expected_state_dir": str(expected),
                "actual_state_dir": str(actual_state_dir),
            })

    if session_root is not None and session_root != root:
        rejections.append({
            "code": "session_project_root_mismatch",
            "reason": "session.yaml project_root does not match current project root",
            "expected_project_root": str(root),
            "actual_project_root": str(session_root),
            "session_path": str(session_path),
        })

    return rejections


def _state_dir_matches_env_override(actual_state_dir: Path, project_root: Path) -> bool:
    raw = os.environ.get("ZF_STATE_DIR", "").strip()
    if not raw:
        return False
    env_state_dir = Path(raw).expanduser()
    if not env_state_dir.is_absolute():
        env_state_dir = project_root / env_state_dir
    try:
        return env_state_dir.resolve() == actual_state_dir
    except Exception:
        return False


__all__ = [
    "WorkflowResumeApplyResult",
    "apply_workflow_resume",
]
