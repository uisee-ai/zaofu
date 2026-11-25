"""Bind duplicate Candidate diagnoses to the controlled repair of their one failed Reader."""
from pathlib import Path

from zf.core.events import ZfEvent
from zf.runtime.candidate_reader_method import PROOF
from zf.runtime.candidate_reader_recovery_owner import candidate_reader_outcomes
from zf.runtime.diagnosis_reader_rebuild import _diagnosed_child, diagnosis_reader_rebuild
from zf.runtime.reader_rebuild_admission import reader_rebuild_dispatch_matches, reader_rebuild_effect_matches
from zf.runtime.sidecar_refs import hydrate_sidecar_ref

DISPOSITION = "superseded_by_admitted_reader_recovery"


def _terminals(events, end, run, fanout):
    rows = {}
    for event in events:
        if event.id == end.id:
            break
        p = event.payload
        if event.origin != "kernel" or p.get("fanout_id") != fanout or p.get("workflow_run_id") != run:
            continue
        if event.type == "fanout.child.dispatched":
            rows.pop(p.get("child_id"), None)
        elif event.type in {"fanout.child.failed", "fanout.child.completed"}:
            rows[p["child_id"]] = event.id
    return rows


def covering_reader_recovery(state: Path, events: list[ZfEvent], action: dict) -> dict:
    """Return the in-flight owner, and an observed completion only after the whole batch passes."""
    if action.get("action") not in {"diagnose-attention", "resident-agent-reprompt"}:
        return {}
    sources = action.get("source_event_ids") or []
    run = action.get("workflow_run_id")
    if not run or len(sources) != 1:
        return {}
    intents = [e for e in events if e.type == "repair.action.requested" and e.origin == "kernel"
               and e.actor == "run-manager" and e.payload.get("recovery_action") == "child-rebuild"
               and e.payload.get(PROOF) and e.payload.get("workflow_run_id") == run]
    if not intents:
        return {}
    by_id = {e.id: e for e in events}
    source = by_id.get(sources[0])
    if not source or not (source.type == "task.attempt.failed" or source.payload.get("failed_children")):
        return {}
    failure = _diagnosed_child(events, set(sources), run)
    if not failure:
        return {}
    p = failure.payload
    child, fanout = p["child_id"], p["fanout_id"]
    intents = [e for e in intents if e.payload.get("source_event_id") == failure.id
               and e.payload.get("target_operation_id") == p.get("operation_id")
               and e.payload.get("active_attempt_id") == p.get("attempt_id")
               and e.payload.get("fanout_child_id") == child and e.payload.get("fanout_id") == fanout]
    if len(intents) != 1:
        return {}
    intent = intents[0]
    aggregates = [e for e in events if e.origin == "kernel" and e.payload.get("workflow_run_id") == run
                  and e.payload.get("fanout_id") == fanout and e.payload.get("failed_children") == [child]
                  and e.payload.get("failure_kind") == "infra" and e.type.endswith(".failed")
                  and e.payload.get("status") == "failed" and (e == source or source.type == "task.attempt.failed")]
    for aggregate in aggregates:
        terminals = _terminals(events, aggregate, run, fanout)
        if terminals.get(child) == failure.id:
            break
    else:
        return {}
    outcomes = candidate_reader_outcomes(events, terminals, fanout, run)
    if (len(terminals) != aggregate.payload.get("child_count")
            or outcomes.get("abstained") != {child: failure.id} or set(outcomes) - {"passed", "abstained"}
            or not isinstance(aggregate.payload.get("findings"), list)
            or any(not isinstance(f, dict) or f.get("child_id") != child or f.get("category") != "runtime_failure"
                   for f in aggregate.payload["findings"])):
        return {}
    frozen = ("target_commit", "contract_snapshot_digest", "target_snapshot_digest")
    if any(not p.get(k) or by_id[t].payload.get(k) != p[k] for t in terminals.values() for k in frozen):
        return {}
    if action.get("recovery_context_ref"):
        if not isinstance(action["recovery_context_ref"], dict) or not action["recovery_context_ref"].get("sha256"):
            return {}
        try:
            context = hydrate_sidecar_ref(state, action["recovery_context_ref"]).payload
            if context.get("request_id") != action.get("request_id"):
                return {}
            card = context.get("reader_rebuild") or {}
        except (OSError, ValueError, AttributeError):
            return {}
    else:
        card = diagnosis_reader_rebuild(state, events, {**action, "request_kind": "recovery_diagnosis"})
    if not isinstance(card, dict):
        return {}
    delta = card.get("method_change") or {}
    if (card.get("failure_event_id") != failure.id or delta.get("status") != "improved"
            or delta.get(PROOF) != intent.payload[PROOF]):
        return {}
    # The intent by itself is not the Run Manager's verified application receipt.
    receipt = next((e for e in events if e.origin == "kernel" and e.type == "run.manager.action.applied"
                    and e.payload.get("action") == "child-rebuild" and e.payload.get("event_id") == intent.id
                    and e.payload.get("source_event_id") == failure.id), None)
    if not receipt or (action.get("request_id") and receipt.payload.get("request_id") == action["request_id"]):
        return {}
    if not any(e.type == "run.manager.action.effect.pending" and e.origin == "kernel"
               and e.payload.get("action") == "child-rebuild" and e.payload.get("source_event_id") == failure.id
               and (verify := by_id.get(e.causation_id)) and verify.origin == "kernel"
               and verify.type == "run.manager.action.verify.passed" and verify.causation_id == receipt.id for e in events):
        return {}
    if any(e.type == "repair.action.rejected" and e.origin == "kernel" and e.causation_id == intent.id for e in events):
        return {}
    result = {"superseding_repair_event_id": intent.id, "reader_failure_event_id": failure.id,
              "superseding_recovery_case_id": receipt.payload.get("recovery_case_id")}
    after = events[events.index(intent) + 1:]
    completions = [e for e in after if e.type in {"fanout.child.completed", "fanout.child.failed"}
                   and e.payload.get("fanout_id") == fanout and reader_rebuild_effect_matches(e, intent.payload, events)]
    if completions:
        completed = completions[-1]
        if (candidate_reader_outcomes(events, {child: completed.id}, fanout, run) != {"passed": {child: completed.id}}
                or any(completed.payload.get(k) != p[k] for k in frozen)):
            return {}
        for final in after:
            if (final.origin == "kernel" and final.type == "fanout.aggregate.completed"
                    and final.payload.get("status") == "completed" and final.payload.get("fanout_id") == fanout
                    and final.payload.get("workflow_run_id") == run
                    and _terminals(events, final, run, fanout) == {**terminals, child: completed.id}):
                return {**result, "replacement_completion_event_id": completed.id, "observed": final}
    latest = next((e for e in reversed(after) if e.origin == "kernel" and e.type == "fanout.child.dispatched"
                   and e.payload.get("fanout_id") == fanout and e.payload.get("child_id") == child), None)
    if latest and not reader_rebuild_dispatch_matches(latest, intent.payload):
        return {}
    if any(e.type == "run.manager.action.effect.failed" and e.origin == "kernel"
           and e.payload.get("action") == "child-rebuild" and e.payload.get("source_event_id") == failure.id
           for e in after):
        return {}
    return result


def duplicate_diagnosis_closed(event: ZfEvent, request_id: str) -> bool:
    p = event.payload
    return bool(request_id and event.origin == "kernel" and event.actor == "run-manager"
        and event.type == "run.manager.action.effect.passed" and p.get("status") == "passed"
        and p.get("request_id") == request_id and p.get("disposition") == DISPOSITION
        and p.get("action") == "resident-agent-reprompt" and p.get("request_kind") == "recovery_diagnosis"
        and p.get("observed_event_id") == event.causation_id and p.get("observed_event_type") == "fanout.aggregate.completed"
        and p.get("replacement_completion_event_id") and p.get("superseding_repair_event_id")
        and p.get("effect_pending_event_id"))
