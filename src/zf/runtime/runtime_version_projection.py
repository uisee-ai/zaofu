"""Expose the last startup receipt separately from the checkout's revision."""
from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any, Iterable

from zf.core.events.model import ZfEvent
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _source_revision() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[3],
            capture_output=True, text=True, timeout=2, check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def runtime_version_snapshot(state_dir: Path, events: Iterable[ZfEvent]) -> dict[str, Any]:
    """Read-only evidence; matching revisions do not prove a worker deployment."""
    from zf.runtime.worker_load_receipt import worker_adoption_snapshot

    events = list(events)
    source = _source_revision()
    result: dict[str, Any] = {
        "task_adoption": worker_adoption_snapshot(state_dir, events, source),
        "source_revision": source, "loaded_revision": "", "comparison": "unknown",
        "lifecycle": "unknown", "event_id": "", "receipt_status": "unavailable",
    }
    started = None
    for event in events:
        if event.type == "loop.started":
            started = event
            result["lifecycle"] = "started"
        elif event.type == "loop.stopped":
            result["lifecycle"] = "stopped"
    if started is None:
        return result
    result["event_id"] = started.id
    descriptor = started.payload.get("runtime_load_ref")
    if not isinstance(descriptor, dict) or descriptor.get("kind") != "runtime-load":
        return result
    try:
        hydrated = hydrate_sidecar_ref(state_dir, descriptor)
        body = hydrated.payload
        if not hydrated.ok or not isinstance(body, dict) or body.get("schema_version") != "runtime-load.v1":
            return result
    except (OSError, ValueError):
        return result
    revision = str(body.get("source_revision") or "")
    result.update(
        loaded_revision=revision, receipt_status="available",
        comparison=("same_revision" if source == revision else "different") if source and revision else "unknown",
    )
    return result
