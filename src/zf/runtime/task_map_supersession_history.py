"""Recover unapplied replacement intent from a digest-bound amendment lineage."""

from collections import defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.plan_preview_artifacts import preview_artifact_path, read_preview_artifact
from zf.runtime.task_map_successor import task_map_supersedes_task_ids


def historical_supersession_proofs(
    *, loaded: Any, events: list[ZfEvent], task_store: TaskStore,
    state_dir: Path, project_root: Path,
) -> dict[str, list[str]]:
    """Require linked history, kernel ledger, exact bytes and live successors."""
    run_id = str(getattr(loaded, "workflow_run_id", "") or "")
    if not run_id:
        return {}
    try:
        current = json.loads(Path(loaded.task_map_path).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(current, dict):
        return {}
    current_ids = {row.get("task_id") for row in current.get("tasks", [])}
    edges: dict[str, set[str]] = defaultdict(set)

    def add_edges(body: dict) -> None:
        for row in body.get("tasks", []):
            if not isinstance(row, dict):
                continue
            for old in task_map_supersedes_task_ids(row):
                edges[old].add(str(row.get("task_id") or ""))

    add_edges(current)
    ledger: dict[Path, list[ZfEvent]] = defaultdict(list)
    for event in events:
        p = event.payload
        if (event.type != "task_map.amended" or event.origin != "kernel"
                or p.get("workflow_run_id") != run_id or not p.get("task_map_digest")):
            continue
        try:
            path = preview_artifact_path(str(p.get("task_map_ref") or ""),
                                         state_dir=state_dir, project_root=project_root)
        except (OSError, ValueError):
            continue
        ledger[path].append(event)
    proofs: dict[str, list[str]] = defaultdict(list)
    ref = (current.get("amend") or {}).get("supersedes_task_map_ref")
    seen: set[Path] = set()
    for _ in range(32):
        if not ref:
            break
        try:
            path = preview_artifact_path(ref, state_dir=state_dir, project_root=project_root)
            if path in seen:
                break
            seen.add(path)
            text = read_preview_artifact(ref, state_dir=state_dir, project_root=project_root)
            body = json.loads(text)
        except (OSError, ValueError):
            break
        if not isinstance(body, dict):
            break
        digest = hashlib.sha256(text.encode()).hexdigest()
        for event in ledger.get(path, []):
            if event.payload["task_map_digest"] != digest:
                continue
            add_edges(body)
            for old in event.payload.get("superseded_task_ids", []):
                if old not in current_ids:
                    proofs[old].append(event.id)
        ref = (body.get("amend") or {}).get("supersedes_task_map_ref")

    def reaches_materialized_successors(node: str, visiting: set[str]) -> bool:
        if node in visiting:
            return False
        if node in current_ids:
            task = task_store.get(node)
            return task is not None and task.status != "cancelled"
        successors = edges.get(node, set())
        return bool(successors) and all(
            reaches_materialized_successors(child, visiting | {node}) for child in successors
        )

    recovered = {}
    for old, event_ids in proofs.items():
        task = task_store.get(old)
        evidence = getattr(getattr(task, "contract", None), "evidence_contract", {}) or {}
        if (task is None or task.status in {"done", "in_progress"}
                or task.active_dispatch_id or evidence.get("workflow_run_id") != run_id):
            continue
        if reaches_materialized_successors(old, set()):
            recovered[old] = list(dict.fromkeys(event_ids))
    return recovered
