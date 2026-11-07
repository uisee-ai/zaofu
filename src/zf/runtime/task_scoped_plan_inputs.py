"""Explicit task partitions of immutable semantic inputs, not task state."""

from __future__ import annotations

from typing import Any, Mapping
from pathlib import Path

SCHEMA_VERSION = "task-scoped-plan-input.v1"


class MissingTaskPartitionError(ValueError):
    """A well-formed scoped input has no partition for the requested task."""


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
        raise MissingTaskPartitionError(f"task-scoped input has no partition for {task_id!r}")
    return ["$.shared", f"$.tasks[{positions[task_id]}]"]


def _pinned_predecessor(state_dir: Path, ports: Mapping[str, Mapping[str, Any]], task_id: str) -> str:
    """Return an explicitly replaced task, only for an admitted gap task map."""
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    port = ports.get("task_map")
    if not port:
        raise ValueError("replacement input requires pinned task_map")
    task_map = hydrate_sidecar_ref(state_dir, {"ref": port.get("ref"), "sha256": port.get("sha256")}).payload
    return _task_map_predecessor(task_map, task_id)


def _task_map_predecessor(task_map: Any, task_id: str) -> str:
    """Check an explicit replacement edge in a pinned or candidate Task Map."""
    if not isinstance(task_map, Mapping) or not isinstance(task_map.get("tasks"), list):
        raise ValueError("replacement input requires a valid task_map")
    tasks = [row for row in task_map["tasks"] if isinstance(row, Mapping)]
    matches = [row for row in tasks if str(row.get("task_id") or row.get("id") or "") == task_id]
    amend = task_map.get("amend")
    if (len(matches) != 1 or not isinstance(amend, Mapping)
            or amend.get("kind") not in {"goal_gap", "module_parity_gap"}
            or task_id not in (amend.get("gap_task_ids") or [])):
        raise ValueError("replacement input requires an unambiguous gap task")
    task = matches[0]
    payload = task.get("payload")
    predecessors = payload.get("supersedes_task_ids") if isinstance(payload, Mapping) else None
    evidence = task.get("evidence_contract")
    evidence_predecessors = evidence.get("supersedes_task_ids") if isinstance(evidence, Mapping) else None
    if not isinstance(predecessors, list) or len(predecessors) != 1 or not isinstance(predecessors[0], str) or not predecessors[0].strip():
        raise ValueError("replacement input requires one explicit predecessor")
    if evidence_predecessors is not None and evidence_predecessors != predecessors:
        raise ValueError("replacement input predecessor declarations conflict")
    predecessor = predecessors[0]
    if predecessor not in (amend.get("superseded_task_ids") or []) or any(
        str(row.get("task_id") or row.get("id") or "") == predecessor for row in tasks
    ):
        raise ValueError("replacement input predecessor is not superseded")
    return predecessor


def _verify_predecessor_provenance(
    state_dir: Path, *, port: Mapping[str, Any], predecessor: str, workflow_run_id: str,
) -> dict[str, Mapping[str, Any]]:
    """Verify the inherited body and predecessor against the package ancestry."""
    from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    ref, digest = str(port.get("source_package_ref") or ""), str(port.get("source_package_digest") or "")
    seen: set[str] = set()
    first = True
    while ref and digest and ref not in seen and len(seen) < 32:
        seen.add(ref)
        package = hydrate_plan_artifact_package(state_dir, {"ref": ref, "sha256": digest})
        if package.get("workflow_run_id") != workflow_run_id:
            raise ValueError("predecessor package belongs to another run")
        ports = [*package.get("produced", []), *package.get("inherited", [])]
        if first and not any(
            item.get("logical_name") == port.get("logical_name")
            and item.get("ref") == port.get("ref") and item.get("sha256") == port.get("sha256")
            for item in ports if isinstance(item, Mapping)
        ):
            raise ValueError("predecessor source port is not inherited from its package")
        first = False
        inherited_port = next((item for item in ports if isinstance(item, Mapping)
                               and item.get("logical_name") == port.get("logical_name")), None)
        if (inherited_port is None or inherited_port.get("ref") != port.get("ref")
                or inherited_port.get("sha256") != port.get("sha256")):
            raise ValueError("predecessor input changed in package ancestry")
        task_port = next((item for item in ports if isinstance(item, Mapping) and item.get("logical_name") == "task_map"), None)
        if task_port:
            task_map = hydrate_sidecar_ref(state_dir, {"ref": task_port["ref"], "sha256": task_port["sha256"]}).payload
            if isinstance(task_map, Mapping) and any(
                isinstance(task, Mapping) and str(task.get("task_id") or task.get("id") or "") == predecessor
                for task in task_map.get("tasks", [])
            ):
                return {str(item["logical_name"]): item for item in ports if isinstance(item, Mapping)}
        ref, digest = str(package.get("supersedes_package_ref") or ""), str(package.get("supersedes_package_digest") or "")
    raise ValueError("predecessor is absent from package ancestry")


