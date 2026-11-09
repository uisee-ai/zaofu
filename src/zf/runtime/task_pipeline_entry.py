"""Admission and reconciliation for non-writer Task Pipeline entries."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision
from zf.runtime.writer_task_materialization import (
    TASK_PIPELINE_ENTRY_EXTERNAL_GATE,
    TASK_PIPELINE_ENTRY_STANDARD,
    TASK_PIPELINE_ENTRY_VERIFY_ONLY,
)


TASK_PIPELINE_EXTERNAL_GATE_SCHEMA = "task-pipeline-external-gate.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ACTIVE_OPERATION_STATUSES = frozenset({"requested", "reserved", "running", "suspended"})
_SYSTEM_ACTORS = frozenset({
    "autoresearch",
    "orchestrator",
    "run-manager",
    "supervisor",
    "zf-runtime",
})


def task_pipeline_entry_mode(task: Any) -> str:
    """Return the scheduler entry mode preserved in the Task contract."""

    contract = getattr(task, "contract", None)
    if contract is None and isinstance(task, Mapping):
        contract = task.get("contract")
    evidence = _contract_value(contract, "evidence_contract", {})
    evidence = evidence if isinstance(evidence, Mapping) else {}
    explicit = str(
        evidence.get("task_pipeline_entry_mode")
        or evidence.get("execution_mode")
        or ""
    ).strip().lower()
    if explicit in {"runtime_only", "verify_only"}:
        return TASK_PIPELINE_ENTRY_VERIFY_ONLY
    if explicit in {"external_gate", "manual_evidence"}:
        return TASK_PIPELINE_ENTRY_EXTERNAL_GATE
    if evidence.get("runtime_only") is True:
        return TASK_PIPELINE_ENTRY_VERIFY_ONLY

    criteria = _contract_value(contract, "acceptance_criteria", [])
    criteria_rows = criteria if isinstance(criteria, list) else []
    mandatory = [
        item for item in criteria_rows
        if isinstance(item, Mapping) and item.get("mandatory", True) is not False
    ]
    human_owned = bool(mandatory) and all(
        str(item.get("verification_owner") or "").strip().lower() == "human"
        or str(item.get("verification_tier") or "").strip().lower()
        == "manual_evidence"
        for item in mandatory
    )
    if human_owned and task_pipeline_required_manual_evidence(task):
        return TASK_PIPELINE_ENTRY_EXTERNAL_GATE
    return TASK_PIPELINE_ENTRY_STANDARD


def task_pipeline_required_manual_evidence(task: Any) -> list[str]:
    contract = getattr(task, "contract", None)
    if contract is None and isinstance(task, Mapping):
        contract = task.get("contract")
    evidence = _contract_value(contract, "evidence_contract", {})
    evidence = evidence if isinstance(evidence, Mapping) else {}
    return _evidence_refs(evidence.get("required_manual_evidence"))


def task_pipeline_entry_target(
    task: Any,
    context: Mapping[str, Any],
    *,
    project_root: Path,
) -> str:
    """Resolve the exact no-patch target and bind it to generation admission."""

    contract = getattr(task, "contract", None)
    if contract is None and isinstance(task, Mapping):
        contract = task.get("contract")
    evidence = _contract_value(contract, "evidence_contract", {})
    evidence = evidence if isinstance(evidence, Mapping) else {}
    declared = {
        _strip_git_ref(evidence.get(key))
        for key in (
            "candidate_target",
            "immutable_baseline",
            "continuation_checkpoint",
        )
        if _strip_git_ref(evidence.get(key))
    }
    if len(declared) > 1:
        raise ValueError("Task Pipeline read-only targets diverge")
    admitted = str(context.get("dispatch_base_commit") or "").strip()
    requested = next(iter(declared), admitted)
    if not requested:
        raise ValueError("Task Pipeline read-only entry has no exact target")
    target = _resolve_commit(project_root, requested)
    if admitted and target != _resolve_commit(project_root, admitted):
        raise ValueError(
            "Task Pipeline read-only target differs from generation dispatch base"
        )
    return target


def reconcile_task_pipeline_entries(
    runtime: Any,
    *,
    generation_contexts: Mapping[str, Mapping[str, Any]],
) -> tuple[list[WorkflowRuntimeDecision], set[str]]:
    """Reconcile manual gates before recovery can redrive an obsolete writer."""

    decisions: list[WorkflowRuntimeDecision] = []
    events = list(runtime.event_log.read_all())
    from zf.runtime.task_pipeline_runtime_selection import operation_matches_generation
    from zf.runtime.workflow_operation import reduce_workflow_operations

    operations = [
        row
        for row in reduce_workflow_operations(events).values()
        if operation_matches_generation(row, generation_contexts)
    ]
    external_tasks: dict[str, Any] = {}
    for task_id, context in sorted(generation_contexts.items()):
        task = runtime.task_store.get(task_id)
        status = str(
            task.get("status", "") if isinstance(task, Mapping)
            else getattr(task, "status", "")
        )
        if task is None or status in {"done", "cancelled"}:
            continue
        mode = task_pipeline_entry_mode(task)
        if mode == TASK_PIPELINE_ENTRY_STANDARD:
            continue
        if mode == TASK_PIPELINE_ENTRY_EXTERNAL_GATE:
            external_tasks[task_id] = task
        decisions.extend(_supersede_incompatible_operations(
            runtime,
            task_id=task_id,
            mode=mode,
            context=context,
            operations=operations,
        ))

    if not external_tasks:
        return decisions, set()

    events = list(runtime.event_log.read_all())
    satisfied = task_pipeline_satisfied_external_gate_ids(
        events=events,
        tasks=external_tasks.values(),
        generation_contexts=generation_contexts,
        project_root=Path(runtime.project_root),
    )
    for task_id, task in sorted(external_tasks.items()):
        if task_id in satisfied:
            continue
        context = generation_contexts[task_id]
        decision, admitted = _reconcile_external_gate(
            runtime,
            task=task,
            context=context,
            events=list(runtime.event_log.read_all()),
        )
        decisions.extend(decision)
        if admitted:
            satisfied.add(task_id)
    return decisions, satisfied


def task_pipeline_satisfied_external_gate_ids(
    *,
    events: Iterable[ZfEvent],
    tasks: Iterable[Any],
    generation_contexts: Mapping[str, Mapping[str, Any]],
    project_root: Path,
) -> set[str]:
    """Return current gate facts whose external artifact still matches its hash."""

    task_by_id: dict[str, Any] = {}
    for task in tasks:
        if task is None:
            continue
        task_id = str(
            task.get("id", "") if isinstance(task, Mapping)
            else getattr(task, "id", "")
        ).strip()
        if task_id:
            task_by_id[task_id] = task
    satisfied: set[str] = set()
    for event in events:
        if event.type != "task.pipeline.external_gate.satisfied":
            continue
        task_id = str(event.task_id or "")
        task = task_by_id.get(task_id)
        context = generation_contexts.get(task_id)
        if (
            task is None
            or context is None
            or task_pipeline_entry_mode(task)
            != TASK_PIPELINE_ENTRY_EXTERNAL_GATE
            or not _event_matches_context(event, task_id, context)
        ):
            continue
        payload = _payload(event)
        descriptor = payload.get("evidence_ref")
        if not isinstance(descriptor, Mapping):
            continue
        required = task_pipeline_required_manual_evidence(task)
        if _external_file_matches(
            descriptor,
            required_refs=required,
            project_root=project_root,
        ):
            satisfied.add(task_id)
    return satisfied


def task_pipeline_external_evidence_bindings(
    runtime: Any,
    *,
    task: Any,
    context: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Project upstream manual evidence into a read-only Verify briefing."""

    contract = getattr(task, "contract", None)
    evidence = _contract_value(contract, "evidence_contract", {})
    evidence = evidence if isinstance(evidence, Mapping) else {}
    env_name = str(evidence.get("required_external_digest_env") or "").strip()
    if not env_name:
        return []
    blocked_by = {
        str(item).strip()
        for item in getattr(task, "blocked_by", []) or []
        if str(item).strip()
    }
    run_id = str(context.get("workflow_run_id") or "")
    bindings: list[dict[str, str]] = []
    for event in reversed(runtime.event_log.read_all()):
        if (
            event.type != "task.pipeline.external_gate.satisfied"
            or str(event.task_id or "") not in blocked_by
        ):
            continue
        payload = _payload(event)
        if str(payload.get("workflow_run_id") or "") != run_id:
            continue
        receipt_digest = str(payload.get("receipt_digest") or "").strip()
        descriptor = payload.get("evidence_ref")
        if not _SHA256.fullmatch(receipt_digest) or not isinstance(descriptor, Mapping):
            continue
        if not _external_file_matches(
            descriptor,
            required_refs=[str(descriptor.get("path") or "")],
            project_root=Path(runtime.project_root),
        ):
            continue
        bindings.append({
            "env": env_name,
            "value": receipt_digest,
            "path": str(descriptor.get("path") or ""),
            "sha256": str(descriptor.get("sha256") or ""),
            "source_event_id": event.id,
        })
        break
    return bindings


