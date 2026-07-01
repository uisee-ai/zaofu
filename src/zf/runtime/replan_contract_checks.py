"""Deterministic checks for replan contract evaluation."""

from __future__ import annotations

from typing import Any

from zf.runtime.replan_contract_types import ReplanContractCheck
from zf.runtime.task_map import validate_task_map_payload


def _schema_check(task_map: dict[str, Any]) -> ReplanContractCheck:
    validation = validate_task_map_payload(task_map, require_task_verification=True)
    return ReplanContractCheck(
        name="schema_completeness",
        passed=validation.passed,
        errors=list(validation.errors),
        summary=dict(validation.summary),
    )


def _contract_completeness_check(task_map: dict[str, Any]) -> ReplanContractCheck:
    errors: list[str] = []
    for idx, raw in enumerate(task_map.get("tasks") or []):
        if not isinstance(raw, dict):
            continue
        task_id = _task_id(raw) or f"tasks[{idx}]"
        if not str(raw.get("behavior") or raw.get("title") or "").strip():
            errors.append(f"{task_id}.behavior is required")
        if not _string_list(raw.get("scope")):
            errors.append(f"{task_id}.scope is required")
        if not (
            str(raw.get("verification") or "").strip()
            or _string_list(raw.get("acceptance"))
            or _validation_command(raw)
        ):
            errors.append(f"{task_id}.verification is required")
        if not str(raw.get("owner_role") or "").strip():
            errors.append(f"{task_id}.owner_role is required")
        if raw.get("wave") in (None, ""):
            errors.append(f"{task_id}.wave is required")
    return ReplanContractCheck(
        name="contract_completeness",
        passed=not errors,
        errors=errors,
        summary={"task_count": len(_task_map_items_by_id(task_map))},
    )


def _scope_concurrency_check(task_map: dict[str, Any]) -> ReplanContractCheck:
    errors: list[str] = []
    by_wave: dict[int, list[dict[str, Any]]] = {}
    for raw in task_map.get("tasks") or []:
        if isinstance(raw, dict):
            by_wave.setdefault(_int(raw.get("wave")), []).append(raw)
    for wave, items in by_wave.items():
        for index, left in enumerate(items):
            for right in items[index + 1:]:
                left_paths = _claimed_paths(left)
                right_paths = _claimed_paths(right)
                for lp in left_paths:
                    for rp in right_paths:
                        if _path_overlaps(lp, rp):
                            errors.append(
                                "same wave path overlap "
                                f"wave={wave}: {_task_id(left)} {lp!r} vs "
                                f"{_task_id(right)} {rp!r}"
                            )
    return ReplanContractCheck(
        name="scope_concurrency",
        passed=not errors,
        errors=errors,
        summary={"wave_count": len(by_wave)},
    )


def _source_coverage_check(
    task_map: dict[str, Any],
    source_index: dict[str, Any],
) -> ReplanContractCheck:
    errors: list[str] = []
    source_entries = _source_entries_by_task_id(source_index)
    for raw in task_map.get("tasks") or []:
        if not isinstance(raw, dict):
            continue
        task_id = _task_id(raw)
        if not task_id:
            continue
        entry = source_entries.get(task_id)
        if not entry:
            errors.append(f"source_index missing task_id {task_id!r}")
            continue
        source_ref = str(entry.get("source_ref") or entry.get("ref") or "").strip()
        excerpt = str(
            entry.get("source_excerpt")
            or entry.get("excerpt")
            or entry.get("text")
            or ""
        ).strip()
        if not source_ref:
            errors.append(f"{task_id}.source_ref is required")
        if not excerpt:
            errors.append(f"{task_id}.source_excerpt is required")
        if _explicitly_untraced(raw):
            errors.append(f"{task_id}.no_invention: candidate marks untraced source")
    return ReplanContractCheck(
        name="source_coverage_no_invention",
        passed=not errors,
        errors=errors,
        summary={
            "task_count": len(_task_map_items_by_id(task_map)),
            "source_task_count": len(source_entries),
        },
    )


