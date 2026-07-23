"""Mechanical Goal Claim coverage checks for task-map admission."""

from __future__ import annotations

from typing import Any


def validate_goal_coverage(
    payload: dict[str, Any],
    tasks: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    raw_claims = payload.get("goal_claims")
    if not isinstance(raw_claims, list) or not raw_claims:
        fallback_count = sum(
            len(_string_list(task.get("acceptance_criteria") or task.get("acceptance")))
            for task in tasks
        )
        return ({
            "mode": "legacy_derived" if fallback_count else "unmapped",
            "claim_count": fallback_count,
            "mapped_claim_count": fallback_count,
            "diagnostics": [],
        }, [])

    known: set[str] = set()
    mandatory: set[str] = set()
    errors: list[str] = []
    for raw in raw_claims:
        if not isinstance(raw, dict):
            continue
        claim_id = str(raw.get("goal_claim_id") or raw.get("id") or "").strip()
        if not claim_id:
            continue
        if claim_id in known:
            errors.append(f"duplicate_goal_claim_id goal_claim_id={claim_id}")
        known.add(claim_id)
        if bool(raw.get("mandatory", True)):
            mandatory.add(claim_id)

    mapped: set[str] = set()
    for task in tasks:
        task_id = _task_id(task) or "<unknown>"
        seen: set[str] = set()
        for claim_id in _string_list(task.get("goal_claim_ids")):
            if claim_id in seen:
                errors.append(
                    f"duplicate_goal_claim_id task={task_id} goal_claim_id={claim_id}"
                )
                continue
            seen.add(claim_id)
            if claim_id not in known:
                errors.append(
                    f"unknown_goal_claim_id task={task_id} goal_claim_id={claim_id}"
                )
                continue
            mapped.add(claim_id)

    diagnostics = [
        {"code": "mandatory_claim_uncovered", "goal_claim_id": claim_id}
        for claim_id in sorted(mandatory - mapped)
    ]
    return ({
        "mode": "explicit",
        "claim_count": len(known),
        "mapped_claim_count": len(mapped),
        "diagnostics": diagnostics,
    }, errors)


def _task_id(raw: dict[str, Any]) -> str:
    return str(raw.get("task_id") or raw.get("id") or "").strip()


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []
