"""Stable opt-in machine output for read-oriented CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


def result_envelope(
    *,
    command: str,
    data: Any,
    context: Any | None = None,
    ok: bool = True,
    error_code: str = "",
    error: str = "",
    next_actions: Iterable[str] = (),
) -> dict[str, Any]:
    project_root = Path(getattr(context, "project_root", Path.cwd()))
    state_dir = Path(getattr(context, "state_dir", project_root / ".zf"))
    config = getattr(context, "config", None)
    project = getattr(config, "project", None)
    project_id = str(
        getattr(project, "name", "")
        or getattr(project, "id", "")
        or project_root.name
    )
    return {
        "schema_version": "zf.cli.result.v1",
        "command": command,
        "ok": bool(ok),
        "identity": {
            "project_id": project_id,
            "project_root": str(project_root),
            "state_dir": str(state_dir),
        },
        "data": data,
        "error": (
            {"code": error_code or "command_failed", "message": error}
            if not ok else None
        ),
        "next_actions": [str(item) for item in next_actions if str(item).strip()],
    }


def print_result(**kwargs: Any) -> None:
    print(json.dumps(result_envelope(**kwargs), ensure_ascii=False, indent=2))


__all__ = ["print_result", "result_envelope"]
