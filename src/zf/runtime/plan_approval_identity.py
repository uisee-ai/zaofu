"""Stable approval identity for an execution scope and its reviewed artifact."""

from __future__ import annotations

import hashlib
from typing import Any


def plan_approval_fingerprint(
    *,
    stage_id: str,
    pdd_id: str,
    task_items: list[Any],
    artifact_digest: str = "",
) -> str:
    task_ids = sorted(
        str(
            getattr(item, "task_id", "")
            or (item.get("task_id") if isinstance(item, dict) else "")
            or ""
        )
        for item in task_items
    )
    identity = [stage_id, pdd_id, *task_ids]
    if artifact_digest:
        identity.append(artifact_digest)
    return hashlib.sha256("|".join(identity).encode("utf-8")).hexdigest()[:16]
