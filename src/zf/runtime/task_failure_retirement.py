"""Retire task diagnostics from later canonical terminal facts, never activity."""

from zf.core.events.model import ZfEvent


def task_failure_retired(events: list[ZfEvent], source: ZfEvent) -> bool | None:
    """None leaves non-task failures with their existing progress policy."""
    if source.type == "human.escalate":
        failure = _resident_diagnosed_failure(events, source)
        return task_failure_retired(events, failure) if failure is not None else None
    if source.type not in {"task.attempt.failed", "task.pipeline.verify.failed"}:
        return None
    identity = _identity(source)
    if identity is None:
        return False
    seen_source = False
    admitted_maps: dict[str, set[str]] = {}
    for event in events:
        if event.origin == "kernel" and event.type == "task.pipeline.generation.admitted":
            ref = str(event.payload.get("task_map_ref") or "")
            explicit_run = event.payload.get("workflow_run_id") or event.payload.get("run_id")
            if ref:
                admitted_maps.setdefault(ref, set()).add(_run_id(event) if explicit_run else "")
    for event in events:
        if event.id == source.id:
            seen_source = True
            continue
        if not seen_source or event.origin != "kernel":
            continue
        if event.type == "task.done" and _identity(event) == identity:
            return True
        if (event.type == "task.superseded"
                and event.payload.get("status") == "cancelled"
                and event.payload.get("superseded_by_task_map_ref")):
            terminal_identity = _identity(event)
            if terminal_identity is None and not any((
                event.payload.get("workflow_run_id"), event.payload.get("run_id"),
                event.correlation_id,
            )) and event.payload.get("source") == "writer_task_map_adoption":
                # Historical writer events omitted Run identity. Only an
                # admitted exact replacement map may resolve that missing value.
                # Writer cancellation can precede generation admission.
                runs = admitted_maps.get(str(event.payload["superseded_by_task_map_ref"]), set())
                task_id = _task_id(event)
                if len(runs) == 1 and task_id:
                    run_id = next(iter(runs))
                    terminal_identity = (run_id, task_id) if run_id else None
            if terminal_identity == identity:
                return True
    return False


def _resident_diagnosed_failure(events: list[ZfEvent], source: ZfEvent) -> ZfEvent | None:
    """Resolve only a kernel-delivered diagnosis of an exact canonical failure."""
    from zf.runtime.recovery_step import bind_recommendation_to_request

    payload = source.payload
    if (source.origin != "kernel" or payload.get("owner_route") != "run_manager"
            or payload.get("failure_class") != "resident_agent_recommendation"
            or payload.get("action") != "agent-recommendation"):
        return None
    before = events[:next((i for i, e in enumerate(events) if e.id == source.id), 0)]
    refs = payload.get("source_event_ids") or []
    recommendations = [e for e in before if e.id in refs
                       and e.type == "run.manager.agent.recommendation"]
    if len(recommendations) != 1:
        return None
    recommendation = recommendations[0]
    prior = before[:before.index(recommendation)]
    request_id = recommendation.payload.get("request_id")
    requests = [e for e in prior if e.type == "run.manager.action.effect.pending"
                and request_id and e.payload.get("request_id") == request_id]
    if len(requests) != 1 or requests[0].origin != "kernel":
        return None
    request = requests[0]
    deliveries = [e for e in prior if e.type == "run.manager.resident.prompted"
                  and e.payload.get("request_id") == request_id]
    if not deliveries or any(e.origin != "kernel" for e in deliveries):
        return None
    try:
        bound = bind_recommendation_to_request(prior, recommendation, recommendation.payload)
    except ValueError:
        return None
    identity = _identity(source)
    if not identity or identity != _identity(request):
        return None
    if (bound.get("workflow_run_id"), bound.get("task_id")) != identity:
        return None
    failure_ref = request.payload.get("source_event_id")
    failures = [e for e in prior[:prior.index(request)] if e.origin == "kernel"
                and e.type in {"task.attempt.failed", "task.pipeline.verify.failed"}
                and failure_ref and (e.id == failure_ref or e.payload.get("source_event_id") == failure_ref)
                and _identity(e) == identity]
    return failures[0] if len(failures) == 1 else None


def _task_id(event: ZfEvent) -> str:
    if event.task_id and event.payload.get("task_id") not in (None, "", event.task_id):
        return ""
    return str(event.task_id or event.payload.get("task_id") or "")


def _run_id(event: ZfEvent) -> str:
    runs = {str(event.payload.get(key) or "") for key in ("workflow_run_id", "run_id")}
    runs.discard("")
    if len(runs) > 1:
        return ""
    return next(iter(runs)) if runs else str(event.correlation_id or "")


def _identity(event: ZfEvent) -> tuple[str, str] | None:
    run_id, task_id = _run_id(event), _task_id(event)
    return (run_id, task_id) if run_id and task_id else None
