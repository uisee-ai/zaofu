"""Evidence from the authenticated CLI process, not a controller's guess."""
from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Any, Callable
from uuid import uuid4

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.artifact_read_capability import canonical_source_manifest_digest
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_json


class _ObservationWriter(EventWriter):
    def _latest_task_event(self, task_id: str) -> ZfEvent | None:
        from zf.runtime.artifact_context_events import artifact_context_events
        return next((event for event in reversed(artifact_context_events(self.event_log))
                     if event.task_id == task_id), None)


def record_worker_cli_load(state_dir: Path, log: EventLog, manifest: dict,
                           identity: dict, consumer: Callable[..., Any]) -> None:
    """Called only after capability authorization at an actual CLI entrypoint.

    This proves CLI code execution, not provider adoption or model compliance.
    Legacy reads without an operation binding cannot qualify a current attempt.
    """
    from zf.runtime.artifact_read_capability import authorize_artifact_read_from_environment
    from zf.runtime.runtime_version_projection import _source_revision
    from zf.runtime.load_receipt import consumer_receipts

    if not identity.get("operation_id"):
        return
    binding = {key: manifest.get(key, "") for key in (
        "workflow_run_id", "task_id", "attempt_id", "dispatch_id",
    )}
    binding.update(operation_id=identity["operation_id"], actor=identity["actor"],
                   manifest_digest=canonical_source_manifest_digest(manifest))
    functions = (consumer, authorize_artifact_read_from_environment, record_worker_cli_load)
    code = consumer_receipts(functions)
    body = {
        "schema_version": "worker-load.v1", "scope": "authenticated_worker_cli",
        "identity": binding, "pid": os.getpid(), "python": sys.executable,
        "source_root": str(Path(__file__).resolve().parents[3]),
        "source_revision": _source_revision(), "loaded_consumers": code,
        "limits": "CLI execution only; revision equality does not prove a clean tree or provider adoption.",
    }
    ref = write_sidecar_json(state_dir, f"diagnostics/worker-load/{uuid4().hex}.json", body,
        kind="worker-load", schema_version="worker-load.v1", created_by=identity["actor"], required=True)
    _ObservationWriter(log).append(ZfEvent(type="worker.runtime.observed", origin="kernel",
        task_id=binding["task_id"], actor=identity["actor"],
        correlation_id=binding["workflow_run_id"],
        payload={**binding, "worker_load_ref": ref}))


def worker_adoption_snapshot(state_dir: Path, events: list[ZfEvent], source: str) -> dict:
    """Only current canonical task bindings can select an observation."""
    from zf.runtime.workflow_operation import reduce_workflow_operations

    operations = reduce_workflow_operations(events)
    latest = {}
    for event in events:
        if event.type == "worker.runtime.observed" and event.origin == "kernel":
            key = (event.task_id, event.actor, event.payload.get("dispatch_id"))
            latest[key] = event
    rows = []
    for task in TaskStore(state_dir / "kanban.json").list_all():
        if task.status != "in_progress":
            continue
        row = {"task_id": task.id, "actor": task.assigned_to or "",
               "dispatch_id": task.active_dispatch_id or "", "status": "unknown",
               "source_revision": "", "comparison": "unknown", "event_id": ""}
        event = latest.get((task.id, task.assigned_to, task.active_dispatch_id))
        if event is not None and task.active_dispatch_id:
            ref = event.payload.get("worker_load_ref")
            try:
                hydrated = hydrate_sidecar_ref(state_dir, ref) if isinstance(ref, dict) else None
                body = hydrated.payload if hydrated is not None and hydrated.ok else None
                binding = body.get("identity", {}) if isinstance(body, dict) else {}
                operation = operations.get(binding.get("operation_id"), {})
                valid = (isinstance(body, dict) and body.get("schema_version") == "worker-load.v1"
                    and body.get("scope") == "authenticated_worker_cli" and ref.get("kind") == "worker-load"
                    and body.get("loaded_consumers") and binding.get("operation_id")
                    and operation.get("status") in {"requested", "reserved", "running"}
                    and operation.get("active_attempt_id") == binding.get("attempt_id")
                    and binding.get("attempt_id") and binding.get("manifest_digest")
                    and binding.get("task_id") == task.id and binding.get("actor") == task.assigned_to
                    and binding.get("workflow_run_id") == task.execution_binding.workflow_run_id
                    and all(event.payload.get(key) == value for key, value in binding.items()))
                if valid:
                    revision = str(body.get("source_revision") or "")
                    row.update(status="observed", source_revision=revision, event_id=event.id,
                        comparison=("same_revision" if revision == source else "different") if revision and source else "unknown")
            except (OSError, ValueError, AttributeError):
                pass  # Missing/corrupt latest evidence never falls back to old success.
        rows.append(row)
    return {"scope": "authenticated_worker_cli", "active": len(rows),
            "observed": sum(row["status"] == "observed" for row in rows), "tasks": rows}
