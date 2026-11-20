"""Bind an operator's proposed edit scope to the pending Plan, not execution."""
from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
from typing import Any

from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package
from zf.runtime.plan_preview_artifacts import preview_artifact_path


def validate_changed_task_ids(
    value: Any, *, request: Mapping[str, Any], state_dir: Path, project_root: Path,
) -> list[str]:
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError("changed_task_ids must be a non-empty list of task IDs")
    selected = sorted({item.strip() for item in value})
    package = hydrate_plan_artifact_package(state_dir, {
        "ref": request.get("plan_artifact_package_ref"),
        "sha256": request.get("plan_artifact_package_digest"),
    })
    if package.get("workflow_run_id") != request.get("workflow_run_id"):
        raise ValueError("changed task scope belongs to a different Plan run")
    ports = [port for port in [*package.get("produced", []), *package.get("inherited", [])]
             if port.get("logical_name") == "task_map"]
    if len(ports) != 1 or ports[0].get("ref") != request.get("task_map_ref"):
        raise ValueError("changed task scope requires the pending Plan task map")
    raw = preview_artifact_path(ports[0]["ref"], state_dir=state_dir,
                                project_root=project_root).read_bytes()
    if hashlib.sha256(raw).hexdigest() != ports[0].get("sha256"):
        raise ValueError("pending Plan task map digest differs")
    rows = json.loads(raw).get("tasks", [])
    known = {row.get("task_id") or row.get("id") for row in rows}
    if not set(selected) <= known:
        raise ValueError("changed_task_ids contains tasks outside the pending Plan")
    return selected