def admit_task_pipeline_read_only_ref(
    runtime: Any,
    *,
    task: Any,
    context: Mapping[str, Any],
    target_commit: str,
    causation_id: str,
    workdir: str = "",
) -> dict[str, Any]:
    """Admit and publish one exact no-patch TaskRef idempotently."""

    from zf.runtime.task_refs import TaskRefManager

    feature_id = str(getattr(task.contract, "feature_id", "") or "")
    result = TaskRefManager(
        state_dir=Path(runtime.state_dir),
        project_root=Path(runtime.project_root),
        config=runtime.config,
    ).admit_read_only_target(
        task_id=str(task.id),
        source_commit=target_commit,
        trigger_event_id=causation_id,
        trace_id=str(context.get("workflow_run_id") or ""),
        workdir=workdir,
        pdd_id=feature_id,
        feature_id=feature_id,
    )
    _emit_task_ref_updated_once(
        runtime,
        task_id=str(task.id),
        context=context,
        result_payload=result.payload,
        causation_id=causation_id,
    )
    return dict(result.payload)


def _reconcile_external_gate(
    runtime: Any,
    *,
    task: Any,
    context: Mapping[str, Any],
    events: list[ZfEvent],
) -> tuple[list[WorkflowRuntimeDecision], bool]:
    from zf.runtime.task_pipeline_dispatch_events import emit_task_pipeline_waiting_once

    task_id = str(task.id)
    required_refs = task_pipeline_required_manual_evidence(task)
    target = task_pipeline_entry_target(
        task,
        context,
        project_root=Path(runtime.project_root),
    )
    token = _decision_token(task_id, context, target, required_refs)
    escalation = _current_escalation(
        events,
        task_id=task_id,
        context=context,
        decision_token=token,
    )
    if escalation is None:
        escalation = runtime.event_writer.append(ZfEvent(
            type="human.escalate",
            actor="orchestrator",
            origin="kernel",
            task_id=task_id,
            correlation_id=str(context.get("workflow_run_id") or "") or None,
            causation_id=str(context.get("generation_admitted_event_id") or "") or None,
            payload={
                "schema_version": TASK_PIPELINE_EXTERNAL_GATE_SCHEMA,
                "workflow_run_id": str(context.get("workflow_run_id") or ""),
                "task_map_generation": str(context.get("task_map_generation") or ""),
                "task_id": task_id,
                "decision_token": token,
                "reason": "required_manual_evidence_pending",
                "blocker_kind": "external_gate",
                "blocking_scope": "task",
                "owner_route": "human",
                "action_policy": "human_evidence",
                "required_evidence_refs": required_refs,
                "required_target_commit": target,
                "manual_commands": _manual_commands(task),
                "suggested_options": ["provide_required_evidence", "safe_halt"],
                "resolution_event_type": "human.resolved",
                "resolution_schema": "task-pipeline-external-gate-resolution.v1",
            },
        ))
    resolution, reason = _current_resolution(
        events=list(runtime.event_log.read_all()),
        escalation=escalation,
        task_id=task_id,
        context=context,
        decision_token=token,
        required_refs=required_refs,
        target_commit=target,
        project_root=Path(runtime.project_root),
    )
    if resolution is None:
        emit_task_pipeline_waiting_once(
            runtime,
            task_id=task_id,
            stage="external_gate",
            operation_generation=1,
            context=context,
            reason=reason,
            detail=(
                "A genuine operator-bound human.resolved event with immutable "
                "path, file SHA-256, receipt digest, and exact target is required"
            ),
        )
        return [WorkflowRuntimeDecision(
            action="task_pipeline_external_gate_waiting",
            task_id=task_id,
            role="human",
            reason=reason,
        )], False

    descriptor = dict(_payload(resolution)["evidence_ref"])
    receipt_digest = str(_payload(resolution).get("receipt_digest") or "")
    admit_task_pipeline_read_only_ref(
        runtime,
        task=task,
        context=context,
        target_commit=target,
        causation_id=resolution.id,
    )
    existing = _current_satisfaction(
        runtime.event_log.read_all(),
        task_id=task_id,
        context=context,
        resolution_event_id=resolution.id,
    )
    if existing is None:
        pipeline_key = _pipeline_key(task_id, context)
        existing = runtime.event_writer.append(ZfEvent(
            type="task.pipeline.external_gate.satisfied",
            actor="orchestrator",
            origin="kernel",
            task_id=task_id,
            correlation_id=str(context.get("workflow_run_id") or "") or None,
            causation_id=resolution.id,
            payload={
                "schema_version": TASK_PIPELINE_EXTERNAL_GATE_SCHEMA,
                "workflow_run_id": str(context.get("workflow_run_id") or ""),
                "task_map_generation": str(context.get("task_map_generation") or ""),
                "task_id": task_id,
                "operation_generation": 1,
                "pipeline_key": pipeline_key,
                "decision_token": token,
                "escalation_event_id": escalation.id,
                "resolution_event_id": resolution.id,
                "target_commit": target,
                "evidence_ref": descriptor,
                "receipt_digest": receipt_digest,
            },
        ))
    return [WorkflowRuntimeDecision(
        action="task_pipeline_external_gate_satisfied",
        task_id=task_id,
        role="human",
        reason=f"manual evidence bound by {existing.id}",
    )], True


