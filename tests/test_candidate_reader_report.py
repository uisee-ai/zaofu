"""Bounded reader admission and the single full-product discovery handoff."""
from pathlib import Path

import pytest

from tests import test_product_acceptance as product
from tests.test_scoped_candidate_verify import _discovery_operation, _discovery_result
from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import canonical_json_sha256
from zf.runtime.candidate_reader_assignments import candidate_acceptance_id
from zf.runtime.flow_discovery_context import build_flow_discovery_context
from zf.runtime.product_acceptance import bind_product_acceptance_report
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


@pytest.fixture
def plan(tmp_path, monkeypatch):
    spec = product._spec()
    spec["candidate_reader_assignments"] = [
        {"role_instance": "behavior", "acceptance": [{"task_id": "T1", "acceptance_id": "AC-1"}],
         "journey_ids": ["journey-create"]},
        {"role_instance": "boundary", "acceptance": [{"task_id": "T1", "acceptance_id": "AC-1"}],
         "journey_ids": []},
    ]
    monkeypatch.setattr(product, "_spec", lambda **_: spec)
    package, descriptor = product._package(tmp_path)
    return spec, package, descriptor


def _event(kind, **payload):
    return ZfEvent(type=kind, origin="kernel", payload={"workflow_run_id": product.RUN_ID, **payload})


def _reader(plan, role, freeze_id):
    spec, package, descriptor = plan
    ac = candidate_acceptance_id("T1", "AC-1")
    journeys = next(row["journey_ids"] for row in spec["candidate_reader_assignments"]
                    if row["role_instance"] == role)
    scope = {"role_instance": role, "acceptance_ids": [ac], "journey_ids": journeys,
             "target_commit": product.TARGET, "candidate_event_id": freeze_id,
             "plan_artifact_package_digest": package["sha256"],
             "assignments_digest": canonical_json_sha256(spec["candidate_reader_assignments"])}
    op = {"output_profile_id": "candidate-verify", "candidate_reader_scope": scope, "role_instance": role}
    candidate = product._adapted(product._report(descriptor))
    candidate.payload.update(plan_artifact_package_ref=package["ref"], plan_artifact_package_digest=package["sha256"],
                             role_instance=role, fanout_id="readers", requirement_results=[
                                 {"acceptance_id": ac, "status": "passed", "evidence_refs": ["probe"]}])
    if not journeys:
        candidate.payload.pop("product_acceptance_report")
    return candidate, op


@pytest.mark.parametrize("fault,detail", [
    ("missing_ac", "exactly"), ("unverified", "passing evidence"),
    ("extra_journey", "unknown journey"), ("missing_assertion", "mandatory assertions"),
    ("wrong_actor", "owner"), ("forged_full_ref", "full-product"), ("wrong_sha", "frozen target"),
])
def test_reader_scoped_admission_rejects_wrong_scope_or_unverified_results(tmp_path, plan, fault, detail):
    result, op = _reader(plan, "behavior", "freeze")
    if fault == "missing_ac":
        result.payload["requirement_results"].clear()
    elif fault == "unverified":
        result.payload["requirement_results"][0]["status"] = "not_applicable"
    elif fault == "extra_journey":
        result.payload["product_acceptance_report"]["journey_results"].append({"journey_id":"other","status":"passed"})
    elif fault == "missing_assertion":
        result.payload["product_acceptance_report"]["journey_results"][0]["assertion_results"].pop()
    elif fault == "wrong_actor":
        op["role_instance"] = "other"
    elif fault == "forged_full_ref":
        result.payload["product_acceptance_report_ref"] = "artifacts/fake.json"
    elif fault == "wrong_sha":
        op["candidate_reader_scope"]["target_commit"] = "f" * 40
    admission = bind_product_acceptance_report(result, state_dir=tmp_path, operation=op, source_event_id="test")
    assert detail in admission.issues[-1]["message"]


@pytest.mark.parametrize("fault", ["", "missing_owner", "stale_freeze", "pending_input", "unadmitted",
                                    "wrong_role_verdict", "global_assertion_missing", "duplicate", "valid_redrive"])
