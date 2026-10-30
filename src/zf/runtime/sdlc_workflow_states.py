"""Read-only execution state for compiled Workflow structure nodes."""

from typing import Any

from zf.core.events.model import ZfEvent


def bind_pipeline_containers(nodes: dict, operations: dict, events: list[tuple[int, ZfEvent]]) -> None:
    """Bind presentation containers from Kernel facts, preserving operation identity."""
    bindings = {}
    for _, event in events:
        if event.origin != "kernel" or event.type != "task.pipeline.generation.admitted":
            continue
        payload = event.payload
        stage = str(payload.get("stage_id") or "")
        run = str(payload.get("workflow_run_id") or "")
        generation = str(payload.get("task_map_generation") or "")
        if run and generation and "workflow:stage:" + stage in nodes:
            bindings[(run, generation)] = (stage, event.id)
    for operation in operations.values():
        if not operation.get("pipeline_key"):
            continue
        binding = bindings.get((operation.get("workflow_run_id"), operation.get("task_map_generation")))
        if binding:
            operation["compiled_stage_id"], operation["compiled_stage_binding_event_id"] = binding


def project_pipeline_container_states(nodes: dict, events: list[tuple[int, ZfEvent]], run_id: str) -> None:
    latest = {}
    for _, event in events:
        if event.origin != "kernel":
            continue
        payload = event.payload
        key = (payload.get("workflow_run_id"), payload.get("task_map_generation"))
        if not all(key) or key[0] != run_id:
            continue
        if event.type == "task.pipeline.generation.admitted":
            identity = "workflow:stage:" + str(payload.get("stage_id") or "")
            if identity in nodes:
                latest[identity] = (key, False, event.id)
        elif event.type == "candidate.ready":
            for identity, (bound, _, _) in list(latest.items()):
                if key == bound:
                    latest[identity] = (key, True, event.id)
    for identity, (_, frozen, event_id) in latest.items():
        # An ended Impl call does not complete the encompassing Task Pipeline.
        # Freeze is its mechanical boundary; downstream Verify/Judge remain separate.
        if frozen or nodes[identity]["status"] in {"settled", "completed"}:
            nodes[identity]["status"] = "completed" if frozen else "running"
        nodes[identity]["data"]["pipeline_status_event_id"] = event_id


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
