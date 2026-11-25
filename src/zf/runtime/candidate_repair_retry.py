"""Operator redrive of a previously miscounted, still-authorized Candidate repair."""
from __future__ import annotations

from contextlib import nullcontext
from hashlib import sha256

from zf.core.events.model import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.call_result_adapters import hydrate_profiled_control_result_event
from zf.runtime.candidate_failure_checkpoint import aggregate_candidate_binding
from zf.runtime.candidate_plan_recovery import candidate_checkpoint_binding
from zf.runtime.candidate_repair_outcome import published_candidate_repair_outcome
from zf.runtime.candidate_task_repair_decision import candidate_task_repair_action
from zf.runtime.orchestrator_agent_admission import admit_orchestrator_agent_decision
from zf.runtime.orchestrator_agent_decision_apply import (
    _apply_semantic_failure,
    _directive_ref_rejection,
    _operation_checkpoint_context,
    _operation_source_descriptors,
    _target_rejection,
)
from zf.runtime.pause_lifecycle import is_run_dispatch_paused


def retry_rejected_candidate_repair(runtime, decision_id: str, *, approval_ref: str = "", apply: bool = False) -> dict:
    if apply and not approval_ref.strip():
        raise ValueError("applying Candidate recovery requires an operator approval reference")
    key = sha256(decision_id.encode()).hexdigest()
    with (locked_path(runtime.state_dir / "locks" / f"candidate-repair-redrive-{key}") if apply else nullcontext()):
        return _retry(runtime, decision_id, approval_ref, apply)


def _retry(runtime, decision_id, approval_ref, apply):
    events = runtime.event_log.read_all()
    original = next((e for e in events if e.id == decision_id and
                     e.type == "orchestrator.semantic.decision.submitted"), None)
    if not original:
        raise ValueError("original submitted OA decision missing")
    event = hydrate_profiled_control_result_event(runtime.state_dir, original)
    history = [e for e in events if e.causation_id == decision_id and e.origin == "kernel"
               and e.actor == "zf-cli" and e.payload.get("decision_event_id") == decision_id]
    admitted = next((e for e in history if e.type == "orchestrator.semantic.decision.admitted"
                     and e.payload.get("checkpoint_policy") == "blocking"
                     and e.payload.get("decision") == "rework"
                     and e.payload.get("checkpoint") == "semantic_failure"), None)
    outcomes = [e for e in history if e.type == "orchestrator.semantic.decision.applied"]
    refused = next((e for e in history if e.type == "orchestrator.semantic.rework.rejected"
                    and e.payload.get("reason") == "candidate product rework cap exhausted"), None)
    if not admitted or not refused or not outcomes:
        raise ValueError("no admitted Candidate repair rejected by the product rework cap")
    latest = outcomes[-1].payload
    published = published_candidate_repair_outcome(runtime, event)
    if published and latest.get("applied") is True:
        return {"status": "already_applied", "event_ids": published["candidate_repair_event_ids"]}
    if latest.get("reason") != refused.payload["reason"] or latest.get("applied") is not False:
        raise ValueError("latest outcome is not the original cap rejection")
    if published:
        result = published
    else:
        run_id = admitted.payload["workflow_run_id"]
        if is_run_dispatch_paused(events, run_id):
            raise ValueError("Candidate recovery cannot resume a paused Run")
        if any(e.type == "orchestrator.semantic.decision.admitted" and e.origin == "kernel"
               and e.payload.get("source_event_id") == admitted.payload["source_event_id"]
               for e in events[events.index(refused) + 1:]):
            raise ValueError("this checkpoint has a newer admitted decision")
        admission = admit_orchestrator_agent_decision(runtime, event)
        if not admission.admitted:
            raise ValueError(admission.reason)
        decision = admission.decision
        expected = _operation_checkpoint_context(runtime, admitted.payload)
        directives = [d for d in (decision.get("delta") or {}).get("directives", []) if d.get("action") == "rework"]
        if len(directives) != 1 or not expected.get("candidate_operation_id"):
            raise ValueError("redrive requires one original Candidate repair directive")
        binding = dict(failure_id=expected["candidate_failure_id"], run_id=run_id,
                       task_id=expected["target_task_id"])
        candidate = candidate_checkpoint_binding(runtime.state_dir, events,
            operation_id=expected["candidate_operation_id"], **binding)
        if expected.get("candidate_failure_scope") == "aggregate":
            candidate = aggregate_candidate_binding(runtime.state_dir, events, **binding)
            if candidate["operation_id"] != expected["candidate_operation_id"]:
                raise ValueError("Candidate recovery operation changed")
        directive = directives[0]
        rejection = _target_rejection(runtime, directive["target"], expected, action="rework", candidate=candidate)
        rejection = rejection or _directive_ref_rejection(directive, _operation_source_descriptors(runtime, admitted.payload))
        if rejection:
            raise ValueError(rejection)
        payload = candidate_task_repair_action(runtime, decision=decision, directive=directive,
                                              expected=expected, candidate=candidate)
        if not apply:
            return {"status": "ready", "run": run_id, "target": payload["source_commit"],
                    "rework_attempt": payload["rework_attempt"], "checkpoint": payload["checkpoint_id"],
                    "tasks": [t["task_id"] for t in payload["rework_summary"]["gap_tasks"]]}
        result = _apply_semantic_failure(runtime, event, decision,
            {**admitted.payload, "status": "applied", "applied": True, "operator_recovery_approval_ref": approval_ref})
        if not result.get("applied") or not result.get("candidate_repair_event_ids"):
            raise ValueError(result.get("reason") or "original repair did not publish")
    if apply:
        runtime.event_writer.append(ZfEvent(type="orchestrator.semantic.decision.applied", actor="zf-cli",
            origin="kernel", causation_id=decision_id, correlation_id=event.correlation_id,
            payload={**result, "operator_recovery_approval_ref": approval_ref, "prior_rejection_event_id": refused.id}))
    return {"status": "applied" if apply else "already_published", "event_ids": result["candidate_repair_event_ids"]}
