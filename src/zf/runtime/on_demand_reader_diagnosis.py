"""Return an on-demand diagnostic opinion to the existing controlled Reader repair route."""
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.diagnosis_reader_rebuild import diagnosis_reader_rebuild
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import operation_request_hash, reduce_workflow_operations


def reader_option(state_dir: Path, events: list[ZfEvent], action: dict[str, Any], *, config=None) -> dict[str, Any]:
    card = diagnosis_reader_rebuild(state_dir, events, {**action, "request_kind": "recovery_diagnosis"}, config=config)
    if not card.get("recommendation") or card.get("human_gate_status") != "clear" or card.get("binding_preflight") != "passed":
        return {}
    body = {"recommendation": card["recommendation"], "failure_event_id": card["failure_event_id"],
            "dispatch_event_id": card["dispatch_event_id"]}
    return {**body, "option_id": operation_request_hash(body), "binding_preflight": card["binding_preflight"],
            "human_gate_status": card["human_gate_status"], "attempt_identity": card["attempt_identity"]}


def publish_selected_options(state_dir: Path, writer: EventWriter, events: list[ZfEvent], *, resident: bool = False) -> bool:
    """The tick lock serializes this publication; restart reuses the settled provider response."""
    offered = [e for e in events if e.type == "run.manager.reflect.completed"
               and e.actor == "run-manager" and e.payload.get("selected_reader_recovery")]
    if not offered:
        return False
    published = {e.payload.get("diagnosis_operation_id") for e in events
                 if e.origin == "kernel" and e.type == "run.manager.agent.recommendation"}
    options = reduce_workflow_operations(events)
    emitted = False
    for event in offered:
        if event.payload.get("operation_id") in published or event.origin not in {"kernel", ""}:
            continue
        p = event.payload
        op = options.get(p.get("operation_id")) or {}
        if (op.get("operation_type") != "run_manager_reflect" or op.get("status") != "settled"
                or op.get("admitted_call_result_ref") != p.get("result_ref")):
            continue
        try:
            result = hydrate_sidecar_ref(state_dir, op["admitted_call_result_ref"]).payload
            request = hydrate_sidecar_ref(state_dir, op["request_ref"]).payload["request"]
        except (KeyError, OSError, ValueError):
            continue
        meta = request["metadata"]
        if resident != bool(meta.get("resident_referral")):
            continue
        option = meta.get("reader_recovery_option")
        if (not option or request["request_id"] != p.get("request_id") or result.get("apply_policy") != "proposal_only"
                or result.get("selected_reader_recovery") != option["option_id"] or not result.get("recommendation")
                or result.get("verdict") not in {"best_so_far", "better_fix_exists"}
                or result.get("risk") not in {"low", "medium"}):
            continue
        writer.append(ZfEvent(type="run.manager.agent.recommendation", origin="kernel", actor="run-manager",
            causation_id=event.id, correlation_id=op["workflow_run_id"], task_id=op.get("task_id") or None,
            payload={**option["recommendation"], "summary": result["recommendation"],
                "evidence_event_ids": [option["failure_event_id"], option["dispatch_event_id"]],
                "diagnosis_operation_id": op["operation_id"], "diagnosis_result_ref": op["admitted_call_result_ref"],
                **({"resident_referral": meta["resident_referral"]} if resident else {}),
                "recovery_case_id": meta["recovery_case_id"], "workflow_run_id": op["workflow_run_id"]}))
        published.add(op["operation_id"])
        emitted = True
    return emitted
