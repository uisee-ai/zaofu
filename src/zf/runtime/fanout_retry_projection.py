"""Reopen a failed reader projection only for its admitted fresh dispatch."""


def project_reader_retry(manifest: dict, child: dict, payload: dict, event_id: str) -> None:
    aggregate = manifest.get("aggregate") or {}
    incoming = payload.get("payload")
    incoming = incoming if isinstance(incoming, dict) else {}
    previous = child.get("payload")
    previous = previous if isinstance(previous, dict) else {}
    old_operation = str(child.get("operation_id") or previous.get("operation_id") or "")
    new_operation = str(payload.get("operation_id") or incoming.get("operation_id") or "")
    if not (
        manifest.get("topology") == "fanout_reader"
        and aggregate.get("status") in {"failed", "timed_out"}
        and child.get("status") == "failed"
        and old_operation and new_operation and new_operation != old_operation
        and child.get("run_id") and payload.get("run_id")
        and incoming.get("retry_of_operation_id") == old_operation
        and payload.get("retry_of_run_id") == child.get("run_id")
        and payload.get("run_id") != child.get("run_id")
    ):
        return
    # The Kernel-controlled retry already passed run/currentness/identity gates.
    # Its dispatch is durable evidence of new work; the old aggregate remains
    # in the ledger and cannot hide this attempt from recovery or the barrier.
    manifest["status"] = "started"
    manifest["aggregate"] = {
        "status": "pending", "last_event_id": event_id,
        "recovered_from_event_id": aggregate.get("last_event_id", ""),
        "recovered_from_aggregate_status": aggregate["status"],
    }
    child.update(reason="", evidence={})
