"""Carry explicitly declared parent read partitions into bounded gap tasks."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def rebind_gap_port_partitions(
    *, state_dir: Path, task_map: Mapping[str, Any], payload: Mapping[str, Any],
    explicit_ports: dict[str, dict[str, Any]], inherited: list[dict[str, Any]],
    source_event_id: str,
) -> None:
    if payload.get("resume_scope") != "gap_tasks_only":
        return
    gap_ids = set(payload.get("gap_task_ids") or (task_map.get("amend") or {}).get("gap_task_ids") or [])
    gaps = [row for row in task_map.get("tasks", []) if row.get("task_id") in gap_ids]
    ports = [*inherited, *[
        port for port in explicit_ports.values() if port.get("rebound_from_ref")
    ]]
    for port in ports:
        body = hydrate_sidecar_ref(state_dir, port).payload
        if not isinstance(body, dict) or "task_input_policy" not in body:
            continue
        body = deepcopy(body)
        policy = body["task_input_policy"]
        if not isinstance(policy, dict) or not isinstance(policy.get("tasks"), list):
            raise ValueError("invalid inherited task_input_policy")
        partitions = {row["task_id"]: row["paths"] for row in policy["tasks"]}
        changed = False
        for gap in gaps:
            task_id = gap["task_id"]
            if task_id in partitions:
                continue
            context = gap.get("payload") or {}
            parent = gap.get("parent_task_id") or context.get("parent_task_id")
            parents = list(dict.fromkeys(
                ([parent] if parent else [])
                + list(gap.get("supersedes_task_ids") or context.get("supersedes_task_ids") or [])
            ))
            if not parents or any(parent not in partitions for parent in parents):
                raise ValueError(
                    f"gap task {task_id} has no explicit source input partition in {port['logical_name']}"
                )
            paths = list(dict.fromkeys(path for parent in parents for path in partitions[parent]))
            policy["tasks"].append({"task_id": task_id, "paths": paths})
            partitions[task_id] = paths
            changed = True
        if not changed:
            continue
        descriptor = write_immutable_json_sidecar(
            state_dir, body, root="plan-input-partitions", kind=port["logical_name"],
            schema_version=str(body.get("schema_version") or ""),
            created_by="plan-artifact-package", source_event_id=source_event_id,
        )
        explicit_ports[port["logical_name"]] = {
            **port, "ref": descriptor["ref"], "sha256": descriptor["sha256"],
            "partition_rebound_from_ref": port["ref"],
            "partition_rebound_from_digest": port["sha256"],
        }
        if port in inherited:
            inherited.remove(port)
    from zf.runtime.task_scoped_plan_inputs import task_input_paths

    for port in [*explicit_ports.values(), *inherited]:
        body = hydrate_sidecar_ref(state_dir, port).payload
        for gap in gaps:
            try:
                task_input_paths(body, task_id=gap["task_id"])
            except ValueError as exc:
                raise ValueError(f"invalid gap input port {port['logical_name']}: {exc}") from exc
