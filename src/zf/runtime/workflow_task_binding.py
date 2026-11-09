"""Read-only Task identity projected from pinned Workflow inputs."""
from pathlib import Path
from typing import Any

from zf.core.task.store import TaskStore
from zf.runtime.workflow_inputs import hydrate_request_workflow_input_manifest
from zf.runtime.workflow_requests import WorkflowRequestError


def project_request_task_id(state_dir: Path, request: dict[str, Any]) -> str:
    if not request.get("workflow_input_manifest_ref"):
        task_id = str(request.get("task_id") or "")
        return task_id if task_id and TaskStore(state_dir / "kanban.json").get(task_id) else ""
    try:
        manifest = hydrate_request_workflow_input_manifest(state_dir, request)
    except (WorkflowRequestError, OSError, ValueError):
        return ""
    binding = manifest.get("task_input_binding")
    if not isinstance(binding, dict) or not binding.get("task_contract_digest"):
        return ""
    if manifest.get("project_id") and manifest["project_id"] != request.get("project_id"):
        return ""
    task_id = str(binding.get("task_id") or "")
    if request.get("task_id") and request["task_id"] != task_id:
        return ""
    return task_id if task_id and TaskStore(state_dir / "kanban.json").get(task_id) else ""
