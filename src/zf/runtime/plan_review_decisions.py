"""Read-only decision register; separate from questions on an approval action."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zf.runtime.plan_preview_artifacts import read_preview_artifact


def decision_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = dict(total=len(items), pending=0, blocking=0, follow_up=0, resolved=0, unknown=0)
    for item in items:
        status = str(item.get("status") or "").lower()
        if item.get("blocking") is True:
            counts["blocking"] += 1
        if status in {"pending", "pending_decision", "pending_approval", "open"}:
            counts["pending"] += 1
        elif status in {"authorized_with_task_obligations", "startup_authorized_measurement_pending", "not_run"}:
            counts["follow_up"] += 1
        elif status in {"resolved", "closed", "approved", "confirmed", "done"}:
            counts["resolved"] += 1
        else:
            counts["unknown"] += 1
    return counts


def read_decision_review(
    state_dir: Path, *, project_root: Path | None,
    artifacts: list[dict[str, str]], action_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Read only the digest-bound task map, without broadening approval scope."""
    items = action_items
    available = False
    error = ""
    port = next((p for p in artifacts if p.get("name") == "task_map"), None)
    if port:
        try:
            value = json.loads(read_preview_artifact(
                port["ref"], state_dir=state_dir, project_root=project_root,
                digest=port.get("sha256", ""),
            ))
            register = value.get("owner_decision_items") if isinstance(value, dict) else None
            if isinstance(register, list):
                if any(not isinstance(item, dict) for item in register):
                    raise ValueError("owner_decision_items 包含非对象条目，无法确认完整登记")
                items = register
                available = True
        except (OSError, ValueError, UnicodeError) as exc:
            error = f"决定登记读取失败：{exc}"
    return {"items": items, "counts": decision_counts(items), "available": available, "error": error}
