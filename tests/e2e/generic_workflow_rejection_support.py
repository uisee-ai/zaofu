"""A valid negative evaluation is admitted evidence, never product completion."""
from zf.core.events import ZfEvent
from zf.runtime.run_manager import run_goal_completion_claim_event
from tests.e2e.generic_workflow_complex_support import REQUEST_ID, _identity


def record_rejected_delivery(writer, invoke, result, admission, *, semantic_replans, protocol_repairs):
    event = writer.append(ZfEvent(type="verify.completed", actor="verifier", correlation_id=REQUEST_ID,
        payload={**_identity(invoke), "stage_id": "verify", "role_instance": "verifier",
                 "verdict": result["verdict"], "artifact_delivery_result": result,
                 "open_gap_refs": result["open_gap_refs"],
                 "admitted_call_result_ref": admission.envelope_ref}))
    events = writer.event_log.read_all()
    assert run_goal_completion_claim_event(events, cause=event) is None
    completed = sum(event.type == "run.goal.completed" for event in events)
    assert completed == 0
    writer.emit("simulation.done", actor="test", correlation_id=REQUEST_ID,
                payload={"scenario": "generic-provider-rejection", "product_completed": False})
    return {"provider_verdict": result["verdict"], "result_admitted": admission.admitted,
            "goal_completed_count": completed, "rejection_event_id": event.id,
            "semantic_replan_count": len(semantic_replans), "protocol_repair_count": len(protocol_repairs)}
