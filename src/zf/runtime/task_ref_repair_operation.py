"""Durable writer-call rotation for task-ref repair dispatches.

Task-ref repair keeps the logical fanout child and source baseline, but a
blocking call whose contract changed must not replay the old immutable
operation request.  This module prepares the replacement operation before the
provider receives bytes and publishes the child rebind only after delivery.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.core.state.task_attempts import task_attempt_id
from zf.core.task.schema import Task
from zf.runtime.call_result_admission import result_protocol_mode
from zf.runtime.call_result_runtime import (
    PreparedCallOperation,
    mark_call_operation_started,
    prepare_call_operation,
    workflow_operation_service,
)
from zf.runtime.fanout import FanoutChild, FanoutContext
from zf.runtime.fanout_result_identity import (
    KERNEL_BOUND_WRITER_RESULT_FIELDS,
)
from zf.runtime.task_attempt_runtime import dispatch_attempt_payload
from zf.runtime.transport import DispatchContext
from zf.runtime.rework_dispatch_context import _task_ref_scope_repair_payload
from zf.runtime.workflow_operation import (
    TERMINAL_OPERATION_STATUSES,
    load_workflow_operation,
)
from zf.runtime.writer_fanout_data import _FANOUT_AFFINITY_METADATA_KEYS


_STALE_CALL_INPUT_FIELDS = frozenset({
    "admitted_call_result_digest",
    "admitted_call_result_ref",
    "attempt_id",
    "attempt_source_manifest",
    "attempt_source_manifest_digest",
    "attempt_source_manifest_ref",
    "contract_snapshot_digest",
    "contract_snapshot_ref",
    "control_result_ref",
    "impl_self_check",
    "impl_self_check_digest",
    "impl_self_check_ref",
    "input_consumption_policy",
    "input_consumption_policy_digest",
    "input_consumption_policy_ref",
    "operation_id",
    "request_hash",
    "required_reads",
    "result_scratch_ref",
    "semantic_result_profile",
    "source_commit",
    "target_commit",
    "target_snapshot_digest",
    "target_snapshot_ref",
})

TASK_REF_REPAIR_REQUESTED_EVENT = "task.ref.repair.requested"


@dataclass(frozen=True)
class TaskRefRepairOperation:
    prepared: PreparedCallOperation
    operation_payload: dict[str, Any]
    rebind_payload: dict[str, Any]
    old_operation_id: str
    old_request_hash: str

    def bind_dispatch_context(self, context: DispatchContext) -> DispatchContext:
        return replace(context, operation_id=self.prepared.operation_id)

    def briefing_section(self) -> str:
        identity = {
            key: self.operation_payload.get(key)
            for key in (
                "workflow_run_id",
                "fanout_id",
                "stage_id",
                "child_id",
                "run_id",
                "operation_id",
                "request_hash",
                "attempt_id",
                "contract_revision",
                "task_map_generation",
                "contract_snapshot_ref",
                "contract_snapshot_digest",
                "base_commit",
            )
            if self.operation_payload.get(key) not in (None, "")
        }
        return (
            "\n## Runtime Durable Repair Identity\n"
            "The Kernel created a new immutable call operation for this repair. "
            "Use this identity instead of the rejected completion's old "
            "run/operation/request/attempt fields.\n"
            "```json\n"
            + json.dumps(identity, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n```\n"
        )


@dataclass(frozen=True)
class TaskRefRepairDispatch:
    context: DispatchContext
    operation: TaskRefRepairOperation | None
    proceed: bool
    causation_id: str = ""
    correlation_id: str = ""

    def publish(
        self,
        runtime: Any,
        *,
        delivered_context: DispatchContext,
        briefing_path: Path,
    ) -> ZfEvent | None:
        if self.operation is None:
            return None
        return publish_task_ref_repair_operation_dispatch(
            runtime,
            repair=self.operation,
            context=delivered_context,
            task_id=str(self.operation.operation_payload.get("task_id") or ""),
            briefing_path=briefing_path,
            causation_id=self.causation_id,
            correlation_id=(
                self.correlation_id
                or str(delivered_context.run_id or "")
            ),
        )


def task_ref_repair_dispatch_id(task_id: str, trigger_event: ZfEvent) -> str:
    """Return a replay-stable scheduler dispatch id for one repair event."""

    digest = hashlib.sha256(
        f"task-ref-repair|{task_id}|{trigger_event.id}".encode("utf-8")
    ).hexdigest()[:12]
    return f"disp-{digest}"


def prepare_task_ref_repair_dispatch(
    runtime: Any,
    *,
    task: Task,
    role: RoleConfig,
    trigger_event: ZfEvent,
    dispatch_context: DispatchContext,
    rework_request: ZfEvent,
) -> TaskRefRepairDispatch:
    """Prepare repair call identity and convert preparation failures to events."""

    if trigger_event.type != TASK_REF_REPAIR_REQUESTED_EVENT:
        return TaskRefRepairDispatch(dispatch_context, None, True)
    try:
        operation = prepare_task_ref_repair_operation(
            runtime,
            task=task,
            role=role,
            trigger_event=trigger_event,
            dispatch_context=dispatch_context,
        )
    except Exception as exc:
        runtime.event_writer.append(ZfEvent(
            type="orchestrator.dispatch_failed",
            actor="zf-cli",
            task_id=task.id,
            payload={
                "role": role.name,
                "instance_id": role.instance_id,
                "trigger_event": trigger_event.type,
                "trigger_event_id": trigger_event.id,
                "dispatch_id": dispatch_context.dispatch_id,
                "failure_kind": "durable_operation_prepare_failed",
                "reason": str(exc),
            },
            causation_id=rework_request.id,
            correlation_id=rework_request.correlation_id,
        ))
        return TaskRefRepairDispatch(dispatch_context, None, False)
    if operation is not None and not operation.prepared.should_dispatch:
        runtime._emit_dispatch_skipped(
            task=task,
            role=role,
            reason=(
                "task-ref repair durable operation is already "
                f"{operation.prepared.ensure_status}"
            ),
        )
        return TaskRefRepairDispatch(dispatch_context, operation, False)
    return TaskRefRepairDispatch(
        (
            operation.bind_dispatch_context(dispatch_context)
            if operation is not None
            else dispatch_context
        ),
        operation,
        True,
        causation_id=rework_request.id,
        correlation_id=(
            rework_request.correlation_id
            or trigger_event.correlation_id
        ),
    )


def render_task_ref_repair_section(
    runtime: Any,
    *,
    trigger_event: ZfEvent,
    operation: TaskRefRepairOperation | None,
) -> str:
    """Render task-ref repair instructions outside the oversized dispatcher."""

    if trigger_event.type != TASK_REF_REPAIR_REQUESTED_EVENT:
        return ""
    if _task_ref_scope_repair_payload(trigger_event.payload):
        return (
            "\n## Task Ref Source Scope Repair Contract\n"
            "The rejected `source_commit` contains files outside this "
            "task's contract scope. This is a source-scope repair, not "
            "a metadata-only handoff repair.\n"
            "- Do not emit a metadata-only repair and do not reuse the "
            "rejected `source_commit`.\n"
            "- Create or select a new `source_commit` whose diff "
            "contains only repo-relative files allowed by this task's "
            "contract scope, then emit that new commit with "
            "`source_branch`, `workdir`, and `files_touched`.\n"
            "- Keep `changed_files`, `files_touched`, and "
            "`artifact_refs` to repo-relative source/artifact paths "
            "inside the task contract scope.\n"
            "- Put non-file evidence such as `git:<sha>`, "
            "`branch:<name>`, briefing paths, and diagnostics in "
            "`evidence_refs` only.\n"
            "- If the rejected commit cannot be split without losing "
            "required work, emit `dev.blocked` instead of re-emitting the "
            "same rejected commit. The payload must include "
            "`failure_class=task_contract_unsatisfiable`, "
            "`recommended_action=replan`, a concrete `reason`, and non-empty "
            "replayable `evidence_refs`; do not emit `dev.failed` for this "
            "contract-shape blocker.\n"
        )
    durable_identity = operation.briefing_section() if operation is not None else ""
    return (
        "\n## Task Ref Repair Handoff Contract\n"
        "This repair is complete only when the next `dev.build.done` "
        "payload can be accepted by TaskRefManager in worktree mode.\n"
        "- Read the rejected completion named by `source_event_id` "
        "in Trigger Payload Evidence and use its `dev.build.done` "
        "payload as the base. "
        + runtime._task_ref_repair_identity_instruction(trigger_event)
        + durable_identity
        + "- The replacement payload requires a non-empty `summary`; "
        "also provide `residual_risks` and `next_agent_input`. Do not "
        "run the bare completion command below without `--payload` "
        "or `--payload-file`.\n"
        "- Emit top-level `source_commit`, `source_branch`, `workdir`, "
        "and `files_touched` fields for the current writer worktree.\n"
        "- Keep `changed_files`, `files_touched`, and `artifact_refs` "
        "to repo-relative source/artifact paths inside the task contract "
        "scope; use `[]` when this is a metadata-only repair.\n"
        "- Do not put `git:<sha>`, `branch:<name>`, `briefing:<file>`, "
        "or other non-file evidence URIs in `changed_files`, "
        "`files_touched`, or `artifact_refs`; put those in "
        "`evidence_refs` only.\n"
        "- `.claude/**` and `.codex/**` skill materialization is "
        "runtime-owned and filtered by TaskRefManager's workdir scan. "
        "Do not commit, delete, add git excludes for, or declare "
        "`worktree_dirty` because of those paths. Use the rejection's "
        "`dirty_files` list as the repair scope.\n"
        "- Codex telemetry lives in the linked worker's private CODEX_HOME. "
        "Preserve a dirty `.codex/hooks.json` in the checkout and report it "
        "for owner review if it falls outside the task scope.\n"
    )


def prepare_task_ref_repair_operation(
    runtime: Any,
    *,
    task: Task,
    role: RoleConfig,
    trigger_event: ZfEvent,
    dispatch_context: DispatchContext,
) -> TaskRefRepairOperation | None:
    """Prepare one replacement call for a selected blocking writer child."""

    if (
        trigger_event.type != "task.ref.repair.requested"
        or str(role.role_kind or "") != "writer"
    ):
        return None
    trigger_payload = (
        trigger_event.payload if isinstance(trigger_event.payload, dict) else {}
    )
    source_event_id = str(trigger_payload.get("source_event_id") or "").strip()
    if not source_event_id:
        return None
    try:
        source_event = next(
            event
            for event in reversed(runtime.event_log.read_all())
            if event.id == source_event_id
        )
    except (OSError, StopIteration):
        return None
    source_payload = (
        source_event.payload if isinstance(source_event.payload, dict) else {}
    )
    child = runtime._writer_source_fanout_child(source_payload, task_id=task.id)
    fanout_id = str(source_payload.get("fanout_id") or "").strip()
    if not fanout_id and child:
        fanout_id = str(child.get("fanout_id") or "").strip()
    if not child or not fanout_id:
        return None
    manifest = runtime._fanout_manifest(fanout_id)
    if not manifest or str(manifest.get("topology") or "") != "fanout_writer_scoped":
        return None

    child_payload = child.get("payload") if isinstance(child.get("payload"), dict) else {}
    current = {**child_payload, **child}
    if result_protocol_mode(runtime.config, current) != "blocking":
        return None

    old_operation_id = str(current.get("operation_id") or "").strip()
    old_request_hash = str(current.get("request_hash") or "").strip()
    child_id = str(
        source_payload.get("child_id")
        or source_payload.get("child_run")
        or child.get("child_id")
        or ""
    ).strip()
    if not child_id:
        return None
    stage_id = str(manifest.get("stage_id") or source_payload.get("stage_id") or "")
    trace_id = str(
        manifest.get("trace_id")
        or source_event.correlation_id
        or trigger_event.correlation_id
        or dispatch_context.trace_id
        or ""
    )
    workflow_run_id = str(dispatch_context.run_id or trigger_payload.get("workflow_run_id") or "")
    dispatch_id = str(dispatch_context.dispatch_id or "")
    if not workflow_run_id or not dispatch_id:
        return None
    scheduler_attempt_id = task_attempt_id(workflow_run_id, task.id, dispatch_id)
    repair_run_id = (
        f"run-{fanout_id}-{child_id}-repair-{trigger_event.id[-12:]}"
    )

    task_item = dict(current)
    task_item.pop("payload", None)
    for key in _STALE_CALL_INPUT_FIELDS:
        task_item.pop(key, None)
    base_commit = str(runtime._writer_child_base_commit(child) or "").strip()
    workdir = str(current.get("workdir") or source_payload.get("workdir") or "").strip()
    target_ref = str(child.get("target_ref") or manifest.get("target_ref") or "")
    task_item.update({
        "task_id": task.id,
        "fanout_id": fanout_id,
        "stage_id": stage_id,
        "child_id": child_id,
        "run_id": repair_run_id,
        "role_instance": role.instance_id,
        "workflow_run_id": workflow_run_id,
        "target_ref": target_ref,
        "workdir": workdir,
        "base_commit": base_commit,
        "dispatch_base_commit": base_commit,
        "rework_of": trigger_event.id,
        "repair_of_operation_id": old_operation_id,
        "result_protocol_mode": "blocking",
        "canonical_success_event": "dev.build.done",
        "canonical_failure_event": "dev.blocked",
        "skills": list(role.skills),
    })
    context = FanoutContext(
        fanout_id=fanout_id,
        stage_id=stage_id,
        topology="fanout_writer_scoped",
        trace_id=trace_id,
        trigger_event_id=trigger_event.id,
        target_ref=target_ref,
        expected_children=[],
    )
    fanout_child = FanoutChild(
        child_id=child_id,
        role_instance=role.instance_id,
        target_ref=target_ref,
        payload=task_item,
    )
    project_path = workdir or str(runtime.project_root)
    _snapshot, _descriptor = runtime._prepare_writer_contract_snapshot(
        task_item=task_item,
        context=context,
        project_path=project_path,
    )
    operation_payload = {
        **task_item,
        "fanout_id": fanout_id,
        "trace_id": trace_id,
        "stage_id": stage_id,
        "child_id": child_id,
        "run_id": repair_run_id,
        "role_instance": role.instance_id,
        "target_ref": target_ref,
    }
    operation_key = f"{child_id}@fanout:{fanout_id}"
    prepared = prepare_call_operation(
        runtime,
        payload=operation_payload,
        operation_type="fanout_writer_child",
        operation_key=operation_key,
        stage_id=stage_id,
        task_id=task.id,
        dispatch_id=repair_run_id,
        attempt_id_override=scheduler_attempt_id,
        causation_id=trigger_event.id,
        correlation_id=trace_id,
    )

    if old_operation_id and old_operation_id != prepared.operation_id:
        old_operation = load_workflow_operation(runtime.event_log, old_operation_id)
        if old_operation is not None and str(old_operation.get("status") or "") not in (
            TERMINAL_OPERATION_STATUSES
        ):
            workflow_operation_service(runtime).supersede(
                operation_id=old_operation_id,
                request_hash=(
                    str(old_operation.get("request_hash") or "")
                    or old_request_hash
                ),
                workflow_run_id=(
                    str(old_operation.get("workflow_run_id") or "")
                    or workflow_run_id
                ),
                reason=f"task-ref repair rotated to {prepared.operation_id}",
                task_id=task.id,
                causation_id=trigger_event.id,
                correlation_id=trace_id,
            )

    rebind_payload: dict[str, Any] = {
        "fanout_id": fanout_id,
        "trace_id": trace_id,
        "stage_id": stage_id,
        "child_id": child_id,
        "run_id": repair_run_id,
        "role_instance": role.instance_id,
        "target_ref": target_ref,
        "task_id": task.id,
        "scope": str(current.get("scope") or ""),
        "workdir": workdir,
        "source_branch": str(current.get("source_branch") or ""),
        "pdd_id": str(current.get("pdd_id") or manifest.get("pdd_id") or ""),
        "feature_id": str(current.get("feature_id") or ""),
        "task_map_ref": str(current.get("task_map_ref") or manifest.get("task_map_ref") or ""),
        "source_index_ref": str(current.get("source_index_ref") or manifest.get("source_index_ref") or ""),
        "retry_of_run_id": str(child.get("run_id") or ""),
        "repair_of_event_id": trigger_event.id,
        "source": "task_ref_repair",
        "payload": dict(operation_payload),
    }
    for key in KERNEL_BOUND_WRITER_RESULT_FIELDS:
        value = operation_payload.get(key)
        if value not in (None, ""):
            rebind_payload[key] = value
    for key in _FANOUT_AFFINITY_METADATA_KEYS:
        value = current.get(key)
        if value not in (None, ""):
            rebind_payload[key] = value
    return TaskRefRepairOperation(
        prepared=prepared,
        operation_payload=operation_payload,
        rebind_payload=rebind_payload,
        old_operation_id=old_operation_id,
        old_request_hash=old_request_hash,
    )


def publish_task_ref_repair_operation_dispatch(
    runtime: Any,
    *,
    repair: TaskRefRepairOperation,
    context: DispatchContext,
    task_id: str,
    briefing_path: Path,
    causation_id: str,
    correlation_id: str,
) -> ZfEvent:
    """Mark the replacement call started and rebind the logical child."""

    mark_call_operation_started(
        runtime,
        repair.prepared,
        task_id=task_id,
        dispatch_id=str(context.dispatch_id or ""),
        causation_id=causation_id,
        correlation_id=correlation_id,
    )
    payload = {
        **repair.rebind_payload,
        "briefing_path": str(briefing_path),
        **dispatch_attempt_payload(context, include_run_alias=False),
    }
    return runtime.event_writer.append(ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        task_id=task_id,
        payload=payload,
        causation_id=causation_id,
        correlation_id=correlation_id,
    ))


__all__ = [
    "TASK_REF_REPAIR_REQUESTED_EVENT",
    "TaskRefRepairDispatch",
    "TaskRefRepairOperation",
    "prepare_task_ref_repair_dispatch",
    "prepare_task_ref_repair_operation",
    "publish_task_ref_repair_operation_dispatch",
    "render_task_ref_repair_section",
    "task_ref_repair_dispatch_id",
]
