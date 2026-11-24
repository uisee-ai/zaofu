"""Replanning can read its pinned baseline and admitted OA decision."""

import pytest

from zf.core.events.model import ZfEvent
from tests.test_orchestrator_agent_semantic_failure import _runtime, _semantic_request, _submit_rework
from tests.test_plan_artifact_package import _package
from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
from zf.runtime.orchestrator_agent_semantic_failure import request_semantic_failure_checkpoint
from zf.runtime.plan_artifact_package import write_plan_artifact_package
from zf.runtime.plan_synth_handoff import build_plan_handoff_input_refs
from zf.runtime.artifact_read_ledger import canonical_required_reads, read_attempt_artifact
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import load_workflow_operation
from zf.runtime.plan_replan_authority_inputs import _decision_evidence_sources


def _return_to_plan(tmp_path):
    runtime = _runtime(tmp_path)
    prepared = request_semantic_failure_checkpoint(runtime, _semantic_request(runtime))
    apply_orchestrator_agent_decision(runtime, _submit_rework(runtime, prepared, action="return_to_plan"))
    events = runtime.event_log.read_all()
    marker = next(e for e in events if e.type == "orchestrator.replan_requested")
    trigger = next(e for e in events if e.type == "prd.plan.requested")
    return runtime, marker, trigger


def test_oa_replan_trigger_carries_decision_and_delta(tmp_path):
    _runtime, marker, trigger = _return_to_plan(tmp_path)
    for key in ("orchestrator_decision_ref", "orchestration_delta_ref", "orchestration_delta_digest"):
        assert trigger.payload.get(key) == marker.payload[key]


@pytest.mark.parametrize("legacy", [False, True])
def test_replan_input_manifest_exposes_current_package_ports_and_oa_authority(tmp_path, legacy):
    runtime, marker, trigger = _return_to_plan(tmp_path)
    payload = dict(trigger.payload)
    if legacy:
        for key in ("orchestrator_decision_ref", "orchestration_delta_ref", "orchestration_delta_digest"):
            payload.pop(key, None)
    refs = build_plan_handoff_input_refs(
        state_dir=runtime.state_dir, project_root=runtime.project_root, payload=payload,
    )
    sources = {r["source_id"]: r for r in refs}
    assert {"plan-rework-package", "plan-rework-decision", "plan-rework-delta",
            "baseline-plan-port-task_map"} <= sources.keys()
    decision = hydrate_sidecar_ref(runtime.state_dir, sources["plan-rework-decision"]).payload
    assert decision["decision"] == "return_to_plan"
    basis = decision["delta"]["directives"][0]["basis_refs"][0]
    assert any(key.startswith("replan-evidence-") and row["ref"] == basis["ref"]
               and row["sha256"] == basis["sha256"] for key, row in sources.items())
    assert sources["plan-rework-decision"]["sha256"] == marker.payload["orchestrator_decision_ref"]["sha256"]
    required = canonical_required_reads(
        {"sources": refs, "handoff_authority_contract": {"attempt_domain": "plan"}},
        output_profile_id="workflow-read",
    )
    ids = {row["source_id"] for row in required}
    assert {"plan-rework-package", "plan-rework-decision", "plan-rework-delta"} <= ids
    assert not any(source.startswith("baseline-plan-port-") for source in ids)
    source = sources["baseline-plan-port-task_map"]
    result = read_attempt_artifact(
        runtime.state_dir, manifest={"sources": refs}, source_id=source["source_id"],
        artifact_id=source["artifact_id"], json_path="$", record=False,
    )
    assert "TASK-1" in result["content"]


@pytest.mark.parametrize("corrupt", ["package", "port"])
def test_replan_baseline_digest_mismatch_fails_closed(tmp_path, corrupt):
    package = _package(tmp_path)
    ref = write_plan_artifact_package(tmp_path, package)
    payload = {"workflow_run_id": "run-1", "rework_of": "failure",
               "plan_artifact_package_ref": ref["ref"], "plan_artifact_package_digest": ref["sha256"]}
    path = ref["ref"] if corrupt == "package" else package["produced"][0]["ref"]
    (tmp_path / path).write_text('{"tampered":true}')
    with pytest.raises(ValueError):
        build_plan_handoff_input_refs(state_dir=tmp_path, project_root=tmp_path, payload=payload)


@pytest.mark.parametrize("mismatch", ["workflow_run_id", "task_map_generation", "fingerprint", "scope", "ambiguous"])
def test_legacy_authority_requires_exact_unambiguous_marker(tmp_path, mismatch):
    runtime, marker, trigger = _return_to_plan(tmp_path)
    payload = dict(trigger.payload)
    for key in ("orchestrator_decision_ref", "orchestration_delta_ref", "orchestration_delta_digest"):
        payload.pop(key, None)
    if mismatch == "fingerprint":
        payload["rework_summary"] = {"failure_fingerprint": "another-failure"}
    elif mismatch == "scope":
        payload["task_ids"] = ["TASK-2"]
    elif mismatch == "ambiguous":
        runtime.event_writer.append(ZfEvent(
            type=marker.type, actor="test", task_id=marker.task_id,
            payload={**marker.payload, "orchestration_delta_digest": "f" * 64},
        ))
    else:
        payload[mismatch] = "another-identity"
    with pytest.raises(ValueError):
        build_plan_handoff_input_refs(state_dir=runtime.state_dir, project_root=runtime.project_root, payload=payload)


