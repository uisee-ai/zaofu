import json
from types import SimpleNamespace

import pytest

from tests.test_candidate_verification_authority import _runtime, _seed_frozen_candidate
from zf.core.events import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.candidate_verification_authority import prepare_candidate_verification_authority
from zf.runtime.goal_task_verify_evidence import judge_task_verify_evidence


@pytest.fixture
def evidence(tmp_path):
    runtime = _runtime(tmp_path)
    seeded = _seed_frozen_candidate(runtime)
    run, generation, target = (seeded[k] for k in ("run_id", "generation", "candidate_head"))
    events = runtime.event_log.read_all()
    pub = next(e for e in events if e.type == "task.pipeline.verify.completed")
    freeze = next(e for e in events if e.type == "candidate.ready")
    pub.actor = "independent-verifier"
    invocation = {"request_hash": "a"*64, "dispatch_id": "dispatch-v", "attempt_id": "attempt-v", "operation_id": "op-v"}
    pub.payload.update(invocation)
    def sidecar(body):
        return write_immutable_json_sidecar(runtime.state_dir, body, root="test/verify", kind="fixture",
                                             schema_version=body["schema_version"], created_by="test")
    control = sidecar({**pub.payload, "schema_version": "verification-result.v1", "role_instance": pub.actor,
                       "verification_owner": "task_verify", "verdict": "passed", "summary": "frozen check ran"})
    envelope = sidecar({"schema_version": "call-result-envelope.v1", "source_event_ids": [pub.id],
                        "identity": {**pub.payload, "producer_role": pub.actor}, "execution": {"status": "completed"},
                        "control_result": control})
    adm = ZfEvent(type="workflow.call.result.admitted", origin="kernel", task_id=pub.task_id, payload={
        **invocation, "admission_status": "admitted", "workflow_run_id": run, "semantic_verdict": "passed",
        "control_result_schema": "verification-result.v1", "source_event_id": pub.id,
        "control_result_ref": control, "envelope_ref": envelope})
    pub.payload.update(semantic_submit_admission_event_id=adm.id, control_result_ref=control, call_result_envelope_ref=envelope)
    events.insert(events.index(pub), adm)
    runtime.event_log = SimpleNamespace(read_all=lambda: events)
    candidate = prepare_candidate_verification_authority(runtime, payload={"candidate_event_id": freeze.id},
                            workflow_run_id=run, task_id="FLOW-ANCHOR")
    reader = ZfEvent(type="workflow.call.result.admitted", origin="kernel", payload={
        "workflow_run_id": run, "semantic_verdict": "passed", "control_result_schema": "verification-result.v1",
        "control_result_ref": sidecar({**candidate, "workflow_run_id": run,"task_id": "FLOW-ANCHOR",
                                      "verification_owner": "candidate_verify","schema_version":"verification-result.v1"})})
    reader.payload["envelope_ref"] = sidecar({"schema_version": "call-result-envelope.v1",
        "identity": {**candidate, "workflow_run_id": run, "operation_id": "candidate-op", "attempt_id": "candidate-attempt",
            "dispatch_id": "candidate-dispatch", "request_hash": "b"*64, "producer_role": "candidate-reader"},
        "execution": {"status":"completed"}, "control_result": reader.payload["control_result_ref"]})
    passed = ZfEvent(type="test.passed", origin="kernel", payload={
        "workflow_run_id": run, "candidate_snapshot_event_id": freeze.id, "target_commit": target})
    events.extend([reader, passed])
    batch = {"readers": [reader.id], "supporting": [], "discovery": [], "candidate_event_id": freeze.id, "publication": passed.id}
    def read():
        return judge_task_verify_evidence(runtime.state_dir, events, batch=batch, run=run, generation=generation, target=target)
    return SimpleNamespace(read=read, state=runtime.state_dir, control=control, pub=pub, adm=adm, freeze=freeze,
                           candidate=candidate, batch=batch, events=events, passed=passed, run=run,
                           generation=generation, target=target, reader=reader, runtime=runtime)


def test_admitted_task_result_is_scoped_to_the_candidate_publication(evidence):
    rows = evidence.read()
    assert len(rows) == 1
    row = rows[0]
    assert (row["source_id"], row["verification_event_id"], row["admitted_event_id"], row["attempt_id"]) == (
        "task-verify-TASK-1", evidence.pub.id, evidence.adm.id, "attempt-v")
    assert row["control_result"] == {k:evidence.control[k] for k in ("ref","sha256")}
    assert evidence.read() == rows