def _supersede_incompatible_operations(
    runtime: Any,
    *,
    task_id: str,
    mode: str,
    context: Mapping[str, Any],
    operations: Iterable[Mapping[str, Any]],
) -> list[WorkflowRuntimeDecision]:
    allowed = (
        {"integration"}
        if mode == TASK_PIPELINE_ENTRY_EXTERNAL_GATE
        else {"verify", "acceptance_review", "integration"}
    )
    from zf.runtime.workflow_operation import WorkflowOperationService

    service = WorkflowOperationService(
        state_dir=Path(runtime.state_dir),
        event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    )
    decisions: list[WorkflowRuntimeDecision] = []
    for row in operations:
        stage = str(row.get("task_pipeline_stage") or row.get("parent_stage_id") or "")
        if (
            str(row.get("task_id") or "") != task_id
            or stage in allowed
        ):
            continue
        event: ZfEvent | None = None
        operation_active = (
            str(row.get("status") or "") in _ACTIVE_OPERATION_STATUSES
        )
        if operation_active:
            event = service.supersede(
                operation_id=str(row.get("operation_id") or ""),
                request_hash=str(row.get("request_hash") or ""),
                workflow_run_id=str(context.get("workflow_run_id") or ""),
                task_id=task_id,
                reason="task_pipeline_entry_mode_mismatch",
                causation_id=str(
                    context.get("generation_admitted_event_id") or ""
                ),
                correlation_id=str(context.get("workflow_run_id") or ""),
            )
            if event is None:
                continue
            from zf.runtime.task_attempt_operation_settlement import (
                settle_terminal_operation_attempt,
            )

            settle_terminal_operation_attempt(runtime, event)
            decisions.append(WorkflowRuntimeDecision(
                action="task_pipeline_incompatible_operation_superseded",
                task_id=task_id,
                reason=f"{stage} is incompatible with {mode}",
            ))
        from zf.runtime.task_pipeline_terminal import (
            archive_task_pipeline_stage_binding,
        )

        if archive_task_pipeline_stage_binding(
            runtime,
            binding_key=str(row.get("task_stage_session_binding") or ""),
            task_id=task_id,
            causation_id=(
                event.id if event is not None
                else str(row.get("last_event_id") or "")
            ),
            reason="task_pipeline_entry_mode_mismatch",
        ):
            decisions.append(WorkflowRuntimeDecision(
                action="task_pipeline_incompatible_session_archived",
                task_id=task_id,
                reason=f"{stage} session is incompatible with {mode}",
            ))
    return decisions


