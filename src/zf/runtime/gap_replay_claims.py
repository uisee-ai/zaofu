"""Validate goal mapping continuity and repair mechanically identical replays."""

from typing import Any


def require_replacement_goal_coverage(
    previous_by_id: dict[str, dict[str, Any]],
    current: list[dict[str, Any]],
    superseded_task_ids: list[str],
) -> None:
    """Check declared ownership links; semantic allocation remains with agents."""

    def claims(tasks: list[dict[str, Any]]) -> set[str]:
        result = set()
        for task in tasks:
            values = task.get("goal_claim_ids") or []
            if isinstance(values, str):
                values = [values]
            if isinstance(values, list):
                result.update(
                    str(value).strip() for value in values if str(value).strip()
                )
        return result

    removed = [
        previous_by_id[task_id]
        for task_id in superseded_task_ids
        if task_id in previous_by_id
    ]
    missing = sorted(claims(removed) - claims(current))
    if missing:
        raise ValueError(
            "gap replacement loses explicit goal_claim_ids: "
            + ", ".join(missing)
            + "; declare successor ownership without removing predecessor goals"
        )


def repair_replayed_goal_claims(
    existing: dict[str, Any], proposed: dict[str, Any], *, has_lineage: bool,
) -> bool:
    requested = proposed.get("goal_claim_ids") or []
    if not requested:
        return False
    task_id = existing["task_id"]
    current = existing.get("goal_claim_ids") or []
    if current:
        if current != requested:
            raise ValueError(f"gap replay goal claim mismatch: {task_id}")
        return False
    if not has_lineage:
        raise ValueError(f"gap replay lacks successor lineage: {task_id}")
    # Dependencies and wave may have been mechanically inherited and raised
    # while amending the predecessor; keep those existing values unchanged.
    excluded = {"goal_claim_ids", "blocked_by", "wave"}
    keys = (existing.keys() | proposed.keys()) - excluded
    if any(existing.get(key) != proposed.get(key) for key in keys):
        raise ValueError(f"gap replay contract mismatch: {task_id}")
    if not set(proposed.get("blocked_by") or []).issubset(existing.get("blocked_by") or []):
        raise ValueError(f"gap replay dependency mismatch: {task_id}")
    existing["goal_claim_ids"] = list(requested)
    return True
