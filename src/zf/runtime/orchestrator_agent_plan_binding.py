"""Resolve OA plan authority from the admitted scope being reviewed."""

from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.plan_artifact_package import (
    admitted_plan_artifact_package_for_generation,
    reduce_plan_artifact_packages,
)
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def current_checkpoint_plan_package(
    events: list[ZfEvent], *, workflow_run_id: str, task_id: str = "",
) -> dict[str, Any]:
    """Retained Tasks keep their admitted package until their own readmission."""
    projection = reduce_plan_artifact_packages(events, workflow_run_id=workflow_run_id)
    context = task_pipeline_generation_contexts(events).get(task_id)
    if context is None:
        return dict(projection.get("current") or {})
    if context.get("workflow_run_id") != workflow_run_id:
        return {}
    package = admitted_plan_artifact_package_for_generation(
        projection, task_map_generation=str(context.get("task_map_generation") or ""),
    )
    for key in ("id", "ref", "digest"):
        binding = context.get("plan_artifact_package_" + key)
        if binding and binding != package.get("package_" + key):
            return {}
    return package


def semantic_failure_target_task_id(state_dir: Path, request: Mapping[str, Any]) -> str:
    if request.get("checkpoint") != "semantic_failure":
        return ""
    checkpoint_input = hydrate_sidecar_ref(state_dir, request["checkpoint_input_ref"]).payload
    return str(checkpoint_input.get("checkpoint_context", {}).get("target_task_id") or "")


def operation_checkpoint_plan_package(
    state_dir: Path, events: list[ZfEvent], operation: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Recover scope from the immutable request, never from a worker's result."""
    if operation.get("operation_type") != "orchestrator_agent_semantic":
        return None
    stored = hydrate_sidecar_ref(state_dir, operation["request_ref"]).payload
    request = stored["request"]
    task_id = semantic_failure_target_task_id(state_dir, request)
    if not task_id:
        return None
    return current_checkpoint_plan_package(
        events, workflow_run_id=str(request.get("workflow_run_id") or ""), task_id=task_id,
    )
