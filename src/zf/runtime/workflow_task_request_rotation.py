"""Controlled Request rotation for sequential Workflow Runs on one Task."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TERMINAL_STATES, TaskStore
from zf.runtime.run_admission import (
    RUN_TERMINAL_EVENT_TYPES,
    build_run_admission_projection,
    request_admission_view,
)
from zf.runtime.task_workflow_plans import task_workflow_binding_digest
from zf.runtime.workflow_anchor import (
    WORKFLOW_TASK_REQUEST_ROTATION_SOURCE,
    bind_workflow_request_to_task,
    mark_workflow_managed_task,
    workflow_task_request_binding,
)
from zf.runtime.workflow_origin import (
    WorkflowOriginError,
    build_workflow_origin_binding,
    workflow_origin_digest,
    workflow_origin_from_request,
)
from zf.runtime.workflow_requests import load_workflow_request


class WorkflowTaskRequestRotationError(ValueError):
    """A Task Request rotation cannot be proven safe."""


def legacy_cli_origin_migration_allowed(
    prior: dict[str, Any],
    supplied: dict[str, Any],
) -> bool:
    """Allow a one-time repair of requests created by the Kanban CLI shim.

    Older Kanban starts persisted a CLI origin even though the request was
    initiated from the Kanban Agent. A fresh retry may bind the same project
    to its actual Kanban conversation; other origin changes remain rejected.
    """
    return (
        str(prior.get("surface") or "") == "cli"
        and str(prior.get("source") or "") == "kanban-agent"
        and str(supplied.get("surface") or "") == "kanban_agent"
        and str(supplied.get("source") or "") == "kanban-agent"
        and bool(str(prior.get("project_id") or "").strip())
        and str(prior.get("project_id") or "").strip()
        == str(supplied.get("project_id") or "").strip()
    )


def terminal_retry_origin_binding(
    rotation: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Select the immutable origin for a proven terminal retry.

    The only permitted change repairs legacy Kanban requests that were stored
    as CLI origins.  It requires the same non-empty project and a concrete
    Kanban conversation; every other retry keeps the prior canonical origin.
    """

    prior = dict(rotation.get("origin_binding") or {})
    supplied = payload.get("origin_binding")
    if not isinstance(supplied, dict):
        conversation_id = str(payload.get("conversation_id") or "").strip()
        project_id = str(payload.get("project_id") or "").strip()
        if conversation_id and project_id:
            supplied = build_workflow_origin_binding(
                source=str(prior.get("source") or ""),
                project_id=project_id,
                conversation_id=conversation_id,
                thread_key=str(
                    payload.get("thread_key")
                    or payload.get("thread_id")
                    or "main"
                ),
            )
    if (
        isinstance(supplied, dict)
        and legacy_cli_origin_migration_allowed(prior, supplied)
    ):
        return dict(supplied)
    return prior


@dataclass(frozen=True)
class TaskRequestBindingDecision:
    should_bind: bool
    rotation: dict[str, Any]


def terminal_task_request_rotation_context(
    state_dir: Path,
    task_request: dict[str, Any],
) -> dict[str, Any]:
    """Prove that a Task's bound Request belongs to a closed Project Run."""

    request_id = str(task_request.get("request_id") or "").strip()
    if not request_id:
        return {}
    request = load_workflow_request(state_dir, request_id)
    if not request:
        return {}
    try:
        origin_binding = workflow_origin_from_request(request)
    except WorkflowOriginError:
        return {}
    origin_digest = workflow_origin_digest(origin_binding)
    expected_origin_digest = str(
        task_request.get("origin_binding_digest") or ""
    ).strip()
    if expected_origin_digest and expected_origin_digest != origin_digest:
        return {}

    events = EventLog(Path(state_dir) / "events.jsonl").read_all()
    run_id = str(request.get("run_id") or request_id).strip()
    admission = request_admission_view(
        events,
        request_id=request_id,
        run_id=run_id,
    )
    run_projection = build_run_admission_projection(events)
    if not admission.get("terminal") or run_projection.active_run_ids:
        return {}
    return {
        "prior_request_id": request_id,
        "prior_request_revision": int(
            task_request.get("request_revision") or 0
        ),
        "current_request_revision": int(request.get("revision") or 0),
        "prior_run_id": str(admission.get("run_id") or run_id),
        "prior_terminal_event_id": str(
            admission.get("terminal_event_id") or ""
        ),
        "prior_terminal_type": str(admission.get("terminal_type") or ""),
        "origin_binding": origin_binding,
        "origin_binding_digest": origin_digest,
    }


