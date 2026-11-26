from copy import deepcopy
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_goal_task_verify_evidence import evidence
from zf.core.events import ZfEvent
from zf.runtime.artifact_output_delivery import emit_artifact_output
from zf.runtime.artifact_read_ledger import (
    active_read_ledger_path, build_attempt_source_manifest, read_attempt_artifact, seal_read_ledger,
)
from zf.runtime.call_result_correction import repair_instruction
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.goal_closure_result import claim_set_issues
from zf.runtime.goal_task_verify_citations import readable_task_citations


@pytest.fixture
def case(evidence, monkeypatch):
    e = evidence
    rows = e.read()
    entry, = rows
    source_id = entry["source_id"]
    def store(data):
        return write_immutable_json_sidecar(e.state, data, root="fixture/judge", kind="fixture",
                                            schema_version=data["schema_version"], created_by="test")
    fact = store({"schema_version": "goal-closure-fact.v1", "task_verify_evidence": rows})
    current = {"workflow_run_id": e.run, "has_task_verify_evidence": True, "judge_batch_admissions": e.batch,
               "closure_fact_ref": fact["ref"], "closure_fact_digest": fact["sha256"], "contract_snapshot_digest": "f"*64,
               "task_map_generation": e.generation, "candidate_head_commit": e.target}
    invocation = {"operation_id": "judge-operation", "attempt_id": "judge-attempt", "dispatch_id": "judge-dispatch"}
    judge = {"workflow_run_id": e.run, "task_id": "anchor", **invocation, "request_hash": "b"*64, "role_instance": "judge"}
    result = {**current, **judge}
    manifest = build_attempt_source_manifest(
        **{k: judge[k] for k in ("workflow_run_id", "task_id", "attempt_id", "dispatch_id")},
        sources=[{**entry["control_result"], "source_id": source_id, "artifact_id": source_id, "allowed_paths": ["$"]},
                 {**entry["envelope"], "source_id": "admission", "artifact_id": "envelope", "allowed_paths": ["$"]}])
    request = {**result, "output_profile_id": "thin-judge-goal-closure", "attempt_source_manifest": store(manifest)}
    dispatch = ZfEvent(type="fanout.child.dispatched", origin="kernel", payload={**invocation, "payload": request})
    e.events.append(dispatch)
    # Canonical policy binding has its own coverage. Use the actual fixture's
    # candidate/member WRC, immutable sources, production and sealed read paths.
    monkeypatch.setattr("zf.runtime.goal_task_verify_citations.pinned_delivery_policy",
                        lambda *args, **kw: {"attempt_id": judge["attempt_id"], "workflow_run_id": e.run})
    envelope = entry["envelope"]["ref"]
    def citations(seal=None, *, ref=envelope):
        return readable_task_citations(e.state, e.events, current, result, {ref}, ledger_descriptor=seal)
    def deliver(*, page_size=0, **overrides):
        read = read_attempt_artifact(e.state, manifest=manifest, source_id=source_id, artifact_id=source_id,
                 record=False, actor=judge["role_instance"], **({"page_size": page_size, "page_start": 0} if page_size else {}))
        receipt = {**read["read"], "operation_id": judge["operation_id"], **overrides}
        emit_artifact_output(e.state, read["content"], [receipt], StringIO())
    def seal():
        return seal_read_ledger(e.state, judge["attempt_id"])
    return SimpleNamespace(e=e, envelope=envelope, body=entry["control_result"]["ref"], cite=citations, deliver=deliver,
                           result=result, dispatch=dispatch, manifest=manifest, seal=seal, store=store)


