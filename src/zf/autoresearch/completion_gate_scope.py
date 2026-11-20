"""Read-only completion evidence matching; never a product admission gate."""
from pathlib import Path

from zf.core.events import ZfEvent
from zf.runtime.run_scope import event_run_id
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def completion_scope(event: ZfEvent, aliases: dict[str, str]) -> tuple[str, str]:
    explicit = str(event.payload.get("workflow_run_id") or "")
    run_id = (aliases.get(explicit, explicit) if explicit else
              event_run_id(event, aliases=aliases) or event.correlation_id
              or str(event.payload.get("run_id") or ""))
    return str(event.task_id or event.payload.get("task_id") or ""), run_id


def gate_matches_completion(gate: ZfEvent, done: ZfEvent, *, aliases: dict[str, str],
                            state_dir: Path) -> bool:
    scope = completion_scope(done, aliases)
    task_id = scope[0]
    if not task_id or completion_scope(gate, aliases) != scope:
        return False
    for key in ("task_map_generation", "contract_revision", "dispatch_id", "attempt_id",
                "candidate_generation", "target_commit"):
        if done.payload.get(key) and gate.payload.get(key) != done.payload[key]:
            return False
    if gate.type != "integration.queue.integrated":
        return True
    # Typed Task Pipeline completion cites the exact admitted integration
    # receipt. A global Verify pass or merely having a JSON file is insufficient.
    descriptor = done.payload.get("integration_receipt_ref")
    digest = done.payload.get("integration_receipt_digest")
    if (done.origin != "kernel" or gate.origin != "kernel"
            or done.payload.get("authority") != "task_integration_receipt"
            or done.payload.get("integration_event_id") != gate.id
            or done.causation_id != gate.id or not isinstance(descriptor, dict)
            or not digest or descriptor.get("sha256") != digest
            or gate.payload.get("receipt_ref") != descriptor
            or gate.payload.get("receipt_digest") != digest):
        return False
    try:
        body = hydrate_sidecar_ref(state_dir, descriptor).payload
    except (OSError, ValueError):
        return False
    return isinstance(body, dict) and body.get("task_id") == task_id and all(
        done.payload.get(k) and body.get(k) == done.payload[k]
        for k in ("workflow_run_id", "task_map_generation")
    )
