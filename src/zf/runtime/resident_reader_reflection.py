"""One read-only amendment when a delegated reflection lacked a now-provable Reader option."""
from pathlib import Path

from zf.core.config.schema import ZfConfig
from zf.core.events import ZfEvent
from zf.runtime.autoresearch_failure_handoff import resident_reflection_followup
from zf.runtime.on_demand_reader_diagnosis import reader_option
from zf.runtime.recovery_step import bind_recommendation_to_request, recommendation_target_current
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import operation_request_hash, reduce_workflow_operations


def missed_reader_reflection(state: Path, events: list[ZfEvent], config: ZfConfig) -> tuple[dict, dict] | None:
    from zf.runtime.run_manager import (
        _action_from_agent_recommendation, _operation_precondition_holds, _pending_human_decisions, action_gate_reason,
    )
    if not (config.runtime.run_manager.reflect.enabled and config.runtime.run_manager.resident_agent.enabled):
        return None
    candidates = [e for e in events if e.origin == "kernel" and e.actor == "run-manager"
                  and e.type == "run.manager.reflect.completed" and e.payload.get("verdict") == "unknown"
                  and e.payload.get("recommended_route") == "autoresearch" and e.payload.get("workflow_run_id")]
    if not candidates:
        return None
    active = build_run_admission_projection(events).active_run_ids
    candidates = [e for e in candidates if e.payload["workflow_run_id"] in active]
    if not candidates:
        return None
    operations = reduce_workflow_operations(events)
    by_id = {e.id: e for e in events}
    for completed in candidates:
        p = completed.payload
        op = operations.get(p.get("operation_id"), {})
        result_ref = p.get("result_ref")
        if not isinstance(result_ref, dict) or not result_ref.get("sha256") or not p.get("operation_id"):
            continue
        amendment_id = "rmrf-" + operation_request_hash({
            "omitted_reader_option": p["operation_id"], "original_result_sha256": result_ref["sha256"],
        })[:20]
        if (not op or "wop-" + amendment_id in operations or op.get("operation_type") != "run_manager_reflect"
                or op.get("status") != "settled" or op.get("admitted_call_result_ref") != p.get("result_ref")):
            continue
        sources = p.get("source_event_ids")
        reply = by_id.get(sources[0]) if isinstance(sources, list) and len(sources) == 1 and isinstance(sources[0], str) else None
        if (not reply or reply.type != "run.manager.agent.recommendation" or reply.payload.get("recommended_route") != "reflect"
                or not reply.payload.get("request_id") or p.get("checkpoint_id") != "recommendation:" + reply.id
                or events.index(reply) >= events.index(completed)):
            continue
        try:
            request = hydrate_sidecar_ref(state, op["request_ref"]).payload["request"]
            result = hydrate_sidecar_ref(state, op["admitted_call_result_ref"]).payload
            meta = request["metadata"]
            if (request["request_id"] != p.get("request_id") or meta.get("reader_recovery_option")
                    or meta.get("reader_option_amendment")
                    or completed.correlation_id != request["request_id"] or op.get("operation_id") != "wop-" + request["request_id"]
                    or op.get("workflow_run_id") != p["workflow_run_id"]
                    or result.get("verdict") != "unknown" or result.get("recommended_route") != "autoresearch"
                    or result.get("apply_policy") != "proposal_only" or not result.get("recommendation")
                    or any(meta.get(k) != p.get(k) for k in ("workflow_run_id", "checkpoint_id", "source_event_ids", "recovery_case_id"))):
                continue
            action = _action_from_agent_recommendation(reply, bind_recommendation_to_request(events, reply, reply.payload))
            if (action.get("workflow_run_id") != p["workflow_run_id"] or action.get("recovery_case_id") != p["recovery_case_id"]
                    or not _operation_precondition_holds(events, action) or not recommendation_target_current(events, action)
                    or action_gate_reason(state, events, action, _pending_human_decisions(events))):
                continue
            followup = resident_reflection_followup(events, action)
            option = reader_option(state, events, followup, config=config) if followup else {}
        except (ValueError, OSError, KeyError, TypeError):
            continue
        if option:
            return action, {"request_id": amendment_id, "predecessor_event_id": completed.id,
                            "predecessor_operation_id": op["operation_id"], "predecessor_result_ref": p["result_ref"],
                            "option_id": option["option_id"]}
    return None
