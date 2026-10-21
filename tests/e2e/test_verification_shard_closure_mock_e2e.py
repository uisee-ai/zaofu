"""Eight evidence shards through real Thin Judge result admission."""
import pytest

from tests.test_call_result_admission import (
    _runtime, _append_admitted_upstream, _goal_closure_payload,
)
from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar

pytestmark = pytest.mark.mock_e2e


@pytest.mark.parametrize("fault", ["none", "missing_shard", "stale_target", "stale_generation"])
def test_judge_requires_complete_current_evidence_set(tmp_path, fault):
    admission, operations = _runtime(tmp_path)
    claim_descriptor = write_immutable_json_sidecar(
        tmp_path, {
            "schema_version": "goal-claim-set.v1", "workflow_run_id": "run-1",
            "goal_id": "GOAL-1", "task_map_generation": "generation-1",
            "claim_set_digest": "content-digest",
            "claims": [{"goal_claim_id": f"AC-{i}", "mandatory": True,
                        "text": f"Deliver behavior {i}"} for i in range(8)],
        }, root="goal-closure/claim-sets", kind="goal_claim_set",
        schema_version="goal-claim-set.v1", created_by="test",
    )
    refs = [_append_admitted_upstream(
        tmp_path, operations, task_id=f"SHARD-{i}",
        target_commit="b" * 40 if i == 7 and fault == "stale_target" else "c" * 40,
        generation="generation-0" if i == 7 and fault == "stale_generation" else "generation-1",
    ) for i in range(8)]
    assert len(set(refs)) == 8
    payload = _goal_closure_payload(claim_descriptor=claim_descriptor)
    result = payload["goal_closure_result"]
    result["input_result_refs"] = refs
    result["goal_coverage"] = [{
        "goal_claim_id": f"AC-{i}", "status": "closed", "supporting_result_refs": [refs[i]],
    } for i in range(7 if fault == "missing_shard" else 8)]
    operations.event_writer.append(ZfEvent(
        type="flow.goal.closed", correlation_id="run-1", payload={
            "workflow_run_id": "run-1", "goal_id": "GOAL-1",
            "task_map_generation": "generation-1", "candidate_head_commit": "c" * 40,
            "goal_claim_set_ref": claim_descriptor["ref"],
            "goal_claim_set_digest": claim_descriptor["sha256"],
            "closure_fact_ref": result["closure_fact_ref"],
            "closure_fact_digest": result["closure_fact_digest"],
        },
    ))
    ensured = operations.ensure_operation(
        workflow_run_id="run-1", operation_id="judge-8", operation_type="agent",
        request={"goal": "GOAL-1", "generation": "generation-1"},
    )
    outcome = admission.report_legacy_result(
        ZfEvent(type="judge.child.completed", actor="judge-prd", correlation_id="run-1", payload=payload),
        mode="blocking", operation={"workflow_run_id": "run-1", "operation_id": "judge-8",
                                    "request_hash": ensured.request_hash},
    )
    assert outcome.admitted is (fault == "none"), outcome.issues
    if fault != "none":
        assert outcome.repair_requested
        assert not any(e.type == "run.goal.completed" for e in operations.event_log.read_all())