@pytest.mark.parametrize("fault", ["other_run", "other_task", "wrong_attempt", "wrong_freeze", "not_a_member",
    "detached_admission", "wrong_ref", "wrong_hash", "actual_bytes", "private", "late_publication"])
def test_invalid_or_other_task_sources_receive_no_added_access(evidence, fault):
    e = evidence
    if fault == "other_run":
        e.adm.payload["workflow_run_id"] = "elsewhere"
    elif fault == "other_task":
        e.pub.task_id = "T-other"
    elif fault == "wrong_attempt":
        e.adm.payload["attempt_id"] = "stale-attempt"
    elif fault == "wrong_freeze":
        e.batch["candidate_event_id"] = "other-candidate"
    elif fault == "not_a_member":
        e.freeze.payload["completed_task_ids"] = ["T-other"]
    elif fault == "detached_admission":
        e.pub.payload["semantic_submit_admission_event_id"] = "unknown"
    elif fault == "wrong_ref":
        e.adm.payload["control_result_ref"] = dict(e.control,ref="artifacts/different.json")
    elif fault == "wrong_hash":
        e.adm.payload["envelope_ref"] = dict(e.adm.payload["envelope_ref"],sha256="0"*64)
    elif fault == "actual_bytes":
        (e.state / e.control["ref"]).write_bytes(b"{}")
    elif fault == "private":
        e.adm.payload["control_result_ref"] = dict(e.control,access_scope={"actors":["other"]})
    elif fault == "late_publication":
        e.events.remove(e.pub)
        e.events.append(e.pub)
    assert e.read() == []


def test_frozen_judge_index_grants_one_optional_body_without_promoting_it_to_a_candidate_vote(evidence, monkeypatch):
    from zf.runtime.artifact_read_ledger import build_attempt_source_manifest, read_attempt_artifact
    from zf.runtime.goal_closure_bridge import GoalClosureBridgeMixin
    from zf.runtime.goal_closure_identity import build_closure_identity
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    e = evidence
    # Cohort selection has its own tests. Use its declared output to exercise
    # the real freeze, both admissions and the Judge's identity/read-set builder.
    monkeypatch.setattr("zf.runtime.goal_judge_admissions.current_judge_admissions", lambda *a, **k: e.batch)
    payload = {"workflow_run_id": e.run, "goal_id": "goal", "candidate_head_commit": e.target,
               "task_map_generation": e.generation}
    def build():
        return build_closure_identity(e.events, source_event=e.passed, payload=payload, state_dir=e.state, flow_kind="issue")

    identity = build()
    fact = hydrate_sidecar_ref(e.state, {"ref": identity["closure_fact_ref"], "sha256": identity["closure_fact_digest"]}).payload
    assert fact["task_verify_evidence"] == e.read()
    assert identity["closure_identity"] == build()["closure_identity"]
    bridge = GoalClosureBridgeMixin()
    bridge.state_dir, bridge.project_root, bridge.config = e.state, e.runtime.project_root, e.runtime.config
    contract = bridge._goal_closure_read_contract(e.events, workflow_run_id=e.run, goal_id="goal", identity=identity,
                                                 claim_set={}, payload=payload)
    loc = next(d for d in contract["input_refs"] if d["kind"] == "task_verification_result")
    assert contract["input_result_refs"] == [e.reader.payload["envelope_ref"]["ref"]]
    assert all(r["source_id"] != loc["source_id"] for r in contract["required_reads"])
    manifest = build_attempt_source_manifest(workflow_run_id=e.run,task_id="anchor", attempt_id="judge", dispatch_id="judge",
                                             sources=contract["input_refs"])
    read = read_attempt_artifact(e.state, manifest=manifest, source_id=loc["source_id"], artifact_id=loc["artifact_id"],
                                 json_path="$", actor="judge", purpose="thin-judge-goal-closure", record=False)
    assert json.loads(read["content"])["summary"] == "frozen check ran"
    e.pub.payload.pop("semantic_submit_admission_event_id")
    assert build()["closure_identity"] != identity["closure_identity"]
