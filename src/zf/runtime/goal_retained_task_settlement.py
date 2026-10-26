"""Prove unchanged prior-plan tasks are covered by the current Goal terminal."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.goal_terminal_settlement import _task_goal_claims_closed
from zf.runtime.plan_artifact_package import (
    hydrate_plan_artifact_package,
    reduce_plan_artifact_packages,
)
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def retained_goal_terminal_task_ids(
    state_dir: Path,
    tasks: Iterable[Task],
    events: list[ZfEvent],
    terminal: ZfEvent,
) -> set[str]:
    """Fail closed unless immutable plan ancestry and closed claims prove carryover."""
    payload = terminal.payload if isinstance(terminal.payload, dict) else {}
    run_id = str(payload.get("workflow_run_id") or payload.get("run_id") or "")
    if terminal.type != "run.goal.completed" or not run_id:
        return set()
    run = build_run_admission_projection(events).runs.get(run_id)
    if run is None or run.status != "completed" or run.terminal_event_id != terminal.id:
        return set()
    try:
        current = reduce_plan_artifact_packages(events, workflow_run_id=run_id).get("current")
        if not current or current.get("task_map_generation") != payload.get("task_map_generation"):
            return set()
        body = hydrate_plan_artifact_package(state_dir, {
            "ref": current["package_ref"], "sha256": current["package_digest"],
        })
        current_port, current_map = _task_map(state_dir, body)
        if current_port["ref"] != payload.get("task_map_ref"):
            return set()
        amend = current_map.get("amend")
        if not isinstance(amend, Mapping) or not amend.get("supersedes_task_map_ref"):
            return set()
        superseded = set(amend.get("superseded_task_ids") or [])
        current_rows = _task_rows(current_map)
        ancestors: dict[tuple[str, str], dict[str, Any]] = {}
        while body.get("supersedes_package_ref") and body.get("supersedes_package_digest"):
            identity = (body["supersedes_package_ref"], body["supersedes_package_digest"])
            if identity in ancestors:
                return set()
            body = hydrate_plan_artifact_package(state_dir, {"ref": identity[0], "sha256": identity[1]})
            if body.get("workflow_run_id") != run_id or body.get("flow_kind") != current.get("flow_kind"):
                return set()
            ancestors[identity] = body
        selected: set[str] = set()
        for task in tasks:
            evidence = task.contract.evidence_contract
            refs = evidence.get("source_refs") if isinstance(evidence, dict) else None
            if (
                not isinstance(refs, dict)
                or evidence.get("workflow_run_id") != run_id
                or task.id in superseded
                or not _task_goal_claims_closed(task, payload)
            ):
                continue
            previous = ancestors.get((str(refs.get("plan_artifact_package_ref") or ""),
                                      str(refs.get("plan_artifact_package_digest") or "")))
            if previous is None:
                continue
            old_port, old_map = _task_map(state_dir, previous)
            generation = evidence.get("task_map_generation") or refs.get("task_map_generation")
            if generation != previous.get("task_map_generation") or refs.get("task_map_ref") != old_port["ref"]:
                continue
            old_row = _task_rows(old_map).get(task.id)
            if old_row is not None and old_row == current_rows.get(task.id):
                selected.add(task.id)
        return selected
    except (OSError, ValueError, KeyError, TypeError):
        return set()


def _task_map(state_dir: Path, package: Mapping[str, Any]) -> tuple[dict, dict]:
    port = next((p for p in [*package["produced"], *package["inherited"]]
                 if p.get("logical_name") == "task_map"), None)
    if port is None:
        raise ValueError("plan package has no task map")
    body = hydrate_sidecar_ref(state_dir, port).payload
    if not isinstance(body, dict):
        raise ValueError("task map must be an object")
    return port, body


def _task_rows(task_map: Mapping[str, Any]) -> dict[str, dict]:
    rows = [r for r in task_map.get("tasks", []) if isinstance(r, dict)]
    ids = [str(r.get("task_id") or "") for r in rows]
    if not all(ids) or len(set(ids)) != len(ids):
        raise ValueError("task map requires unique task ids")
    return dict(zip(ids, rows))
