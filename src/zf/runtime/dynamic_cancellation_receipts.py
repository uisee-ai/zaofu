"""Derive released continuation reservations from exact durable stop receipts."""

from collections.abc import Mapping

from zf.core.events.model import ZfEvent


def cancelled_tree_released(
    operations: Mapping[str, dict], root: dict, events: list[ZfEvent],
) -> bool:
    if root.get("status") not in {"cancelled", "superseded", "failed"}:
        return False
    root_id = root["operation_id"]
    tree = {root_id}
    while True:
        children = {key for key, row in operations.items() if row.get("parent_operation_id") in tree}
        if children <= tree:
            break
        tree.update(children)
    # The root's dispatch is a logical fanout invocation, not a provider PID.
    # Require closure of every invocation before considering its worker receipts.
    fanouts = {
        event.payload.get("fanout_id") for event in events
        if event.type == "fanout.started"
        and (event.payload.get("trigger_payload") or {}).get("workflow_operation_id") in tree
    } - {None, ""}
    closed = {event.payload.get("fanout_id") for event in events if event.type == "fanout.cancelled"}
    if not fanouts or not fanouts <= closed:
        return False
    receipts = {
        (event.actor, event.payload.get("dispatch_id"))
        for event in events if event.type == "worker.state.changed"
        and event.payload.get("provider_stop_confirmed") is True
        and event.payload.get("to") == "idle"
        and event.payload.get("source") in {"run_cancel_reconciliation", "superseded_reader_fanout_closeout"}
    }
    receipts.update(
        (row.get("role_instance"), row.get("dispatch_id"))
        for key, row in operations.items() if key in tree - {root_id}
        and row.get("status") == "settled" and row.get("admitted_call_result_ref")
    )
    for event in events:
        payload = event.payload
        if event.type in {"workflow.operation.started", "workflow.operation.retry_started"} and payload.get("operation_id") in tree - {root_id}:
            dispatch = payload.get("dispatch_id")
            role = payload.get("role_instance")
        elif event.type == "fanout.child.dispatched" and payload.get("fanout_id") in fanouts:
            dispatch = payload.get("run_id")
            role = payload.get("role_instance")
        else:
            continue
        if not dispatch or not role or (role, dispatch) not in receipts:
            return False
    return True
