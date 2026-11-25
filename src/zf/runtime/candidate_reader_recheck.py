"""Reopen one read-only resident diagnosis when its frozen Reader gains a new method."""
from pathlib import Path

from zf.core.events import ZfEvent
from zf.runtime.candidate_reader_method import PROOF, SNAPSHOT
from zf.runtime.diagnosis_reader_rebuild import diagnosis_reader_rebuild
from zf.runtime.recovery_step import diagnosis_reply_matches_request
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _ineffective(receipt: ZfEvent) -> bool:
    p = receipt.payload
    return (receipt.origin == "kernel" and receipt.type == "run.manager.agent.recommendation.consumed"
            and (p.get("status") in {"wait", "blocked", "deferred"}
                 or p.get("reason") in {"controlled_action_status=failed", "controlled_action_status=blocked"}))


def pending_reader_method_rechecks(state: Path, events: list[ZfEvent], run: str) -> list[dict]:
    # Old published runs and ordinary role failures do not need sidecar reconciliation.
    last_dispatch = {}
    dispatches = set()
    for event in events:
        body = event.payload
        if event.origin != "kernel" or (run and body.get("workflow_run_id") != run):
            continue
        child = body.get("fanout_id"), body.get("child_id")
        if event.type == "fanout.child.dispatched":
            dispatches.discard(last_dispatch.pop(child, None))
            if body.get(SNAPSHOT):
                last_dispatch[child] = event.id
        elif event.type == "fanout.child.completed":
            dispatches.discard(last_dispatch.pop(child, None))
        elif event.type == "fanout.child.failed" and child in last_dispatch:
            dispatches.add(last_dispatch[child])
    if not dispatches:
        return []
    by_id = {e.id: e for e in events}
    prompts = {e.payload["request_id"]: e for e in events if e.origin == "kernel"
               and e.type == "run.manager.resident.prompted" and e.payload.get("request_id")}
    effects = {e.payload["request_id"]: e for e in events if e.origin == "kernel"
               and e.type == "run.manager.action.effect.pending" and e.payload.get("request_id")
               and e.payload.get("request_kind") == "recovery_diagnosis"}
    checkpoints = {e.payload.get("source_action_checkpoint_id") for e in prompts.values()}
    actions = []
    visited = set()
    for receipt in reversed(events):
        if not _ineffective(receipt):
            continue
        reply = by_id.get(receipt.payload.get("source_event_id"))
        if not reply or receipt.causation_id != reply.id:
            continue
        request = effects.get(reply.payload.get("request_id"))
        prompt = prompts.get(reply.payload.get("request_id"))
        if not request or not prompt or not diagnosis_reply_matches_request(
                reply, {**request.payload, "instance_id": prompt.payload.get("instance_id")}):
            continue
        p = request.payload
        if not p.get("recovery_case_id") or (run and run != p.get("workflow_run_id")):
            continue
        context_ref = p.get("recovery_context_ref") or {}
        delivered = prompt.payload.get("diagnosis_context") or {}
        if (not context_ref.get("sha256") or delivered.get("recovery_context_ref") != context_ref
                or delivered.get("source_event_ids") != p.get("source_event_ids")):
            continue
        try:
            context = hydrate_sidecar_ref(state, context_ref).payload
        except (OSError, ValueError):
            continue
        if not isinstance(context, dict) or context.get("request_id") != p.get("request_id"):
            continue
        old = context.get("reader_rebuild") or {}
        if not isinstance(old, dict):
            continue
        dispatch = old.get("dispatch_event_id")
        if dispatch not in dispatches or dispatch in visited:
            continue
        visited.add(dispatch)
        card = diagnosis_reader_rebuild(state, events, p)
        change = card.get("method_change") or {}
        previous = old.get("method_change") or {}
        if (not card.get("recommendation") or change.get("status") != "improved" or not previous
                or change.get(PROOF) == previous.get(PROOF) or card.get("dispatch_event_id") != dispatch
                or card.get("failure_event_id") != old.get("failure_event_id")):
            continue
        checkpoint = "reader-method-diagnosis:" + change[PROOF]
        if checkpoint in checkpoints:
            continue
        binding = change["binding"]
        actions.append({"action": "diagnose-attention", "safe_resume_action": "diagnose_attention",
            "schema_version": "run-manager.pending-action.v1", "checkpoint_id": checkpoint,
            "recovery_case_id": p["recovery_case_id"], "workflow_run_id": binding["workflow_run_id"],
            "task_id": binding["task_id"], "target_operation_id": binding["operation_id"],
            "active_attempt_id": binding["attempt_id"], "target_commit": binding["target_commit"],
            "source_event_ids": p["source_event_ids"], "reader_method_predecessor": request.id,
            "source_event_id": card["failure_event_id"], "recorded_event_id": reply.id,
            "owner_route": "run_manager", "suggested_route": "run_manager_recovery",
            "action_policy": "needs_diagnosis", "intervention_class": "diagnose",
            "expected_output": ["run.manager.agent.recommendation"],
            "expected_downstream_events": ["run.manager.resident.prompted"],
            "summary": "已签发 Reader 的可访问证据路径已变化，重新评估之前未生效的建议。"})
    return actions


def bound_reader_method_followup(events, action, case) -> bool:
    """Only a read-only successor to the case's most recent answered diagnosis."""
    pred = action.get("reader_method_predecessor")
    if (not pred or pred != case.get("pending_event_id") or case.get("status") not in {"diagnosing", "verifying"}
            or action.get("action") not in {"diagnose-attention", "resident-agent-reprompt"}):
        return False
    request = next((e for e in events if e.id == pred and e.origin == "kernel"
                    and e.type == "run.manager.action.effect.pending"), None)
    if (not request or request.payload.get("request_kind") != "recovery_diagnosis"
            or request.payload.get("workflow_run_id") != action.get("workflow_run_id")
            or request.payload.get("source_event_ids") != action.get("source_event_ids")):
        return False
    request_id = request.payload.get("request_id")
    delivery = next((e for e in events if e.origin == "kernel" and e.type == "run.manager.resident.prompted"
                     and e.payload.get("request_id") == request_id), None)
    reply = next((e for e in events if e.id == action.get("recorded_event_id")), None)
    return bool(delivery and reply and diagnosis_reply_matches_request(
        reply, {**request.payload, "instance_id": delivery.payload.get("instance_id")})
        and any(_ineffective(e) and e.causation_id == reply.id == e.payload.get("source_event_id") for e in events))