def test_current_claim_citation_requires_exact_proof_on_preflight_and_submit(case):
    c = case
    assert c.cite() == set()
    c.deliver()
    assert c.cite() == c.cite() == {c.envelope}
    parent = active_read_ledger_path(c.e.state, c.result["attempt_id"]).parent
    assert not list(parent.glob("read-ledger-*.jsonl"))
    sealed = c.seal()
    assert c.cite(sealed) == {c.envelope}
    assert c.cite({}, ref=c.envelope) == set()
    assert c.cite({**sealed, "sha256": "0"*64}) == set()
    assert c.cite({**sealed, "ref": "artifacts/old/"+Path(sealed["ref"]).name}) == set()
    # Redelivery of exactly the same immutable dispatch is idempotent.
    c.e.events.append(deepcopy(c.dispatch))
    assert c.cite(sealed) == {c.envelope}
    assert c.cite(sealed, ref=c.body) == set()


@pytest.mark.parametrize("fault", ["wrong_task", "wrong_run", "wrong_attempt", "wrong_operation", "wrong_actor",
    "wrong_source_ref", "wrong_source_digest", "only_first_page", "unbound_wrc", "foreign_member", "grant_disappeared",
    "inaccessible_envelope", "impostor_manifest", "conflicting_redelivery", "mutation_after_read"])
def test_stale_partial_and_unauthorized_evidence_cannot_support_a_claim(case, fault):
    c = case
    wrong = {"wrong_task":"task_id", "wrong_run":"workflow_run_id", "wrong_attempt":"attempt_id",
             "wrong_operation":"operation_id", "wrong_actor":"consumer_actor", "wrong_source_ref":"artifact_ref",
             "wrong_source_digest":"artifact_sha256"}
    c.deliver(page_size=5 if fault == "only_first_page" else 0, **({wrong[fault]:"old"} if fault in wrong else {}))
    if fault == "unbound_wrc":
        c.e.adm.payload["request_hash"] = "c"*64
    elif fault == "foreign_member":
        c.e.freeze.payload["completed_task_ids"] = ["unrelated"]
    elif fault in {"grant_disappeared", "inaccessible_envelope", "impostor_manifest", "conflicting_redelivery"}:
        manifest = deepcopy(c.manifest)
        if fault == "grant_disappeared":
            manifest["sources"].pop()
        elif fault == "inaccessible_envelope":
            manifest["sources"][-1]["access_scope"] = {"actors": ["other"]}
        else:
            manifest["task_id"] = "elsewhere"
        if fault == "conflicting_redelivery":
            dispatch = deepcopy(c.dispatch)
            c.e.events.append(dispatch)
        else:
            dispatch = c.dispatch
        dispatch.payload["payload"]["attempt_source_manifest"] = c.store(manifest)
    elif fault == "mutation_after_read":
        (c.e.state / c.body).write_bytes(b"{}")
    assert c.cite() == set()
    if fault != "wrong_attempt":
        assert c.cite(c.seal()) == set()


def test_task_citations_do_not_substitute_for_current_candidate_or_discovery():
    claims = {"claims": [{"goal_claim_id":"A"}, {"goal_claim_id":"B"}]}
    result = {"goal_coverage": [
        {"goal_claim_id":"A", "status":"closed", "supporting_result_refs":["task", "candidate"]},
        {"goal_claim_id":"B", "status":"closed", "supporting_result_refs":["task", "discovery"]},
    ]}
    def issues():
        return claim_set_issues(result, claims, admitted_result_refs={"candidate", "discovery", "historical"},
                                auxiliary_refs={"task"}, candidate_refs={"candidate", "discovery"})
    assert issues() == []
    result["goal_coverage"][1]["supporting_result_refs"] = ["task", "task", "historical"]
    problem, = issues()
    assert problem["code"] == "candidate_support_required"
    assert problem["field"] == "goal_coverage[1].supporting_result_refs"
    assert "current admitted Candidate or Discovery" in repair_instruction(problem)
    result["goal_coverage"][1]["supporting_result_refs"] = ["candidate", "alien"]
    problem, = issues()
    assert problem["code"] == "result_not_admitted"
    assert "fully read" in repair_instruction(problem)
    result["goal_coverage"][1]["status"] = "open"
    assert [issue["code"] for issue in issues()] == ["result_not_admitted"]


