"""Append-only goal replan history artifacts.

Goal closure may amend the plan after verification evidence finds a gap. This
module writes the durable audit trail for that amendment without touching
kernel runtime truth files.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def append_goal_replan_history_entry(
    *,
    state_dir: Path,
    project_root: Path | None,
    history_ref: str,
    entry: Mapping[str, Any],
) -> dict[str, Any]:
    """Append one JSONL replan-history row and return its resolved path."""

    ref = str(history_ref or "").strip()
    if not ref:
        raise ValueError("history_ref is required")
    path = _resolve_history_ref(
        ref,
        state_dir=Path(state_dir),
        project_root=Path(project_root) if project_root is not None else None,
    )
    payload = dict(entry)
    payload.setdefault("schema_version", "goal-replan-history-entry.v1")
    payload.setdefault("recorded_at", datetime.now(timezone.utc).isoformat())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return {
        "replan_history_ref": ref,
        "replan_history_path": str(path),
        "entry": payload,
    }


def _resolve_history_ref(
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
    base = project_root if project_root is not None else Path(state_dir).parent
    resolved = (base / path).resolve()
    try:
        resolved.relative_to(base.resolve())
    except ValueError as exc:
        raise ValueError(f"history_ref escapes project root: {ref}") from exc
    return resolved
