"""Carry explicitly authored optional task declarations through gap serialization."""

from copy import deepcopy
from typing import Any, Mapping


def preserve_gap_task_declarations(item: dict[str, Any], raw: Mapping[str, Any]) -> None:
    """Preserve supplied content without inferring scope or inheriting old semantics.

    The normal task-map and writer admission paths still validate these values.
    Explicit empty or invalid declarations must not silently become absent.
    """
    for field in (
        "description", "source_excerpt", "source_ref", "owner_decision_ids",
        "lane_affinity", "skills_required", "explicit_non_goals", "exclusive_files",
    ):
        if field in raw:
            item[field] = deepcopy(raw[field])
    if isinstance(raw.get("verification"), dict):
        item["verification"] = deepcopy(raw["verification"])

    root_owner_class = str(raw.get("root_owner_class") or "").strip()
    if root_owner_class:
        item["root_owner_class"] = root_owner_class
    base_commit = str(raw.get("base_commit") or "").strip()
    if base_commit:
        item["base_commit"] = base_commit