def _done_evidence_check(
    new_by_id: dict[str, dict[str, Any]],
    old_by_id: dict[str, dict[str, Any]],
) -> ReplanContractCheck:
    errors: list[str] = []
    for task_id, old in old_by_id.items():
        if str(old.get("status") or "").strip() != "done":
            continue
        if task_id not in new_by_id:
            continue
        raw_evidence = new_by_id[task_id].get("completed_evidence_refs")
        if not _string_list(raw_evidence):
            raw_evidence = _string_list(new_by_id[task_id].get("done_evidence_refs"))
        if not raw_evidence:
            errors.append(f"{task_id}.completed_evidence_refs is required for done preserve")
    done_count = sum(
        1
        for item in old_by_id.values()
        if str(item.get("status") or "") == "done"
    )
    return ReplanContractCheck(
        name="done_evidence_carry_forward",
        passed=not errors,
        errors=errors,
        summary={"done_task_count": done_count},
    )


def _resume_safety_check(
    *,
    new_by_id: dict[str, dict[str, Any]],
    old_by_id: dict[str, dict[str, Any]],
    old_map_by_id: dict[str, dict[str, Any]],
    progress_state: dict[str, Any],
) -> ReplanContractCheck:
    errors: list[str] = []
    active_ids = set(_string_list(progress_state.get("active_task_ids")))
    active_ids.update({
        task_id
        for task_id, raw in old_by_id.items()
        if str(raw.get("status") or "") in {"in_progress", "review", "test", "judge"}
    })
    for task_id in sorted(active_ids):
        old = old_map_by_id.get(task_id) or old_by_id.get(task_id) or {}
        new = new_by_id.get(task_id)
        rewritten = new is not None and _contract_fingerprint(old) != _contract_fingerprint(new)
        cancelled = new is None
        if not (rewritten or cancelled):
            continue
        raw_policy = (new or old).get("resume_safety") or progress_state.get("resume_safety")
        policy = raw_policy if isinstance(raw_policy, dict) else {}
        missing = [
            key
            for key in ("checkpoint_ref", "resume_plan", "capsule_refresh")
            if not str(policy.get(key) or "").strip()
        ]
        if missing:
            errors.append(
                f"{task_id}.resume_safety missing {', '.join(missing)} "
                "for active rewrite/cancel"
            )
    return ReplanContractCheck(
        name="resume_safety",
        passed=not errors,
        errors=errors,
        summary={"active_task_count": len(active_ids)},
    )


def _failure_binding_check(
    *,
    new_by_id: dict[str, dict[str, Any]],
    failure_evidence: dict[str, Any],
) -> ReplanContractCheck:
    required = str(failure_evidence.get("required_remediation") or "").strip()
    failure_class = str(failure_evidence.get("trigger_failure_class") or "").strip()
    if not (required or failure_class):
        return ReplanContractCheck(
            name="failure_taxonomy_binding",
            passed=True,
            summary={"required": False},
        )
    haystack = " ".join(
        " ".join([
            str(raw.get("title") or ""),
            str(raw.get("behavior") or ""),
            " ".join(_string_list(raw.get("acceptance"))),
            " ".join(_string_list(raw.get("scope"))),
        ])
        for raw in new_by_id.values()
    ).lower()
    needles = [item.lower() for item in (required, failure_class) if item]
    passed = any(needle in haystack for needle in needles)
    return ReplanContractCheck(
        name="failure_taxonomy_binding",
        passed=passed,
        errors=[] if passed else [
            "candidate task_map does not bind required remediation/failure class"
        ],
        summary={
            "required": True,
            "trigger_failure_class": failure_class,
        },
    )


def _profile_check(
    *,
    profile: str,
    strict_review_evidence: dict[str, Any],
    release_evidence: dict[str, Any],
) -> ReplanContractCheck:
    errors: list[str] = []
    if profile in {"strict", "release"}:
        for key in ("critic_ref", "verifier_ref"):
            if not str(strict_review_evidence.get(key) or "").strip():
                errors.append(f"strict_review_evidence.{key} is required")
    if profile == "release":
        for key in ("e2e_ref", "security_ref", "human_approval_ref"):
            if not str(release_evidence.get(key) or "").strip():
                errors.append(f"release_evidence.{key} is required")
    return ReplanContractCheck(
        name="profile_policy",
        passed=not errors,
        errors=errors,
        summary={"profile": profile},
    )


