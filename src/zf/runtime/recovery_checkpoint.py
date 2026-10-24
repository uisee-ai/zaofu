"""Current-attempt checkpoint references for existing recovery consumers."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from zf.core.events import ZfEvent
from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref


def maintenance_checkpoint_facts(
    state_dir: Path, events: Sequence[ZfEvent], *, task_id: str,
    attempt_facts: Mapping[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "unavailable", "authorizes_continuation": False,
        "reason": "no captured checkpoint bound to the current canonical attempt",
    }
    identity = attempt_facts.get("identity") or {}
    if attempt_facts.get("status") != "bound":
        return result
    keys = ("run_id", "task_id", "operation_id", "attempt_id", "lease_id", "dispatch_id", "instance_id")
    for event in reversed(events):
        if event.type != "worker.checkpointed" or event.task_id != task_id:
            continue
        payload = event.payload
        prior = payload.get("attempt_identity") or {}
        prior_identity = prior.get("identity") or {}
        if prior.get("status") != "bound" or any(
            not identity.get(k) or prior_identity.get(k) != identity[k] for k in keys
        ):
            continue
        descriptor = payload.get("workspace_snapshot_ref")
        # Do not silently fall back to an older snapshot when the newest
        # matching checkpoint is incomplete or corrupt.
        if not isinstance(descriptor, dict):
            return result
        try:
            hydrated = hydrate_sidecar_ref(state_dir, descriptor)
        except (OSError, SidecarRefError):
            result["reason"] = "current checkpoint sidecar is missing or invalid"
            return result
        snapshot = hydrated.payload
        if (not hydrated.ok or not isinstance(snapshot, dict)
                or snapshot.get("schema_version") != "maintenance-workspace.v1"
                or snapshot.get("status") != "captured"
                or snapshot.get("worktree_root") != payload.get("worktree_root")):
            result["reason"] = "current checkpoint workspace was not captured"
            return result
        return {
            "status": "bound", "authorizes_continuation": False,
            "checkpoint_id": payload.get("checkpoint_id"), "event_id": event.id,
            "workspace_snapshot_ref": descriptor, "worktree_root": snapshot["worktree_root"],
            "head": snapshot.get("head"), "provider_quiescence": "unverified",
            "reason": "verified evidence reference; revalidate workspace, execution rights and lease before use",
        }
    return result
