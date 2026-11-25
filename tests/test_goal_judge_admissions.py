"""Normal Goal closure uses the declared Reader batch and its own Discovery admission."""
from copy import deepcopy

import pytest

from tests.test_goal_candidate_batch import GEN, IDENTITY, NEW, REF, RUN_ID, TARGET, batch, event
from zf.core.config.schema import ZfConfig
from zf.core.events import EventLog, EventWriter
from zf.runtime.call_result_envelope import normalize_call_result_envelope, write_immutable_json_sidecar
from zf.runtime.goal_closure_bridge import GoalClosureBridgeMixin


def closure(tmp_path, fault="", *, judge_members=(), advisory_state=""):
    bridge = GoalClosureBridgeMixin()
    bridge.state_dir = bridge.project_root = tmp_path
    bridge.config = ZfConfig()
    bridge.event_log = EventLog(tmp_path / "events.jsonl")
    bridge.event_writer = EventWriter(bridge.event_log)
    original, current = batch("old", NEW), batch("current", NEW)
    old_admit = original[3]
    # First attempt for current Reader 0 has a real admitted abstention, followed by its replacement.
    obsolete = deepcopy(current[2:5])
    for item in obsolete:
        item.id += "-obsolete"
        item.payload["operation_id"] += "-obsolete"
    obsolete[0].type = "verify.child.failed"
    obsolete[0].payload["semantic_submit_admission_event_id"] = obsolete[1].id
    obsolete[1].payload.update(source_event_id=obsolete[0].id, semantic_verdict="abstained")
    obsolete[2].type = "fanout.child.failed"
    obsolete[2].causation_id = obsolete[0].id
    obsolete[2].payload["semantic_verdict"] = "abstained"
    current[2:2] = obsolete
    events = [*original, *current]
    pin = write_immutable_json_sidecar(tmp_path, {"claims": []}, root="claims", kind="claims",
                                        schema_version="claims.v1", created_by="test")
    events.append(event("goal.claim_set.pinned", "claims", goal_id="goal", task_map_generation=GEN,
                        goal_claim_set_ref=pin["ref"], goal_claim_set_digest=pin["sha256"]))
    fan = "discovery"
    role = "reviewer"
    body = {**IDENTITY, "fanout_id": fan, "role_instance": role, "child_id": role,
            "operation_id": "op-disc", "attempt_id": "att-disc", "control_result_schema": "workflow-read-result.v1",
            "control_result_ref": {"ref": "control/discovery", "sha256": "d"*64}}
    request = event("flow.discovery.requested", "req", cause="pub-old" if fault == "old-publication" else "pub-current")
    start = event("fanout.started", "disc-start", fanout_id=fan, trigger_event_id=request.id,
                  aggregate={"child_success_event": "flow.discovery.child.completed"},
                  expected_children=[{"child_id": role, "role_instance": role}])
    vote = event("flow.discovery.child.completed", "disc-vote", semantic_submit_admission_event_id="disc-adm", **body)
    disc = event("workflow.call.result.admitted", "disc-adm", source_event_id=vote.id, **body)
    terminal = event("fanout.child.completed", "disc-terminal", cause=vote.id, **body)
    agg = event("fanout.aggregate.completed", "disc-agg", fanout_id=fan, status="completed",
                success_event="flow.discovery.completed")
    source = event("flow.discovery.completed", "disc-source", cause=agg.id, fanout_id=fan,
                   candidate_head_commit=TARGET, candidate_ref=REF, task_map_generation=GEN)
    events += [request, start, vote, disc, terminal, agg, source]
    if judge_members:
        bridge.config.workflow.flow_metadata = {"stage_teams": {"judge": {"author": "judge", "members": list(judge_members)}}}
        prior = []
        late = []
        for member in judge_members:
            role = member["role"]
            obligation = {**member, "recipient": "judge"}
            child = {"child_id": role, "role_instance": role, "payload": {"stage_obligation": obligation}}
            required = member.get("required", True)
            if required:
                start.payload["expected_children"].append(child)
                if fault == "missing-specialist":
                    continue
            else:
                prior.append(event("fanout.started", "advisory-start", fanout_id="advisory", stage_id="prd-judge-advisory",
                                   trigger_event_id="old-req" if advisory_state == "old-request" else request.id,
                                   expected_children=[child]))
                if advisory_state == "waiting":
                    continue
            opinion = {**body, "fanout_id": fan if required else "advisory", "child_id": role, "role_instance": role,
                       "operation_id": "op-" + role, "attempt_id": "attempt-" + role}
            if not required and advisory_state == "wrong-target":
                opinion["target_commit"] = "b"*40
            if not required and advisory_state == "wrong-generation":
                opinion["task_map_generation"] = "old-generation"
            if not required and advisory_state == "wrong-run":
                opinion["workflow_run_id"] = "other-run"
            if not required and advisory_state == "declined":
                kind, terminal_kind = "workflow.child.failed", "fanout.child.failed"
            else:
                kind, terminal_kind = ("flow.discovery.child.completed" if required else "workflow.child.completed"), "fanout.child.completed"
            destination = late if not required and advisory_state == "late" else prior
            destination.extend([event(kind, role+"-vote", semantic_submit_admission_event_id=role+"-adm", **opinion),
                                event("workflow.call.result.admitted", role+"-adm", source_event_id=role+"-vote", **opinion),
                                event(terminal_kind, role+"-terminal", cause=role+"-vote", **opinion)])
        events[events.index(agg):events.index(agg)] = prior
        events.extend(late)
    by_source = {e.causation_id: e for e in events if e.type in {"fanout.child.completed","fanout.child.failed"}}
    for e in events:
        if e.type != "workflow.call.result.admitted":
            continue
        p = e.payload
        raw = normalize_call_result_envelope(source_payload=dict(p, task_id="test"),
            control_result={**p["control_result_ref"], "schema_version": p["control_result_schema"]},
            workflow_run_id=RUN_ID, operation_id=p["operation_id"], request_hash="a"*64,
            source_event_id=p["source_event_id"], source_event_type=by_source[p["source_event_id"]].type,
            actor="test", task_id="test")
        ref = write_immutable_json_sidecar(tmp_path, raw, root="call-results/envelopes", kind="call_result",
                                           schema_version=raw["schema_version"], created_by="test")
        p["envelope_ref"] = by_source[p["source_event_id"]].payload["admitted_call_result_ref"] = ref
        if (fault == "missing-reader" and e.id == "admission-current-1") or (fault == "missing-discovery" and e is disc):
            (tmp_path / ref["ref"]).unlink()
    if fault in {"legacy", "missing-declaration"}:
        events = [e for e in events if e.type != "fanout.started"]
    if fault == "legacy":
        for e in events:
            e.payload.pop("output_profile_id", None)
    for e in events:
        bridge.event_writer.append(e)
    result = bridge._emit_current_goal_closure(event_type="flow.goal.closed", source_event=source,
        flow_kind="prd", payload={**source.payload, "goal_id": "goal"})
    return result, bridge.event_log.read_all(), old_admit.payload["envelope_ref"]