def test_nested_trigger_preserves_nonempty_baseline_binding(tmp_path):
    runtime, _marker, trigger = _return_to_plan(tmp_path)
    refs = build_plan_handoff_input_refs(
        state_dir=runtime.state_dir, project_root=runtime.project_root,
        payload={"trigger_payload": trigger.payload, "plan_artifact_package_ref": ""},
    )
    assert any(r["source_id"] == "plan-rework-decision" for r in refs)


def test_cited_oa_evidence_keeps_original_descriptor_and_rejects_tampering(tmp_path):
    runtime, marker, _trigger = _return_to_plan(tmp_path)
    decision = hydrate_sidecar_ref(runtime.state_dir, marker.payload["orchestrator_decision_ref"]).payload
    operation = load_workflow_operation(runtime.event_log, decision["operation_id"])
    request = hydrate_sidecar_ref(runtime.state_dir, operation["request_ref"]).payload["request"]
    manifest = hydrate_sidecar_ref(runtime.state_dir, request["source_manifest_ref"]).payload
    original = manifest["sources"][0]
    # Isolate the legacy top-level shape from current directive evidence.
    decision.pop("delta", None)
    decision["evidence_refs"] = [original["ref"], "artifacts/unregistered.json"]
    refs = _decision_evidence_sources(runtime.state_dir, decision)
    assert refs == [{**original, "source_id": f"replan-evidence-{original['source_id']}"}]
    (runtime.state_dir / original["ref"]).write_text('{"tampered":true}')
    with pytest.raises(ValueError):
        _decision_evidence_sources(runtime.state_dir, decision)


def test_replan_does_not_import_other_action_or_wrong_digest(tmp_path):
    runtime, marker, _trigger = _return_to_plan(tmp_path)
    decision = hydrate_sidecar_ref(runtime.state_dir, marker.payload["orchestrator_decision_ref"]).payload
    directive = decision["delta"]["directives"][0]
    unchanged = {**directive, "action": "rework"}
    wrong_digest = {**directive, "basis_refs": [{**directive["basis_refs"][0], "sha256": "f"*64}]}
    decision["delta"]["directives"] = [unchanged, wrong_digest]
    decision.pop("evidence_refs", None)
    assert _decision_evidence_sources(runtime.state_dir, decision) == []


def test_replan_only_exposes_directives_for_scoped_tasks(tmp_path):
    runtime, marker, _trigger = _return_to_plan(tmp_path)
    decision = hydrate_sidecar_ref(runtime.state_dir, marker.payload["orchestrator_decision_ref"]).payload
    directive = decision["delta"]["directives"][0]
    op = load_workflow_operation(runtime.event_log, decision["operation_id"])
    request = hydrate_sidecar_ref(runtime.state_dir, op["request_ref"]).payload["request"]
    manifest = hydrate_sidecar_ref(runtime.state_dir, request["source_manifest_ref"]).payload
    other_source = next(s for s in manifest["sources"] if s["ref"] != directive["basis_refs"][0]["ref"])
    other = {**directive, "target": {**directive["target"], "task_id": "TASK-2"},
             "basis_refs": [{"ref": other_source["ref"], "sha256": other_source["sha256"]}]}
    decision["delta"]["directives"].append(other)
    decision.pop("evidence_refs", None)
    assert _decision_evidence_sources(runtime.state_dir, decision, task_ids=set()) == []
    refs = _decision_evidence_sources(runtime.state_dir, decision, task_ids={"TASK-1"})
    assert [r["ref"] for r in refs] == [directive["basis_refs"][0]["ref"]]


def test_new_oa_replan_only_exposes_prior_operator_grant_as_history(tmp_path):
    runtime = _runtime(tmp_path)
    prior = {
        "operator_feedback": ["Restore the blank-input assertion."],
        "stage_replan_generation": "owner-blank-input-generation",
        "stage_replan_generation_source_event_id": "prior-escalation",
        "operator_authorized": True,
        "operator_authorization_ref": "owner:blank-input",
    }
    old = runtime.event_writer.append(ZfEvent(
        type="prd.plan.requested", correlation_id="run-semantic-1",
        payload={"rework_of": "prior-failure", **prior},
    ))
    prepared = request_semantic_failure_checkpoint(runtime, _semantic_request(runtime))
    outcome = apply_orchestrator_agent_decision(
        runtime, _submit_rework(runtime, prepared, action="return_to_plan"),
    )
    assert outcome["applied"]
    events = runtime.event_log.read_all()
    trigger = next(e for e in events if e.type == old.type and e.id != old.id)
    assert prior.keys().isdisjoint(trigger.payload)
    history = trigger.payload["historical_orchestration_context"]
    assert history["authority"] == "observation_only"
    assert history["source_rework_of"] == "prior-failure"
    assert {key: history[key] for key in prior} == prior
    refs = build_plan_handoff_input_refs(
        state_dir=runtime.state_dir, project_root=tmp_path, payload=trigger.payload,
        source_event_id=trigger.id,
    )
    context = next(row for row in refs if row["source_id"] == "plan-rework-context")
    body = hydrate_sidecar_ref(runtime.state_dir, context).payload
    assert "operator_feedback" not in body
    assert body["rework_feedback"] == trigger.payload["rework_feedback"]
    assert body["historical_orchestration_context"] == history
    assert old.payload["operator_feedback"] == prior["operator_feedback"]
