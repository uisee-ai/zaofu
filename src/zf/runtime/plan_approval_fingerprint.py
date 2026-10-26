"""Stable identity for pending Plan approval requests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from collections.abc import Mapping, Sequence


def plan_approval_fingerprint(
    *,
    stage_id: str,
    pdd_id: str,
    task_items: Sequence[object],
    identity: Mapping[str, object] | None = None,
) -> str:
    """Distinguish task-map generations while preserving duplicate suppression."""

    tasks = [dict(item) if isinstance(item, Mapping) else
             asdict(item) if is_dataclass(item) else vars(item)
             for item in task_items]
    bound = {key: value for key, value in (identity or {}).items() if value}
    raw = json.dumps({
        "stage_id": stage_id, "pdd_id": pdd_id, "binding": bound,
        "tasks": sorted(tasks, key=lambda item: str(item.get("task_id") or "")),
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "plan-approval.v2:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
