"""Mechanical lifecycle convergence for workflow-managed parent tasks."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TERMINAL_STATES, TaskStore
from zf.runtime.task_workflow_plans import task_workflow_binding_digest
from zf.runtime.workflow_anchor import (
    is_workflow_managed_task,
    mark_workflow_managed_task,
    workflow_task_request_binding,
)
from zf.runtime.workflow_task_request_rotation import (
    accepted_task_request_rotation_event,
)


WORKFLOW_TASK_ACTIVATION_SOURCE = "workflow_invoke_admission"
RESEARCH_TASK_COMPLETION_SOURCE = "research_artifact_delivery"
WORKFLOW_TASK_TERMINAL_SOURCE = "workflow_run_terminal"
WORKFLOW_TASK_REOPEN_SOURCE = "workflow_run_reopened"


def claim_submitted_workflow_task(
    *,
    state_dir: Path,
    task_id: str,
    writer: EventWriter,
    source_event: ZfEvent,
    actor: str,
) -> None:
    """Reserve an existing parent Task for workflow-owned dispatch."""

    if not task_id:
        return
    store = TaskStore(state_dir / "kanban.json")
    task = store.get(task_id)
    if task is None:
        return
    was_managed = is_workflow_managed_task(task)
    previous_assigned_to = str(task.assigned_to or "")
    previous_dispatch_id = str(task.active_dispatch_id or "")
    if was_managed and not previous_assigned_to and not previous_dispatch_id:
        return
    if not was_managed:
        mark_workflow_managed_task(task)
    from zf.runtime.task_contract_authority import (
        TaskContractAuthorityService,
    )

    TaskContractAuthorityService(
        task_store=store,
        event_writer=writer,
        state_dir=state_dir,
    ).replace(
        task,
        contract=task.contract,
        execution_binding=task.execution_binding,
        source="workflow_submit",
        actor=actor,
        causation_id=source_event.id,
        correlation_id=source_event.correlation_id,
        task_updates={"assigned_to": None, "active_dispatch_id": ""},
        audit_payload={
            "contract_digest": task_workflow_binding_digest(task),
            "execution_owner": "workflow",
            "previous_assigned_to": previous_assigned_to,
            "assignment_released": bool(previous_assigned_to),
            "previous_dispatch_id": previous_dispatch_id,
        },
    )


def activate_workflow_managed_task(
    *,
    task_store: TaskStore,
    event_writer: EventWriter,
    accepted_event: ZfEvent,
) -> Task | None:
    """Project an admitted Workflow Run onto its parent Task exactly once."""

    if accepted_event.type != "workflow.invoke.accepted":
        raise ValueError("accepted_event must be workflow.invoke.accepted")
    payload = (
        accepted_event.payload
        if isinstance(accepted_event.payload, dict)
        else {}
    )
    task_id = str(
        accepted_event.task_id or payload.get("task_id") or ""
    ).strip()
    task = task_store.get(task_id) if task_id else None
    if task is None or not is_workflow_managed_task(task):
        return None
    rotation_event = None
    if task.status == "blocked":
        events = event_writer.event_log.read_all()
        rotation_event = accepted_task_request_rotation_event(
            events,
            task,
            accepted_event,
        )
        if rotation_event is None:
            return None
    elif task.status != "backlog":
        return None

    existing = _lifecycle_event(
        event_writer,
        event_type="task.status_changed",
        task_id=task_id,
        source=WORKFLOW_TASK_ACTIVATION_SOURCE,
        trigger_event_id=accepted_event.id,
    )
    if existing is None:
        event_writer.append(ZfEvent(
            type="task.status_changed",
            actor="zf-cli",
            task_id=task_id,
            causation_id=accepted_event.id,
            correlation_id=accepted_event.correlation_id,
            payload={
                "from": task.status,
                "to": "in_progress",
                "source": WORKFLOW_TASK_ACTIVATION_SOURCE,
                "trigger_event": accepted_event.type,
                "trigger_event_id": accepted_event.id,
                "workflow_run_id": str(
                    payload.get("workflow_run_id")
                    or payload.get("run_id")
                    or ""
                ),
                "pattern_id": str(payload.get("pattern_id") or ""),
                "workflow_request_rotation_event_id": str(
                    rotation_event.id if rotation_event is not None else ""
                ),
                "previous_assigned_to": str(task.assigned_to or ""),
                "assignment_released": bool(task.assigned_to),
            },
        ))
    return task_store.update(
        task_id,
        status="in_progress",
        assigned_to=None,
        active_dispatch_id="",
        blocked_reason="",
        started_at=task.started_at or accepted_event.ts,
    )


def complete_standalone_research_task(
    *,
    task_store: TaskStore,
    event_writer: EventWriter,
    result_event: ZfEvent,
) -> Task | None:
    """Close one standalone Research Task after verified artifact delivery.

    Request-bound tasks are preparatory multi-run parents. Research updates
    their Request/Channel but does not close the parent delivery task.
    """

    payload = (
        result_event.payload
        if isinstance(result_event.payload, dict)
        else {}
    )
    if (
        result_event.type != "workflow.result.available"
        or str(payload.get("result_kind") or "") != "research_report"
        or str(payload.get("status") or "") != "available"
    ):
        return None
    task_id = str(
        result_event.task_id or payload.get("task_id") or ""
    ).strip()
    task = task_store.get(task_id) if task_id else None
    if (
        task is None
        or task.status in TERMINAL_STATES
        or task.status != "in_progress"
        or not is_workflow_managed_task(task)
        or workflow_task_request_binding(task)
    ):
        return None
    terminal, descriptor = _verified_research_lineage(
        event_writer,
        result_event=result_event,
        task_id=task_id,
    )
    if terminal is None or descriptor is None:
        return None

    status_event = _lifecycle_event(
        event_writer,
        event_type="task.status_changed",
        task_id=task_id,
        source=RESEARCH_TASK_COMPLETION_SOURCE,
        trigger_event_id=result_event.id,
    )
    if status_event is None:
        status_event = event_writer.append(ZfEvent(
            type="task.status_changed",
            actor="zf-cli",
            task_id=task_id,
            causation_id=result_event.id,
            correlation_id=result_event.correlation_id,
            payload={
                "from": task.status,
                "to": "done",
                "source": RESEARCH_TASK_COMPLETION_SOURCE,
                "trigger_event": result_event.type,
                "trigger_event_id": result_event.id,
                "terminal_event_id": terminal.id,
                "workflow_run_id": str(
                    payload.get("workflow_run_id") or ""
                ),
                "artifact_ref": str(payload.get("artifact_ref") or ""),
                "artifact_digest": str(
                    payload.get("artifact_digest") or ""
                ),
            },
        ))
    evidence_event = _lifecycle_event(
        event_writer,
        event_type="task.done.evidence",
        task_id=task_id,
        source=RESEARCH_TASK_COMPLETION_SOURCE,
        trigger_event_id=result_event.id,
    )
    if evidence_event is None:
        event_writer.append(ZfEvent(
            type="task.done.evidence",
            actor="zf-cli",
            task_id=task_id,
            causation_id=status_event.id,
            correlation_id=result_event.correlation_id,
            payload={
                "source": RESEARCH_TASK_COMPLETION_SOURCE,
                "trigger_event": result_event.type,
                "trigger_event_id": result_event.id,
                "terminal_event_id": terminal.id,
                "artifact_ref": str(descriptor.get("ref") or ""),
                "artifact_digest": str(
                    descriptor.get("sha256") or ""
                ).removeprefix("sha256:"),
                "artifact_schema_version": str(
                    descriptor.get("schema_version") or ""
                ),
                "evidence_refs": [
                    str(descriptor.get("ref") or ""),
                    result_event.id,
                    terminal.id,
                ],
            },
        ))
    return task_store.update(
        task_id,
        status="done",
        completed_at=result_event.ts,
    )


def settle_workflow_managed_task_from_run_terminal(
    *,
    task_store: TaskStore,
    event_writer: EventWriter,
    terminal_event: ZfEvent,
) -> Task | None:
    """Project kernel-admitted Run goal truth onto its parent Kanban Task."""

    if terminal_event.type not in {
        "run.goal.completed",
        "run.goal.blocked",
        "run.failed",
    }:
        return None
    payload = (
        terminal_event.payload
        if isinstance(terminal_event.payload, dict)
        else {}
    )
    workflow_run_id = str(
        payload.get("workflow_run_id")
        or payload.get("run_id")
        or terminal_event.correlation_id
        or ""
    ).strip()
    task_id = str(
        terminal_event.task_id or payload.get("parent_task_id") or ""
    ).strip()
    if not task_id and workflow_run_id:
        from zf.runtime.workflow_lineage import resolve_workflow_run_lineage

        task_id = resolve_workflow_run_lineage(
            event_writer.event_log.read_all(),
            workflow_run_id,
        ).parent_task_id
    if terminal_event.type == "run.goal.blocked" and _blocked_goal_was_recovered(
        event_writer, terminal_event, workflow_run_id,
    ):
        return None
    task = task_store.get(task_id) if task_id else None
    if (
        task is None
        or task.status in TERMINAL_STATES
        or not is_workflow_managed_task(task)
    ):
        return None
    target_status = (
        "done" if terminal_event.type == "run.goal.completed" else "blocked"
    )
    status_event = _lifecycle_event(
        event_writer,
        event_type="task.status_changed",
        task_id=task_id,
        source=WORKFLOW_TASK_TERMINAL_SOURCE,
        trigger_event_id=terminal_event.id,
    )
    if status_event is None:
        status_event = event_writer.append(ZfEvent(
            type="task.status_changed",
            actor="zf-cli",
            task_id=task_id,
            payload={
                "from": task.status,
                "to": target_status,
                "source": WORKFLOW_TASK_TERMINAL_SOURCE,
                "trigger_event": terminal_event.type,
                "trigger_event_id": terminal_event.id,
                "workflow_run_id": workflow_run_id,
                "reason": str(
                    payload.get("reason") or payload.get("summary") or ""
                ),
            },
            causation_id=terminal_event.id,
            correlation_id=workflow_run_id or terminal_event.correlation_id,
        ))
    if target_status == "done" and _lifecycle_event(
        event_writer,
        event_type="task.done.evidence",
        task_id=task_id,
        source=WORKFLOW_TASK_TERMINAL_SOURCE,
        trigger_event_id=terminal_event.id,
    ) is None:
        evidence_refs = [terminal_event.id]
        evidence_refs.extend(
            str(item)
            for item in payload.get("evidence_refs") or []
            if str(item).strip()
        )
        event_writer.append(ZfEvent(
            type="task.done.evidence",
            actor="zf-cli",
            task_id=task_id,
            payload={
                "source": WORKFLOW_TASK_TERMINAL_SOURCE,
                "trigger_event": terminal_event.type,
                "trigger_event_id": terminal_event.id,
                "workflow_run_id": workflow_run_id,
                "evidence_refs": list(dict.fromkeys(evidence_refs)),
            },
            causation_id=status_event.id,
            correlation_id=workflow_run_id or terminal_event.correlation_id,
        ))
    updates = {
        "status": target_status,
        "assigned_to": "",
        "active_dispatch_id": "",
        "blocked_reason": (
            str(payload.get("reason") or payload.get("summary") or terminal_event.type)
            if target_status == "blocked"
            else ""
        ),
    }
    if target_status == "done":
        updates["completed_at"] = terminal_event.ts
    return task_store.update(
        task_id,
        **updates,
    )


def _blocked_goal_was_recovered(
    writer: EventWriter, terminal_event: ZfEvent, workflow_run_id: str,
) -> bool:
    """Do not let replay of an older blocked goal undo its later recovery."""
    if not workflow_run_id:
        return False
    from zf.runtime.run_scope import event_run_id, run_aliases

    events = writer.event_log.read_all()
    aliases = run_aliases(events)
    run_id = aliases.get(workflow_run_id, workflow_run_id)
    seen_terminal = False
    for event in events:
        if event.id == terminal_event.id:
            seen_terminal = True
            continue
        if not seen_terminal or event.type != "run.goal.updated":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            str(payload.get("status") or "").strip() in {"active", "running"}
            and event_run_id(event, aliases=aliases) == run_id
        ):
            return True
    return False


def reopen_workflow_managed_task_from_run_recovery(
    *,
    task_store: TaskStore,
    event_writer: EventWriter,
    recovery_event: ZfEvent,
) -> Task | None:
    """Project an authorized blocked-Run recovery onto its parent Task."""

    if recovery_event.type != "run.goal.updated":
        raise ValueError("recovery_event must be run.goal.updated")
    payload = (
        recovery_event.payload
        if isinstance(recovery_event.payload, dict)
        else {}
    )
    if str(payload.get("status") or "").strip() not in {"active", "running"}:
        return None
    workflow_run_id = str(
        payload.get("workflow_run_id")
        or payload.get("run_id")
        or recovery_event.correlation_id
        or ""
    ).strip()
    task_id = str(
        recovery_event.task_id or payload.get("parent_task_id") or ""
    ).strip()
    if not task_id and workflow_run_id:
        from zf.runtime.workflow_lineage import resolve_workflow_run_lineage

        task_id = resolve_workflow_run_lineage(
            event_writer.event_log.read_all(),
            workflow_run_id,
        ).parent_task_id
    task = task_store.get(task_id) if task_id else None
    if task is None or not is_workflow_managed_task(task):
        return None
    if task.status == "in_progress":
        return task
    if task.status not in {"backlog", "blocked"}:
        return None
    if _lifecycle_event(
        event_writer,
        event_type="task.status_changed",
        task_id=task_id,
        source=WORKFLOW_TASK_REOPEN_SOURCE,
        trigger_event_id=recovery_event.id,
    ) is None:
        event_writer.append(ZfEvent(
            type="task.status_changed",
            actor="zf-cli",
            task_id=task_id,
            payload={
                "from": task.status,
                "to": "in_progress",
                "source": WORKFLOW_TASK_REOPEN_SOURCE,
                "trigger_event": recovery_event.type,
                "trigger_event_id": recovery_event.id,
                "workflow_run_id": workflow_run_id,
                "stage_replan_generation": str(
                    payload.get("stage_replan_generation") or ""
                ),
            },
            causation_id=recovery_event.id,
            correlation_id=workflow_run_id or recovery_event.correlation_id,
        ))
    return task_store.update(
        task_id,
        status="in_progress",
        blocked_reason="",
        assigned_to="",
        active_dispatch_id="",
    )


def _verified_research_lineage(
    event_writer: EventWriter,
    *,
    result_event: ZfEvent,
    task_id: str,
) -> tuple[ZfEvent | None, dict[str, object] | None]:
    payload = (
        result_event.payload
        if isinstance(result_event.payload, dict)
        else {}
    )
    terminal_event_id = str(
        payload.get("terminal_event_id")
        or result_event.causation_id
        or ""
    ).strip()
    if (
        not terminal_event_id
        or result_event.causation_id != terminal_event_id
    ):
        return None, None
    terminal = next(
        (
            event
            for event in reversed(event_writer.event_log.read_all())
            if event.id == terminal_event_id
        ),
        None,
    )
    terminal_payload = (
        terminal.payload
        if terminal is not None
        and isinstance(terminal.payload, dict)
        else {}
    )
    if (
        terminal is None
        or terminal.type != "fanout.aggregate.completed"
        or str(terminal_payload.get("status") or "") != "completed"
    ):
        return None, None
    expected_ref = str(payload.get("artifact_ref") or "").strip()
    expected_digest = str(
        payload.get("artifact_digest") or ""
    ).removeprefix("sha256:").lower()
    descriptor = next(
        (
            dict(item)
            for item in terminal_payload.get("artifact_refs") or []
            if isinstance(item, Mapping)
            and str(item.get("kind") or "") == "research_report"
            and str(item.get("ref") or item.get("path") or "")
            == expected_ref
            and str(
                item.get("sha256")
                or item.get("hash")
                or ""
            ).removeprefix("sha256:").lower()
            == expected_digest
            and str(item.get("task_id") or "") == task_id
        ),
        None,
    )
    return terminal, descriptor


def _lifecycle_event(
    event_writer: EventWriter,
    *,
    event_type: str,
    task_id: str,
    source: str,
    trigger_event_id: str,
) -> ZfEvent | None:
    return next(
        (
            event
            for event in reversed(event_writer.event_log.read_all())
            if event.type == event_type
            and event.task_id == task_id
            and str((event.payload or {}).get("source") or "") == source
            and str(
                (event.payload or {}).get("trigger_event_id") or ""
            )
            == trigger_event_id
        ),
        None,
    )


__all__ = [
    "RESEARCH_TASK_COMPLETION_SOURCE",
    "WORKFLOW_TASK_ACTIVATION_SOURCE",
    "WORKFLOW_TASK_REOPEN_SOURCE",
    "WORKFLOW_TASK_TERMINAL_SOURCE",
    "activate_workflow_managed_task",
    "complete_standalone_research_task",
    "reopen_workflow_managed_task_from_run_recovery",
    "settle_workflow_managed_task_from_run_terminal",
]