@pytest.mark.parametrize("fault", ["missing-reader", "missing-discovery", "old-publication", "missing-declaration"])
def test_missing_current_evidence_prevents_closure(tmp_path, fault):
    closed, ledger, _ = closure(tmp_path, fault)
    assert closed is None
    assert ledger[-1].type == "goal.closure.identity.invalid"
    reason = ledger[-1].payload["reason"]
    if fault == "missing-declaration":
        assert "lack their declared batch" in reason
    elif fault == "old-publication":
        assert "does not follow the current Candidate publication" in reason
    else:
        assert ("admission-current-1" if fault == "missing-reader" else "disc-adm") in reason


def test_same_sha_history_and_replaced_vote_are_inspectable_only(tmp_path):
    closed, _, old = closure(tmp_path)
    p = closed.payload
    assert len(p["input_result_refs"]) == 9
    assert old["ref"] not in p["input_result_refs"]
    required = {r["artifact_sha256"] for r in p["required_reads"]}
    assert old["sha256"] not in required
    assert any(r["ref"] == old["ref"] for r in p["input_refs"])
    assert set(p["judge_batch_admissions"]["readers"]) == {"admission-current-" + str(i) for i in range(8)}
    assert p["judge_batch_admissions"]["discovery"] == ["disc-adm"]


