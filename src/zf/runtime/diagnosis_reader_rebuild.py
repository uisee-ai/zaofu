"""Show the resident an exact failed Reader retry without authorizing it."""

from pathlib import Path
from typing import Any

from zf.core.events import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.failure_kind import FAILURE_KIND_INFRA, failure_kind_from_payload
from zf.runtime.reader_rebuild_admission import reader_rebuild_error
from zf.runtime.run_continuation import bind_action_dependencies, blocking_decisions_for_action
from zf.runtime.workflow_operation import reduce_workflow_operations


def _diagnosed_child(events: list[ZfEvent], sources: set[str], run_id: str) -> ZfEvent | None:
    """An aggregate can identify a single failed child at the time of its verdict."""
    failures: dict[str, ZfEvent] = {}
    child_by_id: dict[str, ZfEvent] = {}
    attempt_failures: list[ZfEvent] = []
    child_states: dict[str, dict[str, ZfEvent]] = {}
    for event in events:
        p = event.payload
        if event.origin != "kernel" or (p.get("workflow_run_id") or p.get("trace_id")
                                        or event.correlation_id) != run_id:
            continue
        fanout = p.get("fanout_id")
        if fanout and p.get("child_id") and event.type in (
            "fanout.child.dispatched", "fanout.child.completed", "fanout.child.failed",
        ):
            child_states.setdefault(fanout, {})[p["child_id"]] = event
            if event.type == "fanout.child.failed":
                child_by_id[event.id] = event
        if event.id not in sources:
            continue
        if event.type == "task.attempt.failed" and p.get("source_event_id"):
            attempt_failures.append(event)
        elif event.type == "fanout.child.failed":
            failures[event.id] = event
        elif event.type == "workflow.operation.failed":
            cause = child_by_id.get(event.causation_id)
            if cause:
                failures[cause.id] = cause
        elif (fanout and event.type.endswith(".failed") and p.get("status") == "failed"
              and failure_kind_from_payload(p) == FAILURE_KIND_INFRA):
            for child in child_states.get(fanout, {}).values():
                if child.type == "fanout.child.failed":
                    failures[child.id] = child
    if attempt_failures:
        from zf.runtime.candidate_reader_recovery_owner import candidate_reader_outcomes

        by_id = {e.id: e for e in events}
        for attempt in attempt_failures:
            # The task attempt mirrors settlement before fanout records its child terminal.
            # Its own attempt_id belongs to TaskAttempt, not the Reader's WRC.
            p = attempt.payload
            source = by_id.get(p["source_event_id"])
            settlement = by_id.get(attempt.causation_id)
            if (not source or not settlement or settlement.type != "workflow.operation.settled"
                    or settlement.origin != "kernel" or source.causation_id != settlement.id
                    or p.get("admission_event_id") != settlement.id
                    or source.payload.get("semantic_submit_admission_event_id") != settlement.causation_id
                    or source.payload.get("request_hash") != settlement.payload.get("request_hash")
                    or source.type != p.get("source_event_type") or not source.type.endswith(".child.failed")
                    or not all(source.payload.get(k) and p.get(k) == source.payload[k]
                               for k in ("workflow_run_id", "task_id", "operation_id", "dispatch_id"))
                    or settlement.payload.get("operation_id") != p["operation_id"]
                    or settlement.payload.get("workflow_run_id") != run_id):
                continue
            for child in child_by_id.values():
                if child.causation_id != source.id:
                    continue
                child_id = child.payload["child_id"]
                outcomes = candidate_reader_outcomes(events, {child_id: child.id}, child.payload["fanout_id"], run_id)
                if outcomes.get("abstained", {}).get(child_id) == child.id:
                    failures[child.id] = child
    if len(failures) != 1:
        return None
    failure = next(iter(failures.values()))
    p = failure.payload
    if (failure_kind_from_payload(p) != FAILURE_KIND_INFRA
            or p.get("semantic_verdict") in {"passed", "rejected"}):
        return None
    return failure


def diagnosis_reader_rebuild(
    state_dir: Path, events: list[ZfEvent], diagnosis: dict[str, Any],
) -> dict[str, Any]:
    if diagnosis.get("request_kind") != "recovery_diagnosis":
        return {}
    run_id = diagnosis.get("workflow_run_id") or diagnosis.get("run_id")
    sources = set(diagnosis.get("source_event_ids") or [])
    if not run_id or not sources:
        return {}
    failure = _diagnosed_child(events, sources, run_id)
    if not failure:
        return {}
    p = failure.payload
    dispatch = next((e for e in reversed(events) if e.origin == "kernel"
                     and e.type == "fanout.child.dispatched"
                     and e.payload.get("fanout_id") == p.get("fanout_id")
                     and e.payload.get("child_id") == p.get("child_id")
                     and e.payload.get("run_id") == p.get("run_id")), None)
    if not dispatch:
        return {}
    operation = reduce_workflow_operations(events).get(dispatch.payload.get("operation_id"), {})
    if (operation.get("operation_type") != "fanout_reader_child"
            or operation.get("semantic_verdict") in {"passed", "rejected"}
            or any(e.type == "workflow.operation.failed" and e.id in sources
                   and e.payload.get("operation_id") != operation.get("operation_id") for e in events)
            or any(diagnosis.get(key) and diagnosis[key] != operation.get(field)
                   for key, field in (("target_operation_id", "operation_id"), ("task_id", "task_id"),
                                      ("active_attempt_id", "active_attempt_id")))):
        return {}
    payload = {"fanout_id": p.get("fanout_id"), "fanout_child_id": p.get("child_id"),
               "workflow_run_id": run_id, "target_operation_id": operation["operation_id"],
               "active_attempt_id": operation.get("active_attempt_id"), "source_event_id": failure.id,
               "task_id": operation.get("task_id")}
    result: dict[str, Any] = {"failure_event_id": failure.id, "dispatch_event_id": dispatch.id,
              "transport_run_id": dispatch.payload.get("run_id"), "as_of_event_id": events[-1].id}
    error = reader_rebuild_error(state_dir, events, payload)
    result["binding_preflight"] = "blocked" if error else "passed"
    if error:
        result["reason"] = error

    from zf.runtime.run_manager import _pending_human_decisions

    action = {"action": "child-rebuild", **payload}
    try:
        scoped = bind_action_dependencies([action], TaskStore(state_dir / "kanban.json").list_all())[0]
        blockers = blocking_decisions_for_action(_pending_human_decisions(events), scoped, events=events)
    except (OSError, ValueError):
        return {**result, "human_gate_status": "unknown"}
    result["human_gate_status"] = "blocked" if blockers else "clear"
    result["blocking_human_decisions"] = [
        {"event_id": gate["event_id"], "blocking_scope": gate["blocking_scope"],
         "checkpoint_id": gate["checkpoint_id"]} for gate in blockers
    ]
    from zf.runtime.candidate_reader_method import PROOF, reader_method_change

    change = reader_method_change(state_dir, dispatch, operation, failure_event_id=failure.id)
    if change:
        result["method_change"] = change
        if change["status"] != "improved" or blockers:
            return result
        payload[PROOF] = change[PROOF]
    if not error:
        result["proposal_only"] = True
        result["recommendation"] = {"recommended_route": "controlled_action", "action": "child-rebuild",
                                    "action_payload": payload}
    return result
