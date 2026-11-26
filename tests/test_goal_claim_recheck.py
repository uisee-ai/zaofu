from copy import deepcopy

import pytest

from tests.test_goal_candidate_batch import GEN, GOAL_ID, REF, RUN_ID, TARGET, _retried_judge, event
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.run_manager import run_goal_completion_claim_event, run_goal_completion_gate_event


@pytest.fixture
def case(tmp_path):
    rows, _ = _retried_judge()
    by = {e.id: e for e in rows}
    def store(body):
        return write_immutable_json_sidecar(tmp_path, body, root="test", kind="fixture",
                                             schema_version=body["schema_version"], created_by="test")
    def envelope(control, source, identity):
        return store({"schema_version":"call-result-envelope.v1", "source_event_ids":[source.id],
                      "execution":{"status":"completed"}, "control_result":control, "identity":{
                          "request_hash":"f"*64,"operation_id":source.payload.get("operation_id","judge-op"),
                          "attempt_id":"attempt-"+source.id,"producer_role":"verifier",**identity}})
    identity = {"workflow_run_id":RUN_ID, "target_commit":TARGET, "task_map_generation":GEN}
    freeze = {"schema_version":"candidate-freeze-receipt.v1", "freeze_id":"frozen",
              "workflow_run_id":RUN_ID, "task_map_generation":GEN, "candidate_head_commit":TARGET}
    desc = store(freeze)
    by["current"].payload.update(freeze, freeze_receipt_ref=desc, freeze_receipt_digest=desc["sha256"])
    for n in range(8):
        admission, source, child = (by[f"{k}-current-{n}"] for k in ("admission", "source", "child"))
        report = store({**identity, "schema_version":"verification-result.v1", "verification_owner":"candidate_verify",
                        "verdict":"passed", "role":f"reader-{n}"})
        signed = envelope(report, source, identity)
        for e in (admission,source,child):
            e.payload["control_result_ref"] = report
        admission.payload["envelope_ref"] = child.payload["admitted_call_result_ref"] = signed
    for c in ("close0", "close1", "close2"):
        by[c].payload.update(goal_claim_set_digest="claims", goal_claim_set_ref="claims.json")
    judge = rows.pop()
    rows.insert(0,event("run.goal.started","root",run_id=RUN_ID,goal_id=GOAL_ID,objective="deliver"))
    body = {**identity,"schema_version":"goal-closure-result.v1","goal_id":GOAL_ID,"flow_kind":"prd","candidate_ref":REF,
            "verdict":"passed","recommended_action":"complete","goal_claim_set_ref":"claims.json","goal_claim_set_digest":"claims",
            "closure_fact_ref":"fact.json","closure_fact_digest":"facts","operation_id":"judge-op"}
    typed = store(body)
    source = event("judge.child.completed","judge-published",semantic_submit_admission_event_id="judge-admitted")
    signed = envelope(typed,source,{**identity,"operation_id":"judge-op"})
    source.payload.update(control_result_ref=typed,call_result_envelope_ref=signed)
    admission = event("workflow.call.result.admitted","judge-admitted",envelope_ref=signed,control_result_ref=typed,
                      source_event_id=source.id,control_result_schema=body["schema_version"],admission_status="admitted",
                      semantic_verdict="passed",operation_id="judge-op")
    judge.payload.update(goal_closure_result=body, goal_id=GOAL_ID, status="completed", rework_of="failedjudge-1",
                         rework_attempt=2, control_result_ref=typed, admitted_call_result_ref=signed, operation_id="judge-op")
    rows.extend([admission, source, event("fanout.aggregate.completed", "last-aggregate",
                                         fanout_id="admitted-final", status="completed"), judge])
    claim = run_goal_completion_claim_event(rows,cause=judge)
    claim.origin = "kernel"
    rows.append(claim)
    outcome = run_goal_completion_gate_event(rows,claim=claim,delivery_policy="ship_candidate",state_dir=tmp_path)
    assert outcome and outcome.type == "run.delivery.requested"
    rejected = event("run.goal.completion.rejected","old-false-negative",cause=claim.id,
        **{k:claim.payload[k] for k in ("run_id","workflow_run_id","claim_id","goal_id","source_event_id","target_commit",
                  "candidate_ref","goal_claim_set_digest","task_map_generation","admitted_call_result_ref")},
        claim_event_id=claim.id,candidate_event_id="current",invalid_reasons=["verification_evidence_missing"],
        completion_recovery_attempt=1,completion_recovery_cap=2)
    rows.append(rejected)
    return tmp_path, rows, claim, rejected


