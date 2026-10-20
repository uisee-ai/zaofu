"""Bound, read-only artifact access for Plan review; no directory discovery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from zf.runtime.artifact_refs import resolve_runtime_artifact_ref

PLAN_REVIEW_REF_FIELDS = (
    "plan_artifact_ref", "plan_artifact_digest", "plan_ref",
    "plan_artifact_package_ref", "plan_artifact_package_digest",
    "source_index_ref", "workflow_run_id",
)


def preview_artifact_path(ref: str, *, state_dir: Path, project_root: Path | None) -> Path:
    path = resolve_runtime_artifact_ref(
        ref, state_dir=state_dir, project_root=project_root or state_dir.parent,
        search_workdirs=False,
    ).resolve()
    roots = [state_dir.resolve()]
    if project_root is not None:
        roots.append(project_root.resolve())
    if not any(path.is_relative_to(root) for root in roots):
        raise ValueError("artifact is outside the current project")
    return path


def read_preview_artifact(
    ref: str, *, state_dir: Path, project_root: Path | None, digest: str = "",
) -> str:
    path = preview_artifact_path(ref, state_dir=state_dir, project_root=project_root)
    # Fail explicitly instead of showing a truncated body as a complete Plan.
    with path.open("rb") as stream:
        body = stream.read(8 * 1024 * 1024 + 1)
    if len(body) > 8 * 1024 * 1024:
        raise ValueError("artifact exceeds the 8 MiB preview limit")
    if digest and hashlib.sha256(body).hexdigest() != digest:
        raise ValueError("artifact digest mismatch")
    return body.decode("utf-8")


def approval_artifact_refs(
    payload: Mapping[str, Any], *, state_dir: Path, project_root: Path,
) -> dict[str, Any]:
    refs = {key: payload[key] for key in PLAN_REVIEW_REF_FIELDS if payload.get(key)}
    ref = str(refs.get("plan_artifact_ref") or refs.get("plan_ref") or "")
    if ref and not refs.get("plan_artifact_digest"):
        try:
            body = read_preview_artifact(ref, state_dir=state_dir, project_root=project_root)
            refs["plan_artifact_digest"] = hashlib.sha256(body.encode()).hexdigest()
        except (OSError, ValueError):
            # Keep the declared ref: the projection must report it as unavailable.
            pass
    return refs


def preview_package_artifacts(
    payload: Mapping[str, Any], *, state_dir: Path, project_root: Path | None,
) -> tuple[list[dict[str, str]], str]:
    ref = str(payload.get("plan_artifact_package_ref") or "")
    if not ref:
        return [], ""
    digest = str(payload.get("plan_artifact_package_digest") or "")
    try:
        if not digest:
            raise ValueError("plan package digest missing")
        package = json.loads(read_preview_artifact(
            ref, state_dir=state_dir, project_root=project_root, digest=digest,
        ))
        from zf.runtime.plan_artifact_package import validate_plan_artifact_package_shape

        validate_plan_artifact_package_shape(package)
        if payload.get("workflow_run_id") and package["workflow_run_id"] != payload["workflow_run_id"]:
            raise ValueError("plan package workflow mismatch")
        ports = [*package["produced"], *package["inherited"]]
        task_map = next((p for p in ports if p["logical_name"] == "task_map"), None)
        if task_map and payload.get("task_map_ref"):
            expected = preview_artifact_path(str(payload["task_map_ref"]), state_dir=state_dir, project_root=project_root)
            actual = preview_artifact_path(task_map["ref"], state_dir=state_dir, project_root=project_root)
            if expected != actual:
                raise ValueError("plan package task map mismatch")
        return [{"name": p["logical_name"], "ref": p["ref"], "sha256": p["sha256"]} for p in ports], ""
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [], str(exc)
