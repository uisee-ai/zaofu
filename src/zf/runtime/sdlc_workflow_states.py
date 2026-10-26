"""Read-only execution state for compiled Workflow structure nodes."""

from typing import Any

from zf.core.events.model import ZfEvent


def project_structure_states(
    nodes: dict[str, dict[str, Any]],
    operations: dict[str, dict[str, Any]],
    events: list[tuple[int, ZfEvent]],
) -> None:
    roles: dict[str, list[dict[str, Any]]] = {}
    for operation in operations.values():
        identity = "workflow:role:" + str(operation.get("role_instance") or "")
        if identity in nodes:
            roles.setdefault(identity, []).append(operation)
    for identity, rows in roles.items():
        # A preregistered queued child does not hide the role's running child.
        running = [row for row in rows if row.get("status") == "running"]
        current = (running or rows)[-1]
        nodes[identity]["status"] = current["status"]
        nodes[identity]["data"].update(
            operation_ids=[row["operation_id"] for row in rows],
            current_operation_id=current["operation_id"],
        )

    stages: dict[str, str] = {}
    latest: dict[str, str] = {}
    for _, event in events:
        trigger_node = nodes.get("workflow:event:" + event.type)
        if trigger_node is not None:
            trigger_node["status"] = "reported"
            trigger_node["data"].setdefault("source_event_ids", []).append(event.id)
        if event.origin != "kernel":
            continue
        payload = event.payload
        fanout_id = str(payload.get("fanout_id") or "")
        if not fanout_id:
            continue
        if event.type == "fanout.started":
            stage_id = str(payload.get("stage_id") or "").split(":", 1)[0]
            stages[fanout_id] = stage_id
            latest[stage_id] = fanout_id
            status = "pending"
        elif event.type == "fanout.synth.dispatched":
            status = "running"
        elif event.type in {"fanout.aggregate.completed", "fanout.synth.completed"}:
            status = str(payload.get("status") or "")
            if status not in {"completed", "failed", "cancelled", "superseded"}:
                continue
        elif event.type in {"fanout.cancelled", "fanout.timed_out"}:
            status = "cancelled" if event.type == "fanout.cancelled" else "failed"
        else:
            continue
        stage_id = stages.get(fanout_id, "")
        identity = "workflow:aggregate:" + stage_id
        if identity in nodes and latest.get(stage_id) == fanout_id:
            nodes[identity]["status"] = status
            nodes[identity]["data"].update(
                fanout_id=fanout_id, status_source_event_id=event.id,
            )
