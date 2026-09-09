"""Stable identity and path resolution for controlled workflow requests."""

from __future__ import annotations

import hashlib
from pathlib import Path


def stable_request_id(project_root: Path, requested_event_id: str, objective: str) -> str:
    digest = hashlib.sha256(
        f"{project_root.resolve()}\0{requested_event_id}\0{objective}".encode("utf-8")
    ).hexdigest()[:16]
    return f"workflow-{digest}"


def project_ref(project_root: Path, state_dir: Path, raw: str) -> str:
    if not raw:
        return ""
    path = Path(raw).expanduser()
    if path.is_absolute():
        return str(path)
    for candidate in (project_root / path, state_dir / path):
        if candidate.exists():
            return str(candidate)
    return raw
