import pytest

from tests.test_candidate_replan_dispositions import replan_case
from zf.runtime.artifact_read_ledger import ArtifactReadError, canonical_required_reads, read_attempt_artifact
from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
from zf.runtime.plan_synth_handoff import build_plan_handoff_input_refs
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.writer_fanout_admission import select_writer_task_items


def _diagnosis(tmp_path):
    runtime, decision, bound, _ = replan_case(tmp_path)
    assert apply_orchestrator_agent_decision(runtime, decision)["applied"]
    trigger, = [e for e in runtime.event_log.read_all() if e.type == "prd.plan.requested" and e.payload.get("rework_of")]
    return runtime, {**trigger.payload, "role_instance": "planner", "output_profile_id": "workflow-read"}, bound


@pytest.mark.parametrize("restricted", [False, True])
def test_planner_reads_only_authorized_cited_basis_and_matching_independent_readers(tmp_path, monkeypatch, restricted):
    from zf.runtime import orchestrator_agent_context

    original = orchestrator_agent_context.build_attempt_source_manifest

    def restrict_reader(**kwargs):
        sources = []
        for row in kwargs["sources"]:
            if row["source_id"] == "candidate-verification-verify-0":
                row = {**row, "allowed_paths": ["$.verdict"], "access_scope": {"roles": ["orchestrator", "planner"]}}
            if restricted and row["source_id"] == "candidate-verification-verify-peer":
                row = {**row, "access_scope": {"actors": ["orchestrator"]}}
            sources.append(row)
        return original(**{**kwargs, "sources": sources})

    monkeypatch.setattr(orchestrator_agent_context, "build_attempt_source_manifest", restrict_reader)
    from tests import test_orchestrator_agent_semantic_failure as oa_fixture

    def read_bound_path(state, **kwargs):
        if kwargs["source_id"] == "candidate-verification-verify-0":
            kwargs["json_path"] = "$.verdict"
        return read_attempt_artifact(state, **kwargs)

    monkeypatch.setattr(oa_fixture, "read_attempt_artifact", read_bound_path)
    runtime, payload, bound = _diagnosis(tmp_path)
    refs = build_plan_handoff_input_refs(state_dir=runtime.state_dir, project_root=runtime.project_root, payload=payload)
    sources = {r["source_id"]: r for r in refs}
    rework = hydrate_sidecar_ref(runtime.state_dir, sources["plan-rework-context"]).payload
    index = rework["candidate_diagnosis_evidence"]
    assert index["candidate_event_id"] == bound["candidate_event_id"] and index["failure_event_id"] == payload["rework_of"]
    assert index["basis"] == [{"source_id": "replan-evidence-effective-config", "status": "available"}]
    assert {row["finding_id"] for row in index["reader_findings"]} == {row["finding_id"] for row in bound["findings"]}
    retained = sources["replan-evidence-candidate-verification-verify-0"]
    assert retained["access_scope"] == {"roles": ["orchestrator", "planner"]} and retained["allowed_paths"] == ["$.verdict"]
    args = dict(manifest={"sources": refs}, source_id=retained["source_id"], artifact_id=retained["artifact_id"],
                role="planner", actor="planner", purpose="workflow-read", record=False)
    with pytest.raises(ArtifactReadError):
        read_attempt_artifact(runtime.state_dir, json_path="$", **args)
    assert read_attempt_artifact(runtime.state_dir, json_path="$.verdict", **args)["content"]
    peer = "replan-evidence-candidate-verification-verify-peer"
    assert (peer in sources) is not restricted
    if restricted:
        assert any(row.get("reason") == "outside_consumer_scope" for row in index["reader_findings"])
    required = canonical_required_reads({"sources": refs, "handoff_authority_contract": {"attempt_domain": "plan"}},
                                        output_profile_id="workflow-read")
    assert not any(row["source_id"].startswith("replan-evidence-") for row in required)
    assert not any("candidate-host-" in key for key in sources)
    assert select_writer_task_items({}, [{"task_id": "TASK-2"}], payload)[0] == []
    assert payload.get("task_ids", []) == []


@pytest.mark.parametrize("change", [{"pdd_id": "other"}, {"candidate_event_id": "other"},
    {"source_commit": "0" * 40}, {"rework_of": "other"}, {"task_ids": ["TASK-2"]}])
def test_unbound_candidate_diagnosis_never_transfers_evidence(tmp_path, change):
    runtime, payload, _ = _diagnosis(tmp_path)
    with pytest.raises(ValueError, match="Candidate diagnosis"):
        build_plan_handoff_input_refs(state_dir=runtime.state_dir, project_root=runtime.project_root,
                                      payload={**payload, **change})