def _predecessor_input_paths(
    state_dir: Path, *, ports: Mapping[str, Mapping[str, Any]], name: str,
    body: Any, task_id: str, workflow_run_id: str,
    initial_task_map: Mapping[str, Any] | None = None,
) -> tuple[list[str], str]:
    """Follow explicit replacement edges without widening the original input."""
    seen = {task_id}
    for _ in range(32):
        predecessor = (_task_map_predecessor(initial_task_map, task_id)
                       if initial_task_map is not None else _pinned_predecessor(state_dir, ports, task_id))
        initial_task_map = None
        if predecessor in seen:
            raise ValueError("replacement input ancestry contains a cycle")
        seen.add(predecessor)
        ports = _verify_predecessor_provenance(
            state_dir, port=ports[name], predecessor=predecessor,
            workflow_run_id=workflow_run_id,
        )
        try:
            return task_input_paths(body, task_id=predecessor), predecessor
        except MissingTaskPartitionError:
            task_id = predecessor
    raise ValueError("replacement input ancestry exceeds supported depth")


def current_plan_port_sources(
    *,
    state_dir: Path,
    workflow_run_id: str,
    task_id: str,
    currentness: Mapping[str, Any],
    allow_without_task: bool = False,
    candidate: bool = False,
    profile: str = "",
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
    required = list(dict.fromkeys([*package_required, *declared]))
    ports = {
        str(item.get("logical_name") or ""): item
        for group in ("produced", "inherited")
        for item in package.get(group, [])
        if isinstance(item, Mapping)
    }
    missing_declarations = sorted(set(declared) - set(ports))
    if missing_declarations:
        raise ArtifactReadError(
            "current Plan Artifact Package does not bind task required ports: "
            + ", ".join(missing_declarations)
        )
    missing = [name for name in required if name not in ports]
    if missing:
        raise ArtifactReadError(
            "current Plan Artifact Package is missing required ports: "
            + ", ".join(missing)
        )

    if not candidate and profile in {"implementation", "task-verify"}:
        from zf.runtime.task_plan_consumption import selected_plan_ports

        try:
            selection = selected_plan_ports(
                state_dir=state_dir, task_id=task_id, profile=profile,
                evidence=evidence, ports=ports, required=required,
            )
        except (ValueError, SidecarRefError) as exc:
            raise ArtifactReadError(f"invalid task plan input selection: {exc}") from exc
        if selection is not None:
            required = selection
    else:
        selection = None

    paths = {}
    kinds = {}
    predecessors: dict[str, str] = {}
    inherited_names = {str(item.get("logical_name") or "") for item in package.get("inherited", [])}
    for name in required:
        try:
            body = hydrate_sidecar_ref(state_dir, {
                "ref": ports[name]["ref"], "sha256": ports[name]["sha256"],
            }).payload
            try:
                paths[name] = task_input_paths(body, task_id=task_id, candidate=candidate)
            except MissingTaskPartitionError:
                if candidate or profile not in {"implementation", "task-verify"} or name not in inherited_names:
                    raise
                paths[name], predecessors[name] = _predecessor_input_paths(
                    state_dir, ports=ports, name=name, body=body, task_id=task_id,
                    workflow_run_id=workflow_run_id,
                )
            kinds[name] = (
                "task_scoped_plan_input"
                if isinstance(body, Mapping) and body.get("schema_version") == SCHEMA_VERSION
                else str(ports[name].get("artifact_kind") or name)
            )
            if selection is not None and name == "task_execution_input" and kinds[name] != "task_scoped_plan_input":
                raise ValueError("selected task_execution_input must be task scoped")
        except ValueError as exc:
            raise ArtifactReadError(f"invalid task input port {name!r}: {exc}") from exc
    return [
        {
            "source_id": f"plan-port-{name}" + (f"-predecessor-{predecessors[name]}" if name in predecessors else ""),
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
