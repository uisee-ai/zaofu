"""Prove candidate input inheritance with the same explicit runtime lineage."""
from pathlib import Path
from typing import Any, Mapping

from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_scoped_plan_inputs import _predecessor_input_paths


def validate_dispatch_input_partitions(
    *, state_dir: Path, task_map: Mapping[str, Any], ports: list[Mapping[str, Any]],
    required_ports: list[str], trigger: Mapping[str, Any], task_ids: list[str] | None = None,
) -> None:
    """Reject unresolvable scoped inputs before admitting their dispatch batch."""
    from zf.runtime.task_scoped_plan_inputs import SCHEMA_VERSION, MissingTaskPartitionError, task_input_paths

    tasks = [task for task in task_map.get("tasks", []) if isinstance(task, Mapping)]
    local_by_task = {
        str(task.get("task_id") or task.get("id") or ""):
        set((task.get("evidence_contract") or {}).get("required_plan_ports") or [])
        for task in tasks
    }
    selected = set(task_ids) if task_ids is not None else None
    for port in ports:
        name = str(port.get("logical_name") or "")
        body = hydrate_sidecar_ref(state_dir, port).payload
        task_input_paths(body, task_id="", candidate=True)
        for task in tasks:
            task_id = str(task.get("task_id") or task.get("id") or "")
            if selected is not None and task_id not in selected:
                continue
            policy = (task.get("evidence_contract") or {}).get("plan_input_selection")
            consumed = (set(policy["implementation"]) | set(policy["task-verify"])) if policy else (
                set(required_ports) | local_by_task[task_id]
            )
            if name not in consumed:
                continue
            if policy and name == "task_execution_input" and (
                not isinstance(body, Mapping) or body.get("schema_version") != SCHEMA_VERSION
            ):
                raise ValueError(f"{task_id}: selected task_execution_input must be task scoped")
            try:
                task_input_paths(body, task_id=task_id)
            except MissingTaskPartitionError:
                try:
                    inherited_candidate_input_paths(
                        state_dir=state_dir, trigger=trigger, task_map=task_map,
                        task_id=task_id, name=name, body=body,
                    )
                except ValueError as exc:
                    raise ValueError(f"{task_id}: input port {name!r} has no resolvable partition: {exc}") from exc


def inherited_candidate_input_paths(
    *, state_dir: Path, trigger: Mapping[str, Any], task_map: Mapping[str, Any],
    task_id: str, name: str, body: Any,
) -> list[str]:
    """Validate ancestry without creating packages or modifying Task state."""
    ref = str(trigger.get("plan_artifact_package_ref") or "")
    digest = str(trigger.get("plan_artifact_package_digest") or "")
    run = str(trigger.get("workflow_run_id") or "")
    if not all((ref, digest, run)):
        raise ValueError("replacement input preflight requires a pinned baseline package and Run")

    def load(package_ref: str, package_digest: str):
        package = hydrate_plan_artifact_package(state_dir, {"ref": package_ref, "sha256": package_digest})
        if package.get("workflow_run_id") != run:
            raise ValueError("replacement input baseline belongs to another Run")
        ports = {p["logical_name"]: p for group in ("produced", "inherited") for p in package[group]}
        if name not in ports or "task_map" not in ports:
            raise ValueError("replacement input baseline is missing required ports")
        return package, ports

    package, ports = load(ref, digest)
    for key in ("task_map_generation", "run_contract_digest"):
        if trigger.get(key) and trigger[key] != package.get(key):
            raise ValueError("replacement input baseline identity mismatch: " + key)
    original_port = ports[name]
    if hydrate_sidecar_ref(state_dir, original_port).payload != body:
        raise ValueError("candidate input differs from pinned baseline")
    amend = task_map.get("amend")
    if isinstance(amend, Mapping) and task_id in (amend.get("gap_task_ids") or []):
        candidate_ports = {**ports, name: {**original_port,
            "source_package_ref": ref, "source_package_digest": digest}}
        paths, _ = _predecessor_input_paths(state_dir, ports=candidate_ports, name=name,
            body=body, task_id=task_id, workflow_run_id=run, initial_task_map=task_map)
        return paths

    def task_row(value: Mapping[str, Any]):
        rows = [row for row in value.get("tasks", []) if isinstance(row, Mapping)
                and str(row.get("task_id") or row.get("id") or "") == task_id]
        if len(rows) != 1:
            raise ValueError("retained input requires one unambiguous historical task")
        return rows[0]

    current = task_row(task_map)
    seen: set[str] = set()
    for _ in range(32):
        if ref in seen:
            raise ValueError("retained input package ancestry contains a cycle")
        seen.add(ref)
        if any(ports[name].get(key) != original_port.get(key) for key in ("ref", "sha256")):
            raise ValueError("retained input changed in package ancestry")
        historical = hydrate_sidecar_ref(state_dir, ports["task_map"]).payload
        if not isinstance(historical, Mapping) or task_row(historical) != current:
            raise ValueError("retained task changed without a current replacement declaration")
        amendment = historical.get("amend")
        if isinstance(amendment, Mapping) and task_id in (amendment.get("gap_task_ids") or []):
            paths, _ = _predecessor_input_paths(state_dir, ports=ports, name=name,
                body=body, task_id=task_id, workflow_run_id=run)
            return paths
        ref = str(package.get("supersedes_package_ref") or "")
        digest = str(package.get("supersedes_package_digest") or "")
        if not ref or not digest:
            break
        package, ports = load(ref, digest)
    raise ValueError("retained task has no proven replacement input ancestry")