def test_only_full_current_scoped_group_reaches_discovery_report(tmp_path: Path, plan, fault: str):
    spec, package, descriptor = plan
    freeze = _event("candidate.ready", schema_version="candidate-freeze-receipt.v1", status="frozen",
                    candidate_head_commit=product.TARGET, plan_artifact_package_digest=package["sha256"])
    events = [freeze]
    if fault == "valid_redrive":
        events.append(_event("candidate.ready", candidate_snapshot_event_id=freeze.id))
    events.append(_event("fanout.started", fanout_id="readers", trigger_event_id=events[-1].id))
    for role in ("behavior", "boundary"):
        if fault == "missing_owner" and role == "boundary":
            continue
        child = _event("verify.child.completed", fanout_id="readers", target_commit=product.TARGET,
                       task_map_generation=product.GENERATION)
        result, op = _reader(plan, role, freeze.id)
        if fault == "wrong_role_verdict" and role == "boundary":
            result.payload.update(verdict="rejected", requirement_results=[
                {**result.payload["requirement_results"][0], "status":"failed"}])
        out = bind_product_acceptance_report(result, state_dir=tmp_path, operation=op, source_event_id=child.id)
        assert not out.issues
        stored = hydrate_sidecar_ref(tmp_path, out.descriptor).payload
        assert stored["candidate_reader_scope"]["role_instance"] == role
        replay = bind_product_acceptance_report(out, state_dir=tmp_path, operation=op, source_event_id=child.id)
        assert not replay.issues and replay.descriptor["sha256"] == out.descriptor["sha256"]
        assert "product_acceptance_report_ref" not in stored
        assert bool(stored["candidate_reader_journeys"]) == (role == "behavior")
        admission = _event("workflow.call.result.admitted", source_event_id=child.id,
                           control_result_schema="verification-result.v1", control_result_ref=out.descriptor)
        if fault == "unadmitted" and role == "boundary":
            admission.origin = "worker"
        events.extend([child, admission])
        if fault == "duplicate" and role == "behavior":
            copy = _event("verify.child.completed", **child.payload)
            result.payload["attempt_id"] = "second"
            duplicate = bind_product_acceptance_report(result, state_dir=tmp_path, operation=op, source_event_id=copy.id)
            events.extend([copy, _event("workflow.call.result.admitted", source_event_id=copy.id,
                                        control_result_schema="verification-result.v1", control_result_ref=duplicate.descriptor)])
    success = _event("test.passed", fanout_id="readers", candidate_head_commit=product.TARGET,
                     task_map_generation=product.GENERATION)
    context = build_flow_discovery_context(events, event=success, payload=success.payload, fallback={}, metadata={},
                                          pdd_id="p", feature_id="p", trace_id=product.RUN_ID, flow_kind="prd",
                                          discovery_profile="product_completeness")
    discovery = _event("flow.discovery.requested", **context.request_payload)
    discovery.causation_id = success.id
    if fault == "pending_input":
        discovery.payload["result_refs"] = []
    events.extend([success, discovery, _event("fanout.started", fanout_id="discovery", trigger_event_id=discovery.id)])
    if fault == "stale_freeze":
        events.append(_event("candidate.ready", **freeze.payload))
    formal = bind_product_acceptance_report(
        _discovery_result(product._report(descriptor, include_visual=fault != "global_assertion_missing")),
        state_dir=tmp_path, operation={**_discovery_operation(package), "fanout_id": "discovery"},
        events=events, source_event_id="whole",
    )
    passed = fault in {"", "valid_redrive"}
    assert bool(formal.issues) != passed, formal.issues
    if passed:
        assert formal.payload["product_acceptance_report_digest"]
        assert formal.payload["product_acceptance_verdict"] == "passed"
        report = hydrate_sidecar_ref(tmp_path, {"ref": formal.payload["product_acceptance_report_ref"],
                                               "sha256": formal.payload["product_acceptance_report_digest"]}).payload
        refs = report["candidate_reader_result_refs"]
        assert {ref["role_instance"] for ref in refs} == {"behavior", "boundary"}
        assert all(hydrate_sidecar_ref(tmp_path, ref).payload["verdict"] == "passed" for ref in refs)

