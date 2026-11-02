"""Repair explicit goal mappings in a mechanically identical gap replay."""

from typing import Any


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