def test_missing_or_ambiguous_independent_source_is_explicit_without_using_another_occurrence(tmp_path, monkeypatch):
    from zf.runtime import orchestrator_agent_context
    original = orchestrator_agent_context.build_attempt_source_manifest

    def omit_peer(**kwargs):
        refs = [s for s in kwargs["sources"] if s["source_id"] != "candidate-verification-verify-peer"]
        return original(**{**kwargs, "sources": refs})
    monkeypatch.setattr(orchestrator_agent_context, "build_attempt_source_manifest", omit_peer)
    runtime, payload, _ = _diagnosis(tmp_path)
    sources = {r["source_id"]: r for r in build_plan_handoff_input_refs(state_dir=runtime.state_dir,
               project_root=runtime.project_root, payload=payload)}
    rows = hydrate_sidecar_ref(runtime.state_dir, sources["plan-rework-context"]).payload["candidate_diagnosis_evidence"]
    assert "replan-evidence-candidate-verification-verify-peer" not in sources
    assert any(row.get("reason") == "missing_or_ambiguous_oa_source" for row in rows["reader_findings"])


@pytest.mark.parametrize("fault", ["shadow", "worker_marker", "ambiguous_marker"])
def test_unadmitted_or_ambiguous_diagnosis_cannot_transfer_evidence(tmp_path, fault):
    from dataclasses import replace

    runtime, payload, _ = _diagnosis(tmp_path)
    history = runtime.event_log.read_all()
    if fault == "shadow":
        history = [replace(e, payload={**e.payload, "checkpoint_policy": "shadow"})
                   if e.type == "orchestrator.semantic.decision.admitted" else e for e in history]
    else:
        marker, = [e for e in history if e.type == "orchestrator.replan_requested"]
        if fault == "ambiguous_marker":
            history = [*history, replace(marker, id="evt-duplicate")]
        else:
            history = [replace(e, origin="external") if e == marker else e for e in history]
    with pytest.raises(ValueError, match="Candidate diagnosis"):
        build_plan_handoff_input_refs(state_dir=runtime.state_dir, project_root=runtime.project_root, payload=payload, read_events=lambda: history)


def test_unbound_legacy_scope_returns_only_existing_authorities(tmp_path):
    runtime, payload, _ = _diagnosis(tmp_path)
    summary = dict(payload["rework_summary"])
    summary.pop("candidate_obligations")
    refs = build_plan_handoff_input_refs(state_dir=runtime.state_dir, project_root=runtime.project_root,
                                         payload={**payload, "rework_summary": summary})
    sources = {r["source_id"]: r for r in refs}
    context = hydrate_sidecar_ref(runtime.state_dir, sources["plan-rework-context"]).payload
    assert context["candidate_diagnosis_evidence"] == {"status": "unavailable", "reason": "no_bound_candidate_obligations"}
    assert "baseline-plan-port-task_map" in sources
    assert not any(source_id.startswith("replan-evidence-") for source_id in sources)


@pytest.mark.parametrize("roles", [["orchestrator", "planner"], ["orchestrator"]])
def test_citing_reader_cannot_expand_its_original_occurrence_scope(tmp_path, monkeypatch, roles):
    from functools import partial
    from tests import test_candidate_replan_dispositions as case
    from zf.runtime import call_result_envelope

    write = call_result_envelope.write_immutable_json_sidecar

    def restrict_original(*args, **kwargs):
        ref = write(*args, **kwargs)
        if kwargs["root"] == "fixtures/peer":
            return {**ref, "access_scope": {"roles": roles}, "allowed_paths": ["$.verdict"]}
        return ref

    monkeypatch.setattr(call_result_envelope, "write_immutable_json_sidecar", restrict_original)
    monkeypatch.setattr(case, "_submit_rework", partial(case._submit_rework, basis_source_id="candidate-verification-verify-peer"))
    runtime, payload, _ = _diagnosis(tmp_path)
    sources = {r["source_id"]: r for r in build_plan_handoff_input_refs(state_dir=runtime.state_dir,
               project_root=runtime.project_root, payload=payload)}
    index = hydrate_sidecar_ref(runtime.state_dir, sources["plan-rework-context"]).payload["candidate_diagnosis_evidence"]
    source_id = "replan-evidence-candidate-verification-verify-peer"
    if "planner" in roles:
        assert index["basis"] == [{"source_id": source_id, "status": "available"}]
        assert sources[source_id]["allowed_paths"] == ["$.verdict"]
    else:
        assert index["basis"] == [{"reason": "outside_consumer_scope", "status": "unavailable"}]
        assert source_id not in sources