def _current_resolution(
    *,
    events: list[ZfEvent],
    escalation: ZfEvent,
    task_id: str,
    context: Mapping[str, Any],
    decision_token: str,
    required_refs: list[str],
    target_commit: str,
    project_root: Path,
) -> tuple[ZfEvent | None, str]:
    saw_resolution = False
    for event in reversed(events):
        if event.type != "human.resolved" or str(event.task_id or "") != task_id:
            continue
        payload = _payload(event)
        if str(payload.get("decision_token") or "") != decision_token:
            continue
        saw_resolution = True
        if not _event_matches_context(event, task_id, context):
            continue
        if str(payload.get("escalation_event_id") or "") != escalation.id:
            continue
        if str(payload.get("action") or "") != "provide_required_evidence":
            continue
        actor = str(event.actor or "").strip().lower()
        if not actor or actor in _SYSTEM_ACTORS:
            continue
        if str(payload.get("target_commit") or "") != target_commit:
            continue
        receipt_digest = str(payload.get("receipt_digest") or "").strip()
        if not _SHA256.fullmatch(receipt_digest):
            continue
        descriptor = payload.get("evidence_ref")
        if not isinstance(descriptor, Mapping):
            continue
        if not _external_file_matches(
            descriptor,
            required_refs=required_refs,
            project_root=project_root,
        ):
            continue
        return event, ""
    return None, (
        "required_manual_evidence_resolution_invalid"
        if saw_resolution
        else "required_manual_evidence_pending"
    )