def gate(case, **kw):
    path, rows, claim, _ = case
    return run_goal_completion_gate_event(rows,claim=claim,delivery_policy="ship_candidate",state_dir=path,**kw)


def test_original_ledger_can_recheck_and_deliver_once(case):
    path, rows, claim, rejected = case
    result = gate(case)
    assert result is not None and result.type == "run.delivery.requested"
    assert result.payload["rechecked_rejection_event_id"] == rejected.id
    assert result.payload["verification_batch_event_id"] == "pub-current"
    assert result.causation_id == claim.id
    rows.append(result)
    rows.append(event("run.delivery.settled", "delivered", cause=result.id,
                      claim_id=claim.payload["claim_id"],delivery_operation_id=result.payload["delivery_operation_id"]))
    completed = gate(case)
    assert completed.type == "run.goal.completed" and completed.payload["rechecked_rejection_event_id"] == rejected.id
    rows.append(completed)
    assert gate(case) is None


@pytest.mark.parametrize("fault", ["other_reason","extra_reason","second_rejection","wrong_run","wrong_cause",
    "wrong_target","wrong_generation","wrong_rejection_claim","wrong_judge_wrc","non_retry","fake_retry","other_freeze",
    "new_judge","new_plan","superseded","real_human_gate","required_op_active","missing_reader","tampered_reader",
    "tampered_judge","tampered_freeze","late_reader_admission","duplicate_rejection","terminal"])
def test_inapplicable_rejections_and_current_blockers_stay_closed(case, fault):
    path, rows, claim, rejected = case
    by = {e.id:e for e in rows}
    if fault in {"other_reason","extra_reason"}:
        rejected.payload["invalid_reasons"] = ["verification_target_mismatch"] if fault=="other_reason" else [
            "verification_evidence_missing","stale_task_authority"]
    elif fault=="second_rejection":
        rejected.payload["completion_recovery_attempt"]=2
    elif fault=="wrong_run":
        rejected.payload["workflow_run_id"]="another"
    elif fault=="wrong_cause":
        rejected.causation_id="someone-else"
    elif fault=="wrong_target":
        rejected.payload["target_commit"]="another"
    elif fault=="wrong_generation":
        rejected.payload["task_map_generation"]="another"
    elif fault=="wrong_rejection_claim":
        rejected.payload["claim_event_id"]="another"
    elif fault=="wrong_judge_wrc":
        by["judge-published"].payload["semantic_submit_admission_event_id"]="unrelated"
    elif fault=="non_retry":
        by["passed"].payload.pop("rework_of")
    elif fault=="fake_retry":
        by["passed"].payload["rework_of"]="failedjudge-0"
    elif fault=="other_freeze":
        rejected.payload["candidate_event_id"]="other"
    elif fault in {"new_judge","new_plan","superseded","late_reader_admission"}:
        name={"new_judge":"goal.closure.synthesized","new_plan":"plan.approved",
              "superseded":"goal.closure.superseded","late_reader_admission":"workflow.call.result.admitted"}[fault]
        rows.append(event(name,"newdecision",control_result_schema="verification-result.v1"))
    elif fault=="real_human_gate":
        rows.append(event("human.escalate","owner",blocking_scope="run",question="Human decision",decision_token="token"))
    elif fault=="required_op_active":
        assert gate(case,required_operation_ids=["unsettled"]) is None
        return
    elif fault=="missing_reader":
        rows.remove(by["child-current-7"])
    elif fault in {"tampered_reader","tampered_judge","tampered_freeze"}:
        source={"tampered_reader":by["source-current-7"].payload["control_result_ref"],
                "tampered_judge":by["judge-admitted"].payload["envelope_ref"],
                "tampered_freeze":by["current"].payload["freeze_receipt_ref"]}[fault]
        (path/source["ref"]).write_bytes(b"{}")
    elif fault=="duplicate_rejection":
        dup=deepcopy(rejected);dup.id="another-rejection";rows.append(dup)
    elif fault=="terminal":
        rows.append(event("run.goal.completed","completed",claim_id=claim.payload["claim_id"]))
    assert gate(case) is None