def fresh_task_request_origin_binding(
    state_dir: Path,
    task: Task | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Revalidate the prior terminal proof before creating a fresh Request."""

    task_request = (
        workflow_task_request_binding(task) if task is not None else {}
    )
    rotation = terminal_task_request_rotation_context(
        state_dir,
        task_request,
    )
    supplied_origin = payload.get("origin_binding")
    try:
        prior_request_revision = int(
            payload.get("prior_request_revision") or 0
        )
    except (TypeError, ValueError):
        prior_request_revision = 0
    try:
        supplied_origin_digest = (
            workflow_origin_digest(supplied_origin)
            if isinstance(supplied_origin, dict)
            else ""
        )
    except WorkflowOriginError:
        supplied_origin_digest = ""
    supplied_binding = supplied_origin if isinstance(supplied_origin, dict) else None
    migrated_origin = bool(
        supplied_binding
        and rotation
        and legacy_cli_origin_migration_allowed(
            dict(rotation.get("origin_binding") or {}),
            supplied_binding,
        )
    )
    if (
        not rotation
        or not isinstance(supplied_origin, dict)
        or str(payload.get("prior_request_id") or "").strip()
        != str(rotation.get("prior_request_id") or "")
        or prior_request_revision
        != int(rotation.get("prior_request_revision") or 0)
        or str(payload.get("prior_terminal_event_id") or "").strip()
        != str(rotation.get("prior_terminal_event_id") or "")
        or (
            supplied_origin_digest
            != str(rotation.get("origin_binding_digest") or "")
            and not migrated_origin
        )
    ):
        raise WorkflowTaskRequestRotationError(
            "fresh Workflow Request is not bound to a proven terminal "
            "Task request"
        )
    return dict(supplied_origin if migrated_origin else rotation["origin_binding"])


def task_request_binding_decision(
    state_dir: Path,
    task: Task,
    *,
    request_projection: dict[str, Any],
    canonical_origin: dict[str, Any],
    events: Iterable[ZfEvent],
) -> TaskRequestBindingDecision:
    """Decide whether submit may initially bind or rotate a Task Request."""

    request_id = str(request_projection.get("request_id") or "").strip()
    request_revision = int(request_projection.get("revision") or 0)
    origin_digest = workflow_origin_digest(canonical_origin)
    task_request = workflow_task_request_binding(task)
    if not task_request:
        return TaskRequestBindingDecision(should_bind=True, rotation={})
    binding_changed = (
        task_request["request_id"] != request_id
        or int(task_request["request_revision"]) != request_revision
        or task_request.get("origin_binding_digest") != origin_digest
    )
    if not binding_changed:
        return TaskRequestBindingDecision(should_bind=False, rotation={})

    rotation = terminal_task_request_rotation_context(
        state_dir,
        task_request,
    )
    event_list = list(events)
    new_admission = request_admission_view(
        event_list,
        request_id=request_id,
        run_id=str(request_projection.get("run_id") or request_id),
    )
    legacy_origin_migration = legacy_cli_origin_migration_allowed(
        dict(rotation.get("origin_binding") or {}),
        canonical_origin,
    ) if rotation else False
    if (
        task.status in TERMINAL_STATES
        or not rotation
        or request_id == task_request["request_id"]
        or bool(new_admission.get("status"))
        or (
            origin_digest != rotation.get("origin_binding_digest")
            and not legacy_origin_migration
        )
    ):
        raise WorkflowTaskRequestRotationError(
            "workflow Task is already bound to another Workflow Request revision"
        )
    return TaskRequestBindingDecision(
        should_bind=True,
        rotation=rotation,
    )


def apply_task_request_binding(
    state_dir: Path,
    *,
    task_store: TaskStore,
    event_writer: EventWriter,
    task: Task,
    request_projection: dict[str, Any],
    requested_event: ZfEvent,
    actor: str,
) -> TaskRequestBindingDecision:
    """Persist an initial or rotated Request binding through canonical APIs."""

    canonical_origin = workflow_origin_from_request(request_projection)
    decision = task_request_binding_decision(
        state_dir,
        task,
        request_projection=request_projection,
        canonical_origin=canonical_origin,
        events=event_writer.event_log.read_all(),
    )
    if not decision.should_bind:
        return decision

    request_id = str(request_projection.get("request_id") or "").strip()
    request_revision = int(request_projection.get("revision") or 0)
    origin_digest = workflow_origin_digest(canonical_origin)
    bind_workflow_request_to_task(
        mark_workflow_managed_task(task),
        request_id=request_id,
        request_revision=request_revision,
        origin_binding_digest=origin_digest,
    )
    rotation = decision.rotation
    from zf.runtime.task_contract_authority import (
        TaskContractAuthorityService,
    )

    TaskContractAuthorityService(
        task_store=task_store,
        event_writer=event_writer,
        state_dir=state_dir,
    ).replace(
        task,
        contract=task.contract,
        execution_binding=task.execution_binding,
        source=(
            WORKFLOW_TASK_REQUEST_ROTATION_SOURCE
            if rotation
            else "workflow_submit"
        ),
        actor=actor,
        causation_id=requested_event.id,
        correlation_id=requested_event.correlation_id,
        audit_payload={
            "contract_digest": task_workflow_binding_digest(task),
            "execution_owner": "workflow",
            "request_id": request_id,
            "request_revision": request_revision,
            "origin_binding_digest": origin_digest,
            "prior_request_id": str(
                rotation.get("prior_request_id") or ""
            ),
            "prior_request_revision": int(
                rotation.get("prior_request_revision") or 0
            ),
            "prior_run_id": str(rotation.get("prior_run_id") or ""),
            "prior_terminal_event_id": str(
                rotation.get("prior_terminal_event_id") or ""
            ),
            "prior_terminal_type": str(
                rotation.get("prior_terminal_type") or ""
            ),
        },
    )
    return decision


def accepted_task_request_rotation_event(
    events: list[ZfEvent],
    task: Task,
    accepted_event: ZfEvent,
) -> ZfEvent | None:
    """Find the exact rotation proof for a blocked Task's accepted Run."""

    payload = (
        accepted_event.payload
        if isinstance(accepted_event.payload, dict)
        else {}
    )
    task_request = workflow_task_request_binding(task)
    request_id = str(payload.get("request_id") or "").strip()
    try:
        request_revision = int(payload.get("request_revision") or 0)
    except (TypeError, ValueError):
        request_revision = 0
    return next((
        event
        for event in reversed(events)
        if event.type == "task.contract.update"
        and event.task_id == task.id
        and str((event.payload or {}).get("source") or "")
        == WORKFLOW_TASK_REQUEST_ROTATION_SOURCE
        and str((event.payload or {}).get("request_id") or "")
        == request_id
        and str(task_request.get("request_id") or "") == request_id
        and str(
            (event.payload or {}).get("request_revision") or ""
        ).strip()
        == str(request_revision)
        and int(task_request.get("request_revision") or 0)
        == request_revision
        and str(
            (event.payload or {}).get("origin_binding_digest") or ""
        )
        == str(task_request.get("origin_binding_digest") or "")
        and _rotation_terminal_is_present(events, event)
    ), None)


def _rotation_terminal_is_present(
    events: list[ZfEvent],
    rotation_event: ZfEvent,
) -> bool:
    payload = (
        rotation_event.payload
        if isinstance(rotation_event.payload, dict)
        else {}
    )
    request_id = str(payload.get("request_id") or "").strip()
    prior_request_id = str(payload.get("prior_request_id") or "").strip()
    terminal_event_id = str(
        payload.get("prior_terminal_event_id") or ""
    ).strip()
    if not prior_request_id or prior_request_id == request_id:
        return False
    prior_run_id = str(payload.get("prior_run_id") or "").strip()
    prior_identities = {
        value for value in (prior_request_id, prior_run_id) if value
    }
    rotation_index = next(
        (
            index
            for index, event in enumerate(events)
            if event.id == rotation_event.id
        ),
        len(events),
    )
    return any(
        event.id == terminal_event_id
        and event.type in RUN_TERMINAL_EVENT_TYPES
        and event.task_id == rotation_event.task_id
        and bool(
            prior_identities
            & {
                str(event.correlation_id or "").strip(),
                str(
                    (event.payload or {}).get("request_id") or ""
                ).strip(),
                str((event.payload or {}).get("run_id") or "").strip(),
                str(
                    (event.payload or {}).get("workflow_run_id") or ""
                ).strip(),
            }
        )
        for event in events[:rotation_index]
    )


__all__ = [
    "TaskRequestBindingDecision",
    "WorkflowTaskRequestRotationError",
    "accepted_task_request_rotation_event",
    "apply_task_request_binding",
    "fresh_task_request_origin_binding",
    "legacy_cli_origin_migration_allowed",
    "task_request_binding_decision",
    "terminal_retry_origin_binding",
    "terminal_task_request_rotation_context",
]
