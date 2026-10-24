"""Stable identity and path resolution for controlled workflow requests."""

from __future__ import annotations

import hashlib
from pathlib import Path
from collections.abc import Mapping
from typing import Any


def request_source_ref(project_root: Path, state_dir: Path, payload: Mapping[str, Any]) -> str:
    """Resolve a request's source without losing a bound Channel identity."""
    raw = str(payload.get("source_ref") or "").strip() or str(payload.get("artifact_ref") or "").strip()
    contract = payload.get("task_input_contract")
    return project_ref(project_root, state_dir, raw,
                       task_input_contract=contract if isinstance(contract, Mapping) else None)


def stable_request_id(project_root: Path, requested_event_id: str, objective: str) -> str:
    digest = hashlib.sha256(
        f"{project_root.resolve()}\0{requested_event_id}\0{objective}".encode("utf-8")
    ).hexdigest()[:16]
    return f"workflow-{digest}"


def project_ref(
    project_root: Path, state_dir: Path, raw: str,
    *, task_input_contract: Mapping[str, Any] | None = None,
) -> str:
    if not raw:
        return ""
    contract = task_input_contract or {}
    evidence = contract.get("evidence_contract") or {}
    if (isinstance(evidence, Mapping) and evidence.get("channel_id")
            and raw == contract.get("source_ref")):
        # A Channel ref is relative to state_dir, not project_root. Keep its
        # canonical identity for coverage; intake subsequently hydrates and
        # verifies its digest before materializing the complete reader source.
        return raw
    path = Path(raw).expanduser()
    if path.is_absolute():
        return str(path)
    for candidate in (project_root / path, state_dir / path):
        if candidate.exists():
            return str(candidate)
    return raw
