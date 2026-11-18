"""Identify historical integrated tasks replaced by verified current tasks."""

from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_map_successor import (
    successor_base_errors,
    task_map_successor_base_commit,
    task_map_supersedes_task_ids,
)


def verified_predecessors(
    state_dir: Path,
    candidate: ZfEvent,
    history: list[ZfEvent],
    verified: dict[str, dict],
) -> set[str]:
    """Require the pinned Plan, a prior freeze, and all current replacement Verifies."""
    frozen = candidate.payload
    if (
        candidate.origin != "kernel"
        or not verified
        or not all(
            frozen.get(key)
            for key in (
                "plan_artifact_package_ref",
                "plan_artifact_package_digest",
                "task_map_ref",
                "task_map_digest",
            )
        )
    ):
        return set()
    package = hydrate_plan_artifact_package(
        state_dir,
        {
            "ref": frozen["plan_artifact_package_ref"],
            "sha256": frozen["plan_artifact_package_digest"],
        },
        validate_ports=False,
    )
    if any(
        package.get(key) != frozen.get(key)
        for key in ("workflow_run_id", "task_map_generation")
    ):
        return set()
    if not any(
        port["logical_name"] == "task_map"
        and port["ref"] == frozen["task_map_ref"]
        and port["sha256"] == frozen["task_map_digest"]
        for port in [*package["produced"], *package["inherited"]]
    ):
        return set()
    task_map = hydrate_sidecar_ref(
        state_dir,
        {
            "ref": frozen["task_map_ref"],
            "sha256": frozen["task_map_digest"],
        },
    ).payload
    rows = task_map.get("tasks", []) if isinstance(task_map, dict) else []
    if not isinstance(rows, list):
        return set()
    historical: dict[str, set[str]] = {}
    for event in history:
        prior = event.payload
        if (
            event.type == "candidate.ready"
            and event.origin == "kernel"
            and prior.get("schema_version") == "candidate-freeze-receipt.v1"
            and prior.get("workflow_run_id") == frozen["workflow_run_id"]
            and prior.get("task_map_generation") != frozen["task_map_generation"]
            and prior.get("candidate_ref") == frozen.get("candidate_ref")
            and prior.get("freeze_receipt_digest")
        ):
            historical.setdefault(
                str(prior.get("candidate_head_commit") or ""), set()
            ).update(prior.get("completed_task_ids") or [])

    current_ids: set[str] = set()
    replaced: set[str] = set()
    unresolved: set[str] = set()
    for row in rows:
        if isinstance(row, str):
            current_ids.add(row)
            continue
        if not isinstance(row, dict):
            return set()
        task_id = str(row.get("task_id") or row.get("id") or "")
        current_ids.add(task_id)
        predecessors = set(task_map_supersedes_task_ids(row))
        if not predecessors:
            continue
        successor = verified.get(task_id, {})
        base = task_map_successor_base_commit(row)
        if (
            successor.get("base_commit") != base
            or not base
            or successor.get("plan_artifact_package_digest")
            != frozen["plan_artifact_package_digest"]
            or successor_base_errors(row, task_id=task_id)
        ):
            unresolved.update(predecessors)
            continue
        base_tasks = historical.get(base, set())
        replaced.update(predecessors & base_tasks)
        unresolved.update(predecessors - base_tasks)
    return replaced - current_ids - unresolved
