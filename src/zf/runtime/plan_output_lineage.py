"""Content fence for reusing an admitted Plan generation."""

from pathlib import Path
from typing import Any, Mapping

from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_map import load_task_map, resolve_artifact_file


def package_task_map(state_dir: Path, package: Mapping[str, Any]) -> dict:
    port = next((p for p in [*package.get("produced", []), *package.get("inherited", [])]
                 if p.get("logical_name") == "task_map"), None)
    if not port:
        raise ValueError("admitted package has no canonical task_map port")
    return hydrate_sidecar_ref(state_dir, port).payload


def assert_package_task_map_matches(
    state_dir: Path, project_root: Path, package: Mapping[str, Any], payload: Mapping[str, Any],
) -> None:
    from zf.runtime.plan_artifact_package import PlanArtifactPackageError

    incoming = load_task_map(resolve_artifact_file(
        str(payload.get("task_map_ref") or ""), project_root=project_root, state_dir=state_dir))
    if incoming != package_task_map(state_dir, package):
        raise PlanArtifactPackageError(
            "task_map generation is already bound to different canonical content; "
            "new Plan output requires a fresh package identity")
