"""Prepare immutable inputs for an approved Task-bound Workflow start."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zf.core.task.schema import Task
from zf.core.task.store import TERMINAL_STATES, TaskStore
from zf.runtime.task_workflow_plans import task_workflow_binding_digest
from zf.runtime.workflow_anchor import (
    is_workflow_managed_task,
    mark_workflow_managed_task,
)
from zf.runtime.workflow_request_acceptance import bind_task_workflow_inputs


@dataclass(frozen=True)
class PreparedWorkflowStart:
    parameters: dict[str, Any]
    workflow_task: Task | None
    common_payload: dict[str, Any]


def ensure_workflow_managed_task(
    *,
    state_dir: Path,
    workflow_task: Task | None,
    writer: Any,
    actor: str,
    causation_id: str,
    correlation_id: str,
) -> None:
    if workflow_task is None:
        return
    reopen_terminal = workflow_task.status in TERMINAL_STATES
    if is_workflow_managed_task(workflow_task) and not reopen_terminal:
        return
    mark_workflow_managed_task(workflow_task)
    from zf.runtime.task_contract_authority import (
        TaskContractAuthorityService,
    )

    store = TaskStore(state_dir / "kanban.json")
    TaskContractAuthorityService(
        task_store=store,
        event_writer=writer,
        state_dir=state_dir,
    ).replace(
        workflow_task,
        contract=workflow_task.contract,
        execution_binding=workflow_task.execution_binding,
        source="workflow_start",
        actor=actor,
        causation_id=causation_id,
        correlation_id=correlation_id,
        audit_payload={
            "contract_digest": task_workflow_binding_digest(workflow_task),
            "execution_owner": "workflow",
        },
        task_updates=(
            {
                "status": "backlog",
                "completed_at": None,
                "cancelled_at": None,
            }
            if reopen_terminal
            else None
        ),
        reopen_terminal=reopen_terminal,
    )


def prepare_approved_workflow_start(
    *,
    state_dir: Path,
    normalized: dict[str, Any],
    route: dict[str, Any],
    actor: str,
    reason: str,
) -> PreparedWorkflowStart:
    """Revalidate the approved preview and build its downstream payload."""

    task_id = str(normalized.get("task_id") or "")
    route_id = str(normalized.get("route_id") or "")
    objective = str(normalized.get("objective") or "")
    parameters = (
        dict(normalized.get("parameters"))
        if isinstance(normalized.get("parameters"), dict)
        else {}
    )
    workflow_task = TaskStore(state_dir / "kanban.json").get(task_id)
    if workflow_task is None:
        raise ValueError(f"workflow task {task_id!r} does not exist")
    expected_task_digest = str(
        normalized.get("task_contract_digest") or ""
    )
    if (
        not expected_task_digest
        or task_workflow_binding_digest(workflow_task)
        != expected_task_digest
    ):
        raise ValueError("workflow Task binding changed after preview")
    task_input_contract: dict[str, Any] = {}
    if isinstance(normalized.get("task_input_binding"), dict):
        parameters, _, task_input_contract, _ = bind_task_workflow_inputs(
            parameters,
            workflow_task,
            task_contract_digest=str(
                normalized.get("task_contract_digest") or ""
            ),
            prior_binding=normalized["task_input_binding"],
        )

    common_payload = {
        **parameters,
        "task_id": task_id,
        "objective": objective,
        "route_id": route_id,
        "pattern_id": str(route.get("entry_pattern_id") or ""),
        "requested_by": actor,
        "reason": reason or f"approved task workflow route {route_id}",
    }
    if isinstance(normalized.get("task_input_binding"), dict):
        common_payload["task_input_binding"] = dict(
            normalized["task_input_binding"]
        )
    if task_input_contract:
        common_payload["task_input_contract"] = task_input_contract
    for key in (
        "artifact_refs",
        "channel_id",
        "conversation_id",
        "fresh_request",
        "origin_binding",
        "prior_request_id",
        "prior_request_revision",
        "prior_terminal_event_id",
        "project_id",
        "request_id",
        "request_revision",
        "source_refs",
        "thread_id",
        "thread_key",
    ):
        value = normalized.get(key)
        if value not in (None, "", [], {}):
            common_payload[key] = value
    return PreparedWorkflowStart(
        parameters=parameters,
        workflow_task=workflow_task,
        common_payload=common_payload,
    )


__all__ = [
    "PreparedWorkflowStart",
    "ensure_workflow_managed_task",
    "prepare_approved_workflow_start",
]
