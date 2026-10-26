"""Explicit task partitions of immutable semantic inputs, not task state."""

from __future__ import annotations

from typing import Any, Mapping
from pathlib import Path

SCHEMA_VERSION = "task-scoped-plan-input.v1"


def task_input_paths(body: Any, *, task_id: str, candidate: bool = False) -> list[str]:
    """Keep legacy inputs whole; select only producer-declared partitions."""
    if not isinstance(body, Mapping) or body.get("schema_version") != SCHEMA_VERSION:
        return ["$"]
    if set(body) != {"schema_version", "shared", "tasks"}:
        raise ValueError("task-scoped input requires exactly schema_version, shared, tasks")
    if not isinstance(body["shared"], Mapping) or not isinstance(body["tasks"], list):
        raise ValueError("task-scoped input requires shared object and tasks list")
    positions = {}
    for index, row in enumerate(body["tasks"]):
        if not isinstance(row, Mapping) or set(row) != {"task_id", "body"}:
            raise ValueError("task-scoped input row requires task_id and body")
        key = row["task_id"]
        if not isinstance(key, str) or not key.strip() or key in positions:
            raise ValueError("task-scoped input task ids must be non-empty and unique")
        if not isinstance(row["body"], Mapping):
            raise ValueError("task-scoped input body must be an object")
        positions[key] = index
    if candidate:
        return ["$"]
    if task_id not in positions:
        raise ValueError(f"task-scoped input has no partition for {task_id!r}")
    return ["$.shared", f"$.tasks[{positions[task_id]}]"]


def current_plan_port_sources(
    *,
    state_dir: Path,
    workflow_run_id: str,
    task_id: str,
    currentness: Mapping[str, Any],
    allow_without_task: bool = False,
    candidate: bool = False,
) -> list[dict[str, Any]]:
    from zf.core.task.store import TaskStore
    from zf.runtime.artifact_read_ledger import ArtifactReadError
    from zf.runtime.plan_artifact_package import PlanArtifactPackageError, hydrate_plan_artifact_package
    from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref

    task = TaskStore(state_dir / "kanban.json").get(task_id) if task_id else None
    if task is None and not allow_without_task:
        return []
    evidence = (
        task.contract.evidence_contract
        if task is not None
        and isinstance(task.contract.evidence_contract, dict)
        else {}
    )
    declared = _declared_required_ports(
        evidence.get("required_plan_ports")
    )
    package_ref = str(currentness.get("plan_artifact_package_ref") or "")
    package_digest = str(currentness.get("plan_artifact_package_digest") or "")
    if not package_ref or not package_digest:
        if declared:
            raise ArtifactReadError(
                "task contract declares required_plan_ports but no current "
                "Plan Artifact Package is bound"
            )
        return []
    try:
        package = hydrate_plan_artifact_package(
            state_dir,
            {"ref": package_ref, "sha256": package_digest},
        )
    except (PlanArtifactPackageError, SidecarRefError) as exc:
        raise ArtifactReadError(
            f"current Plan Artifact Package cannot be hydrated: {exc}"
        ) from exc
    expected = {
        "workflow_run_id": workflow_run_id,
        "task_map_generation": str(
            currentness.get("task_map_generation") or ""
        ),
    }
    for field, value in expected.items():
        if value and str(package.get(field) or "") != value:
            raise ArtifactReadError(
                f"current Plan Artifact Package {field} mismatch: "
                f"expected {value!r}, got {package.get(field)!r}"
            )
    package_required = _declared_required_ports(
        package.get("required_ports")
    )
    missing_declarations = sorted(set(declared) - set(package_required))
    if missing_declarations:
        raise ArtifactReadError(
            "current Plan Artifact Package does not bind task required ports: "
            + ", ".join(missing_declarations)
        )
    required = list(dict.fromkeys([*package_required, *declared]))
    ports = {
        str(item.get("logical_name") or ""): item
        for group in ("produced", "inherited")
        for item in package.get(group, [])
        if isinstance(item, Mapping)
    }
    missing = [name for name in required if name not in ports]
    if missing:
        raise ArtifactReadError(
            "current Plan Artifact Package is missing required ports: "
            + ", ".join(missing)
        )

    paths = {}
    kinds = {}
    for name in required:
        try:
            body = hydrate_sidecar_ref(state_dir, {
                "ref": ports[name]["ref"], "sha256": ports[name]["sha256"],
            }).payload
            paths[name] = task_input_paths(body, task_id=task_id, candidate=candidate)
            kinds[name] = (
                "task_scoped_plan_input"
                if isinstance(body, Mapping) and body.get("schema_version") == SCHEMA_VERSION
                else str(ports[name].get("artifact_kind") or name)
            )
        except ValueError as exc:
            raise ArtifactReadError(f"invalid task input port {name!r}: {exc}") from exc
    return [
        {
            "source_id": f"plan-port-{name}",
            "artifact_id": name,
            "kind": kinds[name],
            "ref": str(ports[name].get("ref") or ""),
            "sha256": str(ports[name].get("sha256") or ""),
            "allowed_paths": paths[name],
        }
        for name in required
    ]


def _declared_required_ports(value: Any) -> list[str]:
    from zf.runtime.artifact_read_ledger import ArtifactReadError
    from zf.runtime.plan_artifact_ports import canonical_plan_port_name

    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ArtifactReadError("required_plan_ports must be a list")
    ports: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ArtifactReadError(
                "required_plan_ports entries must be non-empty strings"
            )
        name = canonical_plan_port_name(item)
        if name in ports:
            raise ArtifactReadError(
                f"duplicate required_plan_ports entry: {name}"
            )
        ports.append(name)
    return ports
