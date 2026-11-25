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


def reader_result_dispatch_id(child: dict, payload: dict) -> str | None:
    """Resolve the dispatch that may consume this result, including signed replacements."""
    request = child.get("payload")
    if not (isinstance(request, dict) and request.get("retry_of_operation_id")
            and request.get("operation_id")):
        return str(payload.get("run_id") or child.get("run_id") or "")
    dispatch_id = child.get("run_id")
    current = {
        "operation_id": request["operation_id"],
        "request_hash": request.get("request_hash"),
        "attempt_id": request.get("attempt_id"),
        "active_attempt_id": request.get("attempt_id"),
    }
    supplied = {key: payload[key] for key in current if payload.get(key)}
    if supplied:
        # Signed results and provider-stop projections report WRC identity. The
        # child owns the dispatch ID used for releasing the worker and closing the barrier.
        return dispatch_id if all(current[key] == value for key, value in supplied.items()) else None
    run_id = payload.get("run_id")
    if not run_id and request.get("result_protocol_mode") != "blocking":
        return dispatch_id  # Legacy non-blocking reports can omit per-attempt identity.
    return dispatch_id if dispatch_id and run_id == dispatch_id else None
