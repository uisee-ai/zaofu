from zf.core.events import EventLog, ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.goal_claim_set import build_goal_claim_set
from zf.runtime.plan_claim_continuity import candidate_claim_errors


def test_precritic_keeps_implicit_claims_and_checks_current_goal_waivers(tmp_path):
    original = {"tasks": [{"task_id": "render", "acceptance_criteria": [
        {"id": "A-SUCCESS", "statement": "The approved v1 command succeeds."},
    ]}]}
    old = build_goal_claim_set(original, workflow_run_id="run-1", goal_id="goal-1", task_map_generation="v1")
    port = write_immutable_json_sidecar(tmp_path, old, root="claims", kind="goal_claim_set",
                                        schema_version="goal-claim-set.v1", created_by="test")
    package = {"workflow_run_id": "run-1", "produced": [], "inherited": [{**port, "logical_name": "goal_claim_set"}]}
    changed = {"tasks": [{"task_id": "render", "acceptance_criteria": [
        {"id": "A-SUCCESS", "statement": "The new v2 command succeeds."},
    ]}]}
    trigger = {"goal_id": "goal-1", "goal_claim_waivers": [{"goal_claim_id": "A-SUCCESS", "waiver_ref": "allow-v2"}]}

    def findings(candidate):
        return candidate_claim_errors(tmp_path, tmp_path, package, candidate, trigger)

    def event(kind, goal):
        log = EventLog(tmp_path / "events.jsonl")
        try:
            log.append(ZfEvent(type=kind, origin="operator", actor="operator",
                               payload={"task_ids": [goal], "signature": "allow-v2"}))
        finally:
            log.close()

    assert findings(original) == []
    expected = [{"code": "mandatory_claim_rewritten", "field": "task_map.goal_claims[A-SUCCESS]",
                 "message": "Preserve the bound mandatory claim: The approved v1 command succeeds."}]
    assert findings(changed) == expected
    assert findings({**changed, "goal_claims": old["claims"]}) == []
    event("verification.waived", "other-goal")
    assert findings(changed) == expected
    event("verification.waived", "goal-1")
    assert findings(changed) == []
    event("verification.waiver.revoked", "goal-1")
    assert findings(changed) == expected
