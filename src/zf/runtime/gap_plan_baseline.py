"""Resolve gap amendments against the package that admitted their generation."""

from pathlib import Path
from typing import Any, Mapping

from zf.runtime.plan_artifact_package import (
    PlanArtifactPackageError,
    admitted_plan_artifact_package_for_generation,
    hydrate_plan_artifact_package,
    reduce_plan_artifact_packages,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def gap_plan_base_ref(
    *, state_dir: Path, events: list, payload: Mapping[str, Any],
    fallback_ref: str, required: bool,
) -> str:
    run_id = str(payload.get("workflow_run_id") or "")
    generation = str(payload.get("task_map_generation") or "")
    package = admitted_plan_artifact_package_for_generation(
        reduce_plan_artifact_packages(events, workflow_run_id=run_id),
        task_map_generation=generation,
    ) if run_id and generation else {}
    if not package:
        if required:
            raise PlanArtifactPackageError("gap amendment has no admitted baseline generation")
        return fallback_ref
    for key in ("id", "ref", "digest"):
        claimed = payload.get("plan_artifact_package_" + key)
        if claimed and claimed != package.get("package_" + key):
            raise PlanArtifactPackageError("gap amendment package binding mismatch: " + key)
    body = hydrate_plan_artifact_package(state_dir, {
        "ref": package["package_ref"], "sha256": package["package_digest"],
    })
    port = next((p for p in [*body["produced"], *body["inherited"]]
                 if p.get("logical_name") == "task_map"), None)
    if port is None:
        raise PlanArtifactPackageError("gap amendment baseline has no task_map port")
    hydrate_sidecar_ref(state_dir, port)
    return str(port["ref"])