def _contract_delta(
    *,
    new_by_id: dict[str, dict[str, Any]],
    old_by_id: dict[str, dict[str, Any]],
    old_map_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    preserve: list[str] = []
    rewrite: list[str] = []
    cancel: list[str] = []
    new: list[str] = []
    old_ids = set(old_map_by_id) | set(old_by_id)
    for task_id in sorted(new_by_id):
        if task_id not in old_ids:
            new.append(task_id)
            continue
        old = old_map_by_id.get(task_id) or old_by_id.get(task_id) or {}
        if _contract_fingerprint(old) == _contract_fingerprint(new_by_id[task_id]):
            preserve.append(task_id)
        else:
            rewrite.append(task_id)
    for task_id in sorted(old_ids - set(new_by_id)):
        cancel.append(task_id)
    return {
        "preserve_task_ids": preserve,
        "cancel_task_ids": cancel,
        "rewrite_task_ids": rewrite,
        "new_task_ids": new,
    }


def _decision(*, profile: str, failed: list[ReplanContractCheck]) -> str:
    if not failed:
        return "adopt"
    if profile == "release" and any(check.name == "profile_policy" for check in failed):
        return "reject"
    if any(check.name == "schema_completeness" for check in failed):
        return "revise"
    return "revise"


def _old_tasks_by_id(
    old_tasks: list[dict[str, Any]] | dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    if isinstance(old_tasks, dict):
        return {
            str(task_id): dict(raw)
            for task_id, raw in old_tasks.items()
            if isinstance(raw, dict)
        }
    out: dict[str, dict[str, Any]] = {}
    for raw in old_tasks or []:
        if not isinstance(raw, dict):
            continue
        task_id = str(raw.get("task_id") or raw.get("id") or "").strip()
        if task_id:
            out[task_id] = raw
    return out


def _task_map_items_by_id(task_map: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for raw in task_map.get("tasks") or []:
        if not isinstance(raw, dict):
            continue
        task_id = _task_id(raw)
        if task_id:
            out[task_id] = raw
    return out


def _source_entries_by_task_id(source_index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = source_index.get("tasks")
    if isinstance(raw, dict):
        entries = [
            {"task_id": key, **value}
            if isinstance(value, dict)
            else {"task_id": key, "source_excerpt": str(value)}
            for key, value in raw.items()
        ]
    elif isinstance(raw, list):
        entries = raw
    else:
        entries = []
    out: dict[str, dict[str, Any]] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        task_id = _task_id(item)
        if task_id:
            out[task_id] = item
    return out


def _contract_fingerprint(raw: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(raw.get("title") or "").strip(),
        str(raw.get("behavior") or "").strip(),
        tuple(_string_list(raw.get("scope"))),
        str(raw.get("verification") or _validation_command(raw) or "").strip(),
        tuple(_string_list(raw.get("acceptance"))),
        str(raw.get("owner_role") or "").strip(),
        _int(raw.get("wave")),
    )


def _claimed_paths(raw: dict[str, Any]) -> list[str]:
    return _string_list(raw.get("exclusive_files")) or _string_list(raw.get("scope"))


def _path_overlaps(left: str, right: str) -> bool:
    left = left.strip().rstrip("/")
    right = right.strip().rstrip("/")
    if not left or not right:
        return False
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _explicitly_untraced(raw: dict[str, Any]) -> bool:
    if bool(raw.get("untraced")):
        return True
    source_status = str(raw.get("source_status") or "").strip()
    return source_status in {"untraced", "invented", "missing"}


def _task_id(raw: dict[str, Any]) -> str:
    return str(raw.get("task_id") or raw.get("id") or "").strip()


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _validation_command(raw: dict[str, Any]) -> str:
    validation = raw.get("validation")
    if not isinstance(validation, dict):
        return ""
    if str(validation.get("kind") or "") == "command":
        return str(validation.get("command") or "").strip()
    return ""


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "_contract_completeness_check",
    "_contract_delta",
    "_decision",
    "_done_evidence_check",
    "_failure_binding_check",
    "_old_tasks_by_id",
    "_profile_check",
    "_resume_safety_check",
    "_schema_check",
    "_scope_concurrency_check",
    "_source_coverage_check",
    "_task_map_items_by_id",
]
