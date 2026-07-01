"""Module-parity and goal-closure gap plan helpers.

These helpers keep gap work in the canonical task-map path. They do not write
runtime state directly; callers may persist the returned task map as an
artifact and emit the normal kernel events.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SUPPORTED_SCHEMA_VERSIONS = {"module-gap-plan.v1", "goal-gap-plan.v1"}


@dataclass(frozen=True)
class ModuleGapPlanValidationResult:
    passed: bool
    errors: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_check(self) -> dict[str, Any]:
        return {
            "name": "module_gap_plan_validate",
            "passed": self.passed,
            "errors": list(self.errors),
            "summary": dict(self.summary),
        }


def gap_tasks_from_rework_summary(summary: Any) -> list[dict[str, Any]]:
    if not isinstance(summary, dict):
        return []
    return _enriched_gap_tasks(summary)


def gap_tasks_from_gap_plan_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    return _enriched_gap_tasks(payload)


def write_gap_task_map_amend_artifact(
    *,
    state_dir: Path,
    project_root: Path | None,
    base_task_map_ref: str,
    pdd_id: str,
    source_event_id: str,
    gap_tasks: list[dict[str, Any]],
    gap_plan_ref: str = "",
) -> dict[str, Any]:
    from zf.runtime.task_map import validate_task_map_payload

    task_map_path = _resolve_artifact_ref(
        base_task_map_ref,
        state_dir=state_dir,
        project_root=project_root,
    )
    if not task_map_path.exists():
        raise ValueError(f"base task_map_ref not found: {base_task_map_ref}")
    base_task_map = json.loads(task_map_path.read_text(encoding="utf-8"))
    if not isinstance(base_task_map, dict):
        raise ValueError("base task_map must be a JSON object")
    artifact_rel = (
        Path("artifacts")
        / _safe_artifact_part(pdd_id or "unknown")
        / "gap-amends"
        / _safe_artifact_part(source_event_id or "event")
        / "task_map.json"
    )
    artifact_path = Path(state_dir) / artifact_rel
    artifact_ref = ".zf/" + artifact_rel.as_posix()
    amended = build_gap_task_map_amend(
        base_task_map,
        gap_tasks=gap_tasks,
        supersedes_task_map_ref=base_task_map_ref,
        gap_plan_ref=gap_plan_ref,
    )
    validation = validate_task_map_payload(amended, require_task_verification=True)
    if not validation.passed:
        raise ValueError("amended task_map validation failed: " + "; ".join(validation.errors))
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(amended, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    history = _append_replan_history_if_requested(
        state_dir=state_dir,
        project_root=project_root,
        history_ref=_first_nonempty(gap_tasks, "replan_history_ref"),
        gap_tasks=gap_tasks,
        source_event_id=source_event_id,
        supersedes_task_map_ref=base_task_map_ref,
        new_task_map_ref=artifact_ref,
        gap_plan_ref=gap_plan_ref,
    )
    result = {
        "task_map_ref": artifact_ref,
        "task_map_path": str(artifact_path),
        "gap_task_ids": [
            _task_id(task)
            for task in gap_tasks
            if _task_id(task)
        ],
    }
    result.update(history)
    return result


def validate_module_gap_plan_payload(payload: dict[str, Any]) -> ModuleGapPlanValidationResult:
    errors: list[str] = []
    schema_version = str(payload.get("schema_version") or "").strip()
    if schema_version and schema_version not in _SUPPORTED_SCHEMA_VERSIONS:
        errors.append(f"unsupported schema_version {schema_version!r}")
    if schema_version == "goal-gap-plan.v1":
        if not str(payload.get("goal_kind") or "").strip():
            errors.append("goal_kind is required for goal-gap-plan.v1")
        if not str(payload.get("gap_category") or "").strip():
            errors.append("gap_category is required for goal-gap-plan.v1")
    gap_tasks = _enriched_gap_tasks(payload)
    if not gap_tasks:
        errors.append("gap_tasks must be a non-empty list")

    seen: set[str] = set()
    for idx, task in enumerate(gap_tasks):
        task_id = _task_id(task)
        prefix = task_id or f"gap_tasks[{idx}]"
        if not task_id:
            errors.append(f"gap_tasks[{idx}].task_id is required")
            continue
        if task_id in seen:
            errors.append(f"duplicate gap task_id {task_id!r}")
        seen.add(task_id)
        if not _claim_paths(task):
            errors.append(f"{prefix}.claim_paths is required")
        if not _acceptance(task):
            errors.append(f"{prefix}.acceptance is required")
        if not _verification(task):
            errors.append(f"{prefix}.verify_commands is required")
        if not _string_list(task.get("source_refs")):
            errors.append(f"{prefix}.source_refs is required")

    return ModuleGapPlanValidationResult(
        passed=not errors,
        errors=errors,
        summary={"gap_task_count": len(gap_tasks), "task_ids": [_task_id(t) for t in gap_tasks]},
    )


def build_gap_task_map_amend(
    base_task_map: dict[str, Any],
    *,
    gap_tasks: list[dict[str, Any]],
    supersedes_task_map_ref: str,
    gap_plan_ref: str = "",
) -> dict[str, Any]:
    """Return a full task-map with gap tasks appended.

    The writer fanout already understands full task maps and task_id filtering.
    Returning a full task map keeps dependency validation and artifact history
    simple while callers can dispatch only the new gap task ids.
    """

    validation = validate_module_gap_plan_payload(_validation_payload(gap_tasks))
    if not validation.passed:
        raise ValueError("gap plan validation failed: " + "; ".join(validation.errors))

    out = dict(base_task_map)
    tasks = [dict(task) for task in _dict_list(base_task_map.get("tasks"))]
    existing_ids = {_task_id(task) for task in tasks if _task_id(task)}
    next_wave = _next_wave(tasks)
    appended: list[str] = []
    for raw in gap_tasks:
        task_id = _task_id(raw)
        if task_id in existing_ids:
            continue
        tasks.append(_gap_task_to_task_map_item(raw, wave=next_wave))
        existing_ids.add(task_id)
        appended.append(task_id)

    source_refs = dict(base_task_map.get("source_refs") or {})
    if supersedes_task_map_ref:
        source_refs["supersedes_task_map_ref"] = supersedes_task_map_ref
    if gap_plan_ref:
        source_refs["gap_plan_ref"] = gap_plan_ref
    out["schema_version"] = "task-map.v1"
    out["source_refs"] = source_refs
    out["tasks"] = tasks
    out["amend"] = {
        "schema_version": "task-map-amend.v1",
        "kind": _amend_kind(gap_tasks),
        "supersedes_task_map_ref": supersedes_task_map_ref,
        "gap_plan_ref": gap_plan_ref,
        "gap_task_ids": appended,
        "gap_task_count": len(appended),
    }
    goal_kind = _first_nonempty(gap_tasks, "goal_kind")
    gap_category = _first_nonempty(gap_tasks, "gap_category")
    if goal_kind:
        out["amend"]["goal_kind"] = goal_kind
    if gap_category:
        out["amend"]["gap_category"] = gap_category
    return out


def _gap_task_to_task_map_item(raw: dict[str, Any], *, wave: int) -> dict[str, Any]:
    task_id = _task_id(raw)
    module_id = str(raw.get("module_id") or "").strip()
    parent_task_id = str(raw.get("parent_task_id") or "").strip()
    affinity_tag = str(raw.get("affinity_tag") or module_id or "").strip()
    goal_kind = str(raw.get("goal_kind") or "").strip()
    gap_category = str(raw.get("gap_category") or "").strip()
    gap_kind = str(
        raw.get("gap_kind")
        or ("module_parity_gap" if gap_category in {"", "parity_gap"} else gap_category)
    ).strip()
    claim_paths = _claim_paths(raw)
    acceptance = _acceptance(raw)
    verification = _verification(raw)
    source_refs = _string_list(raw.get("source_refs"))
    title = str(
        raw.get("title")
        or raw.get("message")
        or raw.get("capability")
        or task_id
    ).strip()
    owner_role = str(raw.get("owner_role") or "dev").strip()
    evidence_contract = _gap_task_evidence_contract(
        raw,
        module_id=module_id,
        parent_task_id=parent_task_id,
        affinity_tag=affinity_tag,
        goal_kind=goal_kind,
        gap_category=gap_category,
        gap_kind=gap_kind,
        source_refs=source_refs,
    )
    return {
        "task_id": task_id,
        "title": title,
        "owner_role": owner_role,
        "phase": str(raw.get("phase") or "gap-impl").strip(),
        "module_id": module_id,
        "parent_task_id": parent_task_id,
        "affinity_tag": affinity_tag,
        "context_group": str(raw.get("context_group") or affinity_tag or module_id).strip(),
        "priority": str(raw.get("priority") or "P0").strip(),
        "gap_kind": gap_kind,
        "gap_category": gap_category,
        "goal_kind": goal_kind,
        "scope": claim_paths,
        "allowed_paths": claim_paths,
        "allowed_paths_reason": (
            str(raw.get("allowed_paths_reason") or "").strip()
            or "Goal gap claim paths generated from verifier source_refs."
        ),
        "acceptance": acceptance,
        "acceptance_criteria": acceptance,
        "verification": verification,
        "verify_commands": verification,
        "source_refs": source_refs,
        "source_ref": source_refs[0] if source_refs else "",
        "source_key": str(raw.get("source_key") or f"goal-gap:{task_id}").strip(),
        "evidence_contract": evidence_contract,
        "wave": _int_value(raw.get("wave"), default=wave),
        "payload": {
            "module_id": module_id,
            "parent_task_id": parent_task_id,
            "affinity_tag": affinity_tag,
            "goal_kind": goal_kind,
            "gap_category": gap_category,
            "gap_kind": gap_kind,
        },
    }


def _validation_payload(gap_tasks: list[dict[str, Any]]) -> dict[str, Any]:
    goal_kind = _first_nonempty(gap_tasks, "goal_kind")
    gap_category = _first_nonempty(gap_tasks, "gap_category")
    if goal_kind or gap_category:
        return {
            "schema_version": "goal-gap-plan.v1",
            "goal_kind": goal_kind,
            "gap_category": gap_category,
            "gap_tasks": gap_tasks,
        }
    return {
        "schema_version": "module-gap-plan.v1",
        "gap_tasks": gap_tasks,
    }


def _enriched_gap_tasks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = _dict_list(payload.get("gap_tasks") or payload.get("tasks"))
    inherited = {
        key: str(payload.get(key) or "").strip()
        for key in (
            "goal_id",
            "goal_kind",
            "gap_category",
            "replan_history_ref",
            "parent_task_id",
            "module_id",
            "affinity_tag",
        )
        if str(payload.get(key) or "").strip()
    }
    inherited_lists = {
        key: _string_list(payload.get(key))
        for key in ("affected_tasks", "gate_changes")
        if _string_list(payload.get(key))
    }
    if not inherited and not inherited_lists:
        return tasks
    enriched: list[dict[str, Any]] = []
    for task in tasks:
        item = dict(task)
        for key, value in inherited.items():
            item.setdefault(key, value)
        for key, value in inherited_lists.items():
            item.setdefault(key, value)
        enriched.append(item)
    return enriched


def _amend_kind(gap_tasks: list[dict[str, Any]]) -> str:
    goal_kind = _first_nonempty(gap_tasks, "goal_kind")
    gap_category = _first_nonempty(gap_tasks, "gap_category")
    if goal_kind or gap_category:
        return "goal_gap"
    return "module_parity_gap"


def _first_nonempty(items: list[dict[str, Any]], key: str) -> str:
    for item in items:
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _gap_task_evidence_contract(
    raw: dict[str, Any],
    *,
    module_id: str,
    parent_task_id: str,
    affinity_tag: str,
    goal_kind: str,
    gap_category: str,
    gap_kind: str,
    source_refs: list[str],
) -> dict[str, Any]:
    evidence = (
        dict(raw.get("evidence_contract"))
        if isinstance(raw.get("evidence_contract"), dict)
        else {}
    )
    fields = {
        "goal_id": str(raw.get("goal_id") or "").strip(),
        "goal_kind": goal_kind,
        "gap_category": gap_category,
        "gap_kind": gap_kind,
        "module_id": module_id,
        "parent_task_id": parent_task_id,
        "affinity_tag": affinity_tag,
        "acceptance_id": str(raw.get("acceptance_id") or "").strip(),
        "repro_ref": str(raw.get("repro_ref") or "").strip(),
        "replan_history_ref": str(raw.get("replan_history_ref") or "").strip(),
        "source_refs": source_refs,
        "affected_tasks": _string_list(raw.get("affected_tasks")),
        "gate_changes": _string_list(raw.get("gate_changes")),
    }
    for key, value in fields.items():
        if value:
            evidence.setdefault(key, value)
    return evidence


def _append_replan_history_if_requested(
    *,
    state_dir: Path,
    project_root: Path | None,
    history_ref: str,
    gap_tasks: list[dict[str, Any]],
    source_event_id: str,
    supersedes_task_map_ref: str,
    new_task_map_ref: str,
    gap_plan_ref: str,
) -> dict[str, Any]:
    if not history_ref:
        return {}
    from zf.runtime.goal_replan_history import append_goal_replan_history_entry

    history = append_goal_replan_history_entry(
        state_dir=state_dir,
        project_root=project_root,
        history_ref=history_ref,
        entry={
            "goal_id": _first_nonempty(gap_tasks, "goal_id"),
            "goal_kind": _first_nonempty(gap_tasks, "goal_kind"),
            "gap_category": _first_nonempty(gap_tasks, "gap_category"),
            "gap_kind": _first_nonempty(gap_tasks, "gap_kind"),
            "source_event_id": source_event_id,
            "supersedes_task_map_ref": supersedes_task_map_ref,
            "new_task_map_ref": new_task_map_ref,
            "gap_plan_ref": gap_plan_ref,
            "gap_task_ids": [_task_id(task) for task in gap_tasks if _task_id(task)],
            "affected_tasks": _unique_strings(
                value
                for task in gap_tasks
                for value in _string_list(task.get("affected_tasks"))
            ),
            "gate_changes": _unique_strings(
                value
                for task in gap_tasks
                for value in _string_list(task.get("gate_changes"))
            ),
            "source_refs": _unique_strings(
                value
                for task in gap_tasks
                for value in _string_list(task.get("source_refs"))
            ),
        },
    )
    return {
        "replan_history_ref": history["replan_history_ref"],
        "replan_history_path": history["replan_history_path"],
    }


def _unique_strings(values: Any) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _next_wave(tasks: list[dict[str, Any]]) -> int:
    waves = [_int_value(task.get("wave"), default=0) for task in tasks]
    return (max(waves) if waves else 0) + 1


def _task_id(raw: dict[str, Any]) -> str:
    return str(raw.get("task_id") or raw.get("id") or "").strip()


def _claim_paths(raw: dict[str, Any]) -> list[str]:
    return (
        _string_list(raw.get("claim_paths"))
        or _string_list(raw.get("allowed_paths"))
        or _string_list(raw.get("target_refs"))
    )


def _acceptance(raw: dict[str, Any]) -> list[str]:
    return _string_list(raw.get("acceptance") or raw.get("acceptance_criteria"))


def _verification(raw: dict[str, Any]) -> list[str]:
    return _string_list(raw.get("verify_commands") or raw.get("verification"))


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _int_value(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolve_artifact_ref(
    ref: str,
    *,
    state_dir: Path,
    project_root: Path | None,
) -> Path:
    path = Path(ref)
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == ".zf":
        return Path(state_dir).joinpath(*path.parts[1:])
    if project_root is not None:
        return Path(project_root) / path
    return Path(state_dir) / path


def _safe_artifact_part(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return text[:80] or "unknown"