def _external_file_matches(
    descriptor: Mapping[str, Any],
    *,
    required_refs: list[str],
    project_root: Path,
) -> bool:
    raw_path = str(descriptor.get("path") or descriptor.get("ref") or "").strip()
    digest = str(descriptor.get("sha256") or "").strip().lower()
    if not raw_path or not _SHA256.fullmatch(digest):
        return False
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = project_root / path
    if path.is_symlink():
        return False
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return False
    required_paths = {
        _resolve_external_ref(ref, project_root)
        for ref in required_refs
        if str(ref).strip()
    }
    if required_paths and resolved not in required_paths:
        return False
    if not resolved.is_file():
        return False
    try:
        if resolved.stat().st_mode & 0o222:
            return False
        actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
    except OSError:
        return False
    return actual == digest


def _current_escalation(
    events: Iterable[ZfEvent],
    *,
    task_id: str,
    context: Mapping[str, Any],
    decision_token: str,
) -> ZfEvent | None:
    return next((
        event for event in reversed(list(events))
        if event.type == "human.escalate"
        and str(event.task_id or "") == task_id
        and str(_payload(event).get("decision_token") or "") == decision_token
        and _event_matches_context(event, task_id, context)
    ), None)


def _current_satisfaction(
    events: Iterable[ZfEvent],
    *,
    task_id: str,
    context: Mapping[str, Any],
    resolution_event_id: str,
) -> ZfEvent | None:
    return next((
        event for event in reversed(list(events))
        if event.type == "task.pipeline.external_gate.satisfied"
        and str(event.task_id or "") == task_id
        and str(_payload(event).get("resolution_event_id") or "")
        == resolution_event_id
        and _event_matches_context(event, task_id, context)
    ), None)


