"""Carry an unchanged acceptance contract into an explicitly linked gap plan."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


def rebind_inherited_gap_acceptance(
    *,
    state_dir: Path,
    project_root: Path,
    payload: Mapping[str, Any],
    workflow_run_id: str,
    flow_kind: str,
    explicit_ports: dict[str, dict[str, Any]],
    inherited: list[dict[str, Any]],
) -> None:
    from zf.runtime.artifact_read_ledger import materialize_attempt_source_ref
    from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package
    from zf.runtime.product_acceptance import (
        ProductAcceptanceError,
        validate_product_acceptance_spec,
    )
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    name = "product_acceptance_spec"
    if name in explicit_ports or payload.get("resume_scope") != "gap_tasks_only":
        return
    port = next((p for p in inherited if p.get("logical_name") == name), None)
    if port is None:
        return
    previous = hydrate_plan_artifact_package(state_dir, {
        "ref": port["source_package_ref"],
        "sha256": port["source_package_digest"],
    })
    prior_map = next((p for p in [*previous["produced"], *previous["inherited"]]
                      if p.get("logical_name") == "task_map"), {})
    amendment_base = materialize_attempt_source_ref(
        state_dir=state_dir, project_root=project_root,
        ref=str(payload.get("amend_of") or ""),
        source_id="gap-plan:amend-of", kind="task_map",
    ) if payload.get("amend_of") else None
    if (
        previous.get("workflow_run_id") != workflow_run_id
        or previous.get("flow_kind") != flow_kind
        or not amendment_base
        or amendment_base.get("sha256") != prior_map.get("sha256")
        or not payload.get("supersedes_task_map_generation")
        or payload["supersedes_task_map_generation"] != previous.get("task_map_generation")
    ):
        raise ProductAcceptanceError("gap acceptance inheritance requires exact prior plan lineage")
    spec = hydrate_sidecar_ref(state_dir, port).payload
    if not isinstance(spec, Mapping):
        raise ProductAcceptanceError("inherited Product Acceptance spec must be an object")
    validate_product_acceptance_spec(spec, expected={
        key: previous[key]
        for key in ("workflow_run_id", "flow_kind", "plan_revision", "task_map_generation")
    })
    # The ordinary explicit-port binder writes a new immutable spec containing
    # the same semantic body and the new plan identity; old reports stay stale.
    explicit_ports[name] = {
        **port,
        "rebound_from_ref": port["ref"],
        "rebound_from_digest": port["sha256"],
    }
    inherited.remove(port)
