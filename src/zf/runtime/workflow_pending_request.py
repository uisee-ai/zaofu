"""Read-only proof that a pre-Run Request belongs to an unbound Task."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.events.factory import event_log_from_project
from zf.core.task.schema import Task
from zf.core.task.store import TERMINAL_STATES
from zf.runtime.run_admission import request_admission_view
from zf.runtime.task_workflow_plans import task_workflow_binding_digest
from zf.runtime.workflow_requests import (
    WorkflowRequestError,
    hydrate_workflow_requirement,
    require_current_workflow_request,
)


def pending_request_for_task(
    state_dir: Path,
    *,
    config: ZfConfig | None,
    task: Task,
    request_id: str,
    revision: int,
) -> dict[str, Any]:
    """Permit a new approval preview, never adopt or launch the Request here."""
    request = require_current_workflow_request(state_dir, request_id, revision)
    if request.get("status") not in {"draft", "clarifying", "ready", "proposed"}:
        raise WorkflowRequestError("pending Request is no longer editable")
    if (
        task.status in TERMINAL_STATES
        or task.active_dispatch_id
        or task.execution_binding.workflow_run_id
    ):
        raise WorkflowRequestError("pending Request requires an idle nonterminal Task")
    requirement = hydrate_workflow_requirement(state_dir, request)
    binding = requirement.get("task_input_binding")
    if not isinstance(binding, dict) or (
        binding.get("task_id") != task.id
        or binding.get("task_contract_digest") != task_workflow_binding_digest(task)
    ):
        raise WorkflowRequestError(
            "pending Request immutable Task binding is stale or missing"
        )
    log = event_log_from_project(state_dir, config=config)
    try:
        events = log.read_all()
        admission = request_admission_view(
            events,
            request_id=request_id,
            run_id=str(request.get("run_id") or request_id),
        )
    finally:
        log.close()
    if admission.get("status") or any(
        event.type == "workflow.invoke.requested"
        and request_id == str(event.payload.get("request_id") or "")
        for event in events
    ):
        raise WorkflowRequestError("pending Request already has Run admission history")
    return request