def _emit_task_ref_updated_once(
    runtime: Any,
    *,
    task_id: str,
    context: Mapping[str, Any],
    result_payload: Mapping[str, Any],
    causation_id: str,
) -> None:
    source_commit = str(result_payload.get("source_commit") or "")
    if any(
        event.type == "task.ref.updated"
        and str(event.task_id or "") == task_id
        and str(_payload(event).get("source_commit") or "") == source_commit
        and str(_payload(event).get("workflow_run_id") or "")
        == str(context.get("workflow_run_id") or "")
        and str(_payload(event).get("task_map_generation") or "")
        == str(context.get("task_map_generation") or "")
        for event in runtime.event_log.read_all()
    ):
        return
    runtime.event_writer.append(ZfEvent(
        type="task.ref.updated",
        actor="zf-cli",
        origin="kernel",
        task_id=task_id,
        correlation_id=str(context.get("workflow_run_id") or "") or None,
        causation_id=causation_id or None,
        payload={
            **dict(result_payload),
            "workflow_run_id": str(context.get("workflow_run_id") or ""),
            "task_map_generation": str(context.get("task_map_generation") or ""),
            "source": "task_pipeline_external_gate",
        },
    ))


def _event_matches_context(
    event: ZfEvent,
    task_id: str,
    context: Mapping[str, Any],
) -> bool:
    payload = _payload(event)
    return bool(
        str(event.task_id or payload.get("task_id") or "") == task_id
        and str(payload.get("workflow_run_id") or event.correlation_id or "")
        == str(context.get("workflow_run_id") or "")
        and str(payload.get("task_map_generation") or "")
        == str(context.get("task_map_generation") or "")
    )


def _manual_commands(task: Any) -> list[str]:
    validation = getattr(task.contract, "validation", {}) or {}
    commands = validation.get("commands") if isinstance(validation, Mapping) else []
    command_rows = commands if isinstance(commands, list) else []
    return [
        str(item.get("command") or "").strip()
        for item in command_rows
        if isinstance(item, Mapping)
        and (
            str(item.get("owner") or "").strip().lower() == "human"
            or str(item.get("tier") or "").strip().lower() == "manual_evidence"
        )
        and str(item.get("command") or "").strip()
    ]


def _evidence_refs(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return list(dict.fromkeys(
            str(item).strip() for item in value if str(item).strip()
        ))
    if isinstance(value, Mapping):
        for key in ("refs", "required_refs", "artifacts"):
            refs = _evidence_refs(value.get(key))
            if refs:
                return refs
    return []


def _contract_value(contract: Any, key: str, default: Any) -> Any:
    if isinstance(contract, Mapping):
        return contract.get(key, default)
    return getattr(contract, key, default)


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


def _strip_git_ref(value: Any) -> str:
    text = str(value or "").strip()
    return text.removeprefix("git:").removeprefix("commit:")


def _resolve_commit(project_root: Path, ref: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ValueError(f"Task Pipeline read-only target is invalid: {detail}")
    return result.stdout.strip()


def _resolve_external_ref(value: str, project_root: Path) -> Path:
    path = Path(str(value or "")).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _decision_token(
    task_id: str,
    context: Mapping[str, Any],
    target: str,
    refs: list[str],
) -> str:
    seed = "|".join((
        str(context.get("workflow_run_id") or ""),
        task_id,
        str(context.get("task_map_generation") or ""),
        target,
        *sorted(refs),
    ))
    return "hdec-external-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _pipeline_key(task_id: str, context: Mapping[str, Any]) -> str:
    seed = "|".join((
        str(context.get("workflow_run_id") or ""),
        task_id,
        str(context.get("task_map_generation") or ""),
        "external_gate",
    ))
    return "tp-external-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


__all__ = [
    "TASK_PIPELINE_EXTERNAL_GATE_SCHEMA",
    "admit_task_pipeline_read_only_ref",
    "reconcile_task_pipeline_entries",
    "task_pipeline_entry_mode",
    "task_pipeline_entry_target",
    "task_pipeline_external_evidence_bindings",
    "task_pipeline_required_manual_evidence",
    "task_pipeline_satisfied_external_gate_ids",
]
