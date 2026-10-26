"""Stable identity for pending Plan approval requests."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence


def plan_approval_fingerprint(
    *,
    stage_id: str,
    pdd_id: str,
    task_items: Sequence[object],
    identity: Mapping[str, object] | None = None,
) -> str:
    """Distinguish task-map generations while preserving duplicate suppression."""

    task_ids = sorted(
        str(
            getattr(item, "task_id", "")
            or (item.get("task_id") if isinstance(item, Mapping) else "")
            or ""
        )
        for item in task_items
    )
    identity = identity or {}
    identity_parts = [
        str(identity.get(key) or "")
        for key in (
            "workflow_run_id",
            "task_map_generation",
            "plan_artifact_package_digest",
            "requirement_spec_digest",
        )
    ]
    raw = "|".join([stage_id, pdd_id, *identity_parts, *task_ids])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