@pytest.mark.parametrize("backend", ["codex", "claude-code"])
def test_role_materialization_includes_current_judge_citation_guidance(tmp_path, backend):
    from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
    from zf.core.skills.materialize import materialize_role_skills

    name = "zf-goal-closure-judge-contract"
    root = Path(__file__).resolve().parents[1]
    config = ZfConfig(project=ProjectConfig(name="judge"))
    role = RoleConfig(name="judge", backend=backend, skills=[name])
    output = materialize_role_skills(config=config, project_root=root, state_dir=tmp_path/"state", role=role)
    entry, = output.skills
    assert entry.status == "resolved"
    actual = Path(entry.materialized_to)/"SKILL.md"
    assert actual.read_bytes() == (root/"skills"/name/"SKILL.md").read_bytes()
    assert "closure-fact.task_verify_evidence" in actual.read_text()


def test_validate_entrypoint_applies_judge_citation_rules_before_first_seal(case, monkeypatch):
    from tests.test_goal_closure_result import _result
    from zf.runtime.result_submit import SemanticResultSubmitService

    c = case
    c.deliver()
    # Only the transport capability has already been checked in this fixture.
    # Run the normal result validate routing, adapter and attempt-bound coverage check.
    admission = SemanticResultSubmitService(state_dir=c.e.state, event_log=c.e.runtime.event_log,
                                             event_writer=c.e.runtime.event_writer)
    claim_set = c.store({"schema_version": "goal-claim-set.v1", "claims": [{"goal_claim_id": "GOAL-AC-1"}]})
    primary = c.e.reader.payload["envelope_ref"]["ref"]
    body = {**_result(), **c.result, "goal_claim_set_ref": claim_set["ref"], "goal_claim_set_digest": claim_set["sha256"],
            "target_commit": c.e.target, "input_result_refs": [primary]}
    body["goal_coverage"][0]["supporting_result_refs"] = [primary, c.envelope]
    c.e.events.append(ZfEvent(type="flow.goal.closed", origin="kernel", payload=body.copy()))
    role = c.result["role_instance"]
    profile = "thin-judge-goal-closure"
    request = {"canonical_success_event": "judge.child.completed", "output_profile_id": profile,
               "output_profile_revision": "1", "result_identity": {"task_id": body["task_id"]}}
    operation = {**c.result, "active_attempt_id": c.result["attempt_id"]}
    monkeypatch.setattr(admission, "_authorized_semantic_result",
                        lambda **kw: (operation, request, kw["semantic_result"], profile, "1"))
    outcome = admission.validate_plan_candidate(operation_id=body["operation_id"], semantic_result=body,
                                               role_instance=role, credential="fixture")
    assert outcome["errors"] == [] and outcome["status"] == "passed"
    parent = active_read_ledger_path(c.e.state, body["attempt_id"]).parent
    assert not list(parent.glob("read-ledger-*.jsonl"))
    # Even an explicit Discovery supporting attachment is not an independent Candidate vote.
    c.e.batch["supporting"] = [c.e.adm.id]
    body["input_result_refs"].append(c.envelope)
    body["goal_coverage"][0]["supporting_result_refs"] = [c.envelope]
    outcome = admission.validate_plan_candidate(operation_id=body["operation_id"], semantic_result=body,
                                               role_instance=role, credential="fixture")
    assert [e["code"] for e in outcome["errors"]] == ["candidate_support_required"]
    body["goal_coverage"][0]["supporting_result_refs"] = [primary, c.envelope]
    active_read_ledger_path(c.e.state, body["attempt_id"]).unlink()
    outcome = admission.validate_plan_candidate(operation_id=body["operation_id"], semantic_result=body,
                                               role_instance=role, credential="fixture")
    assert [e["code"] for e in outcome["errors"]] == ["result_not_admitted"]