def test_old_independent_verify_contract_retains_its_admitted_results(tmp_path):
    closed, _, old = closure(tmp_path, "legacy")
    assert "judge_batch_admissions" not in closed.payload
    assert old["ref"] in closed.payload["input_result_refs"]
    assert old["sha256"] in {r["artifact_sha256"] for r in closed.payload["required_reads"]}


@pytest.mark.parametrize("cite_history, kind", [(False, "readers"), (False, "advisory"), (True, "readers")])
def test_judge_submission_requires_current_support_for_closed_claims(tmp_path, cite_history, kind):
    from zf.core.events import ZfEvent
    from tests.test_call_result_admission import _append_admitted_upstream, _goal_closure_payload, _runtime

    service, operations = _runtime(tmp_path)
    old = _append_admitted_upstream(tmp_path, operations, task_id="replaced-reader")
    active = _append_admitted_upstream(tmp_path, operations, task_id="current-reader")
    admitted = operations.event_log.read_all()[-1]
    claims = write_immutable_json_sidecar(tmp_path, {"claims": [{"goal_claim_id": "GOAL-AC-1"}]},
        root="claims", kind="claim-set", schema_version="goal-claim-set.v1", created_by="test")
    payload = _goal_closure_payload(claim_descriptor=claims, upstream_ref=old if cite_history else active)
    result = payload["goal_closure_result"]
    operations.event_log.append(ZfEvent(type="flow.goal.closed", origin="kernel", correlation_id="run-1", payload={
        **result, "candidate_head_commit": result["target_commit"], "input_result_refs": [active],
        "judge_batch_admissions": {"readers": [], "discovery": [], "supporting": [], kind: [admitted.id]},
    }))
    op = operations.ensure_operation(workflow_run_id="run-1", operation_id="judge", operation_type="agent",
                                     request={"goal": "GOAL-1"})
    verdict = service.report_legacy_result(
        ZfEvent(type="judge.child.completed", actor="judge-prd", correlation_id="run-1", payload=payload),
        mode="blocking", operation={"workflow_run_id": "run-1", "operation_id": "judge",
                                    "request_hash": op.request_hash})
    assert verdict.admitted is (not cite_history)
    if cite_history:
        fields = {i["field"] for i in verdict.issues if i["code"] == "result_not_admitted"}
        assert fields == {"control_result.input_result_refs", "goal_coverage[0].supporting_result_refs"}


@pytest.mark.parametrize("state", ["waiting", "declined", "on-time", "late", "old-request", "wrong-target", "wrong-generation", "wrong-run"])
def test_formal_judge_only_waits_for_required_opinions_and_freezes_advisory_at_publication(tmp_path, state):
    members = [{"obligation_id": "must", "role": "risk-reviewer"},
               {"obligation_id": "may", "role": "advisor", "required": False}]
    closed, events, _ = closure(tmp_path, judge_members=members, advisory_state=state)
    assert closed and closed.type == "flow.goal.closed"
    batch = closed.payload["judge_batch_admissions"]
    assert set(batch["discovery"]) == {"disc-adm", "risk-reviewer-adm"}
    assert batch.get("advisory", []) == (["advisor-adm"] if state == "on-time" else [])
    options = [r for r in closed.payload["input_refs"] if r["kind"] == "judge_advisory_result"]
    assert len(options) == int(state == "on-time")
    if options:
        assert options[0]["ref"] in closed.payload["input_result_refs"]
        assert all(r["artifact_sha256"] != options[0]["sha256"] for r in closed.payload["required_reads"])
    other = tmp_path / "missing-required"
    other.mkdir()
    blocked, ledger, _ = closure(other, "missing-specialist", judge_members=members, advisory_state=state)
    assert blocked is None and ledger[-1].type == "goal.closure.identity.invalid"
