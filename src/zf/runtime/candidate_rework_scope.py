"""Current Task Map rebinding and path-owned candidate rework scope."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from zf.core.task.store import TaskStore
from zf.runtime.artifact_refs import resolve_runtime_artifact_ref
from zf.runtime.candidate_rework_identity import (
    _TASK_MAP_GENERATION_BOUND_IDENTITY_KEYS,
)
from zf.runtime.task_refs import _path_allowed_by_scope
from zf.runtime.verification_result import verification_rework_items_from_payload
from zf.runtime.writer_fanout_admission import (
    TERMINAL_TASK_STATUSES,
    writer_task_items,
)


def prepare_candidate_rework_scope(
    *,
    state_dir: Path,
    project_root: Path,
    events: list[Any],
    pdd_id: str,
    trace_id: str,
    source_event_id: str,
    anchor: dict[str, Any],
    rework_paths: list[str],
    failed_task_ids: list[str],
    rework_summary: dict[str, Any],
) -> dict[str, Any]:
    rebound = dict(anchor)
    _restore_existing_source_index(
        rebound,
        events=events,
        pdd_id=pdd_id,
        trace_id=trace_id,
        state_dir=state_dir,
        project_root=project_root,
    )
    binding = _current_task_map_binding(events, pdd_id=pdd_id, trace_id=trace_id)
    if binding:
        prior_generation = str(rebound.get("task_map_generation") or "")
        current_generation = str(binding.get("task_map_generation") or "")
        plan_revision = str(rebound.get("plan_revision") or "")
        if current_generation and (
            current_generation != prior_generation
            or (plan_revision and plan_revision != current_generation)
        ):
            for key in _TASK_MAP_GENERATION_BOUND_IDENTITY_KEYS:
                rebound.pop(key, None)
        rebound.update(binding)

    task_map_ref = str(rebound.get("task_map_ref") or "").strip()
    task_items = _load_task_items(
        task_map_ref,
        state_dir=state_dir,
        project_root=project_root,
    )
    current_task_ids = {
        str(item.get("task_id") or "").strip()
        for item in task_items
        if str(item.get("task_id") or "").strip()
    }
    task_store = TaskStore(state_dir / "kanban.json")
    terminal_task_ids = {
        task_id
        for task_id in current_task_ids
        if (
            (task := task_store.get(task_id)) is not None
            and str(task.status or "") in TERMINAL_TASK_STATUSES
        )
    }
    valid_failed_task_ids = [
        task_id
        for task_id in dict.fromkeys(failed_task_ids)
        if task_id in current_task_ids and task_id not in terminal_task_ids
    ]
    invalid_failed_task_ids = [
        task_id
        for task_id in dict.fromkeys(failed_task_ids)
        if task_id not in current_task_ids
    ]
    terminal_failed_task_ids = [
        task_id
        for task_id in dict.fromkeys(failed_task_ids)
        if task_id in terminal_task_ids
    ]
    paths = list(dict.fromkeys(str(path).strip() for path in rework_paths if str(path).strip()))
    owners_by_path = _owners_by_path(paths, task_items)
    owner_ids = list(dict.fromkeys(
        task_id
        for path in paths
        for task_id in owners_by_path.get(path, [])
    ))
    terminal_owner_ids = [
        task_id for task_id in owner_ids if task_id in terminal_task_ids
    ]
    active_owner_ids = [
        task_id for task_id in owner_ids if task_id not in terminal_task_ids
    ]
    unowned_paths = [path for path in paths if not owners_by_path.get(path)]
    summary = dict(rework_summary)
    summary["rework_paths"] = paths
    summary["path_owner_task_ids"] = owner_ids
    summary["terminal_path_owner_task_ids"] = terminal_owner_ids
    summary["unowned_rework_paths"] = unowned_paths
    summary["invalid_failed_task_ids"] = invalid_failed_task_ids
    summary["terminal_failed_task_ids"] = terminal_failed_task_ids

    if unowned_paths and not owner_ids and not summary.get("gap_tasks"):
        # A finding's path can describe a runner/evidence failure. Without a
        # canonical source owner or an explicit gap plan, it cannot authorize
        # a new writer scope, dependency graph, or default "dev" owner.
        summary["scope_replan_reason"] = (
            "candidate finding paths have no Task Map owner; an explicit repair plan is required"
        )
        return {
            "anchor": rebound,
            "failed_task_ids": [],
            "rework_summary": summary,
            "requires_replan": True,
        }

    if unowned_paths or terminal_owner_ids:
        rework_items = _source_rework_items(events, source_event_id)
        gap_task = _replacement_gap_task(
            pdd_id=pdd_id,
            source_event_id=source_event_id,
            base_commit=str(rebound.get("source_commit") or ""),
            rework_items=rework_items,
            rework_paths=paths,
            unowned_paths=unowned_paths,
            owner_ids=owner_ids,
            task_items=task_items,
        )
        if gap_task:
            summary["gap_tasks"] = [gap_task]
            summary["path_owner_task_ids"] = []
            return {
                "anchor": rebound,
                "failed_task_ids": [],
                "rework_summary": summary,
                "requires_replan": False,
            }

    selected_task_ids = active_owner_ids or valid_failed_task_ids
    requires_replan = bool(
        (invalid_failed_task_ids or terminal_failed_task_ids or terminal_owner_ids)
        and not selected_task_ids
        and not summary.get("gap_tasks")
    )
    if requires_replan:
        summary["scope_replan_reason"] = (
            "candidate rework task ids are absent from the current Task Map"
        )
    return {
        "anchor": rebound,
        "failed_task_ids": selected_task_ids,
        "rework_summary": summary,
        "requires_replan": requires_replan,
    }


def _restore_existing_source_index(
    anchor: dict[str, Any],
    *,
    events: list[Any],
    pdd_id: str,
    trace_id: str,
    state_dir: Path,
    project_root: Path,
) -> None:
    current = str(anchor.get("source_index_ref") or "").strip()
    if current and _artifact_ref_exists(
        current,
        state_dir=state_dir,
        project_root=project_root,
    ):
        return
    for event in reversed(events):
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            continue
        event_pdd = str(payload.get("pdd_id") or payload.get("feature_id") or "").strip()
        if event_pdd != pdd_id:
            continue
        event_trace = str(
            payload.get("trace_id") or getattr(event, "correlation_id", "") or ""
        ).strip()
        if trace_id and event_trace and event_trace != trace_id:
            continue
        candidate = str(payload.get("source_index_ref") or "").strip()
        if candidate and _artifact_ref_exists(
            candidate,
            state_dir=state_dir,
            project_root=project_root,
        ):
            anchor["source_index_ref"] = candidate
            return


def _artifact_ref_exists(
    ref: str,
    *,
    state_dir: Path,
    project_root: Path,
) -> bool:
    return resolve_runtime_artifact_ref(
        ref,
        state_dir=state_dir,
        project_root=project_root,
        search_workdirs=False,
    ).is_file()


def _current_task_map_binding(
    events: list[Any],
    *,
    pdd_id: str,
    trace_id: str,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    superseded_generations: set[str] = set()
    for event in events:
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            continue
        if str(payload.get("pdd_id") or payload.get("feature_id") or "") != pdd_id:
            continue
        # Task Map currentness is feature-scoped. Replan/gap bridges may use a
        # child correlation id while still superseding the same PDD map.
        superseded = str(payload.get("supersedes_task_map_generation") or "").strip()
        if superseded:
            superseded_generations.add(superseded)
        if getattr(event, "type", "") != "task_map.ready":
            continue
        task_map_ref = str(payload.get("task_map_ref") or "").strip()
        generation = str(payload.get("task_map_generation") or "").strip()
        if task_map_ref and generation:
            candidates.append(payload)
    for payload in reversed(candidates):
        generation = str(payload.get("task_map_generation") or "").strip()
        if generation in superseded_generations:
            continue
        return {
            key: payload[key]
            for key in (
                "task_map_ref",
                "task_map_generation",
                "task_map_digest",
            )
            if payload.get(key) not in (None, "")
        }
    return {}


def _load_task_items(
    task_map_ref: str,
    *,
    state_dir: Path,
    project_root: Path,
) -> list[dict[str, Any]]:
    if not task_map_ref:
        return []
    path = resolve_runtime_artifact_ref(
        task_map_ref,
        state_dir=state_dir,
        project_root=project_root,
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    normalized = writer_task_items(payload)
    raw_tasks = payload.get("tasks") if isinstance(payload, dict) else []
    raw_by_id = {
        str(item.get("task_id") or ""): dict(item)
        for item in raw_tasks or []
        if isinstance(item, dict) and str(item.get("task_id") or "")
    }
    return [
        {**raw_by_id.get(str(item.get("task_id") or ""), {}), **item}
        for item in normalized
    ]


def _owners_by_path(
    paths: list[str],
    task_items: list[dict[str, Any]],
) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for path in paths:
        owners: list[str] = []
        for item in task_items:
            task_id = str(item.get("task_id") or "").strip()
            scope = [str(value) for value in item.get("allowed_paths") or []]
            if task_id and _path_allowed_by_scope(path, scope):
                owners.append(task_id)
        out[path] = owners
    return out


def _source_rework_items(
    events: list[Any],
    source_event_id: str,
) -> list[dict[str, Any]]:
    source = next(
        (event for event in events if str(getattr(event, "id", "")) == source_event_id),
        None,
    )
    source_payload = getattr(source, "payload", {}) or {}
    fanout_id = str(source_payload.get("fanout_id") or "") if isinstance(source_payload, dict) else ""
    out: list[dict[str, Any]] = []
    for event in events:
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            continue
        if fanout_id and str(payload.get("fanout_id") or "") != fanout_id:
            continue
        out.extend(verification_rework_items_from_payload(payload))
    return out


def _replacement_gap_task(
    *,
    pdd_id: str,
    source_event_id: str,
    base_commit: str,
    rework_items: list[dict[str, Any]],
    rework_paths: list[str],
    unowned_paths: list[str],
    owner_ids: list[str],
    task_items: list[dict[str, Any]],
) -> dict[str, Any]:
    if len(owner_ids) > 1:
        return {}
    rework = rework_items[0] if rework_items else {}
    parent = next(
        (item for item in task_items if str(item.get("task_id") or "") in owner_ids),
        {},
    )
    raw_id = str(rework.get("rework_item_id") or "candidate-verification-gap")
    suffix = re.sub(r"[^A-Za-z0-9]+", "-", raw_id).strip("-").upper()
    if suffix.startswith("RW-"):
        suffix = suffix[3:]
    task_id = f"TASK-REWORK-{suffix}"[:120]
    parent_paths = [str(path) for path in parent.get("allowed_paths") or []]
    additional = [
        path for path in unowned_paths
        if not _path_allowed_by_scope(path, parent_paths)
    ]
    allowed_paths = list(dict.fromkeys([*parent_paths, *additional]))
    acceptance = _acceptance_lines(parent.get("acceptance_criteria") or parent.get("acceptance"))
    acceptance.extend(
        value for value in (
            str(rework.get("expected") or "").strip(),
            str(rework.get("required_delta") or "").strip(),
            str(rework.get("done_when") or "").strip(),
        ) if value and value not in acceptance
    )
    verification = _string_list(parent.get("verify_commands") or parent.get("verification"))
    for command in _string_list(rework.get("verification_commands")):
        if command not in verification:
            verification.append(command)
    source_refs = _string_list(parent.get("source_refs"))
    for ref in _string_list(rework.get("source_refs")):
        if ref not in source_refs:
            source_refs.append(ref)
    if not source_refs:
        source_refs.append(f"events.jsonl#{source_event_id}")
    git_ref = f"git:{base_commit}" if base_commit else ""
    if git_ref and git_ref not in source_refs:
        source_refs.append(git_ref)
    validation = _replacement_validation(parent.get("validation"), task_id)
    return {
        "task_id": task_id,
        "title": str(rework.get("rework_item_id") or "Candidate verification rework"),
        "owner_role": str(parent.get("owner_role") or rework.get("owner") or "dev"),
        "parent_task_id": pdd_id,
        "affinity_tag": str(parent.get("affinity_tag") or "candidate-rework"),
        "blocked_by": _string_list(parent.get("blocked_by")),
        "claim_paths": allowed_paths,
        "allowed_paths_reason": (
            "Preserve the current owner scope and add verifier-admitted unowned paths: "
            + ", ".join(additional)
        ),
        "acceptance": acceptance,
        "verify_commands": verification,
        "verification_read_paths": _string_list(parent.get("verification_read_paths")),
        "source_refs": source_refs,
        "supersedes_task_ids": owner_ids,
        "affected_tasks": list(dict.fromkeys([*owner_ids, *rework_paths])),
        "goal_kind": "prd",
        "gap_category": "verification_gap",
        "gap_kind": "candidate_rejection",
        "priority": "P0",
        "wave": int(parent.get("wave") or 0),
        "base_commit": base_commit,
        "validation": validation,
    }


def _replacement_validation(value: Any, task_id: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    validation = json.loads(json.dumps(value))
    commands = validation.get("commands")
    if isinstance(commands, list):
        for command in commands:
            if isinstance(command, dict) and command.get("producer_task_id"):
                command["producer_task_id"] = task_id
    return validation


def _acceptance_lines(value: Any) -> list[str]:
    if not isinstance(value, list):
        return _string_list(value)
    out: list[str] = []
    for item in value:
        text = (
            str(item.get("statement") or item.get("description") or item.get("id") or "")
            if isinstance(item, dict)
            else str(item)
        ).strip()
        if text and text not in out:
            out.append(text)
    return out


def _string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    if value not in (None, ""):
        return [str(value).strip()]
    return []


__all__ = ["prepare_candidate_rework_scope"]