@pytest.mark.parametrize("fault", ["missing_optional_ac", "missing_roster", "unassigned_check", "unknown_task", "missing_receipt", "missing_integration_evidence", "success"])
def test_frozen_cross_task_check_requires_reader_assignment_and_own_receipt(tmp_path, monkeypatch, fault):
    from zf.runtime.candidate_reader_assignments import (integration_acceptance_id, scope_reader_contract,
                                                        validate_plan_assignment_coverage, validate_reader_assignments)
    spec = product._spec()
    spec["integration_checks"] = [{"check_id": "I-1", "task_ids": ["T1", "T2"], "assertion": "An item created by the CLI is readable by the API."}]
    spec["candidate_reader_assignments"] = rows = [
        {"role_instance": "behavior", "acceptance": [{"task_id": "T1", "acceptance_id": "AC-1"}],
         "journey_ids": ["journey-create"]},
        {"role_instance": "boundary", "acceptance": [{"task_id": "T2", "acceptance_id": "AC-2"}],
         "journey_ids": [], "integration_check_ids": ["I-1"]}]
    tasks = {"tasks": [{"task_id": "T1", "acceptance_criteria": [{"id": "AC-1"}]},
                       {"task_id": "T2", "acceptance_criteria": [{"id": "AC-2", "mandatory": False}]}]}
    if fault == "missing_roster":
        from zf.runtime.product_acceptance import validate_product_acceptance_spec
        del spec["candidate_reader_assignments"]
        with pytest.raises(ValueError, match="integration checks lack"):
            validate_product_acceptance_spec(spec)
        return
    if fault == "missing_optional_ac":
        rows[1]["acceptance"] = []
    elif fault == "unassigned_check":
        rows[1]["integration_check_ids"] = []
    elif fault == "unknown_task":
        spec["integration_checks"][0]["task_ids"][1] = "other"
    if fault in {"unassigned_check", "unknown_task", "missing_optional_ac"}:
        with pytest.raises(ValueError, match="integration|coverage"):
            validate_reader_assignments(spec)
            validate_plan_assignment_coverage(spec, tasks)
        return
    validate_reader_assignments(spec)
    validate_plan_assignment_coverage(spec, tasks)
    monkeypatch.setattr(product, "_spec", lambda **_: spec)
    package, descriptor = product._package(tmp_path)
    aggregate = {"contract_revision": "rv", "candidate_event_id": "freeze", "plan_artifact_package_digest": package["sha256"],
        "verification_commands": [], "acceptance_criteria": [{"acceptance_id": candidate_acceptance_id(t, a),
        "source_task_id": t, "source_acceptance_id": a} for t, a in [("T1", "AC-1"), ("T2", "AC-2")]]}
    scope = scope_reader_contract(aggregate, spec, role="boundary", target=product.TARGET)
    assert scope["integration_check_ids"] == ["I-1"] and scope["task_ids"] == ["T1", "T2"]
    assert aggregate["acceptance_criteria"][-1]["text"] == spec["integration_checks"][0]["assertion"]
    result, op = _reader((spec, package, descriptor), "boundary", "freeze")
    op["candidate_reader_scope"] = scope
    result.payload["requirement_results"] = [{"acceptance_id": candidate_acceptance_id("T2", "AC-2"), "status": "passed", "evidence_refs": ["T2-proof"]}]
    if fault in {"success", "missing_integration_evidence"}:
        result.payload["requirement_results"].append({"acceptance_id": integration_acceptance_id("I-1"), "status": "passed",
                                                     "evidence_refs": ["integration-proof"] if fault == "success" else []})
    bound = bind_product_acceptance_report(result, state_dir=tmp_path, operation=op, source_event_id="vote")
    assert bool(bound.issues) == (fault != "success")
    if fault == "success":
        assert bound.payload["candidate_reader_scope"] == scope
