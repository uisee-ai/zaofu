"""Standard named Plan refs have the same pinned identity as explicit ports."""
import hashlib
import json

import pytest

from tests.test_plan_input_read_selection import fixture, read_rows
from zf.runtime.artifact_read_requirements import build_canonical_required_reads
from zf.runtime.plan_input_read_selection import select_plan_input_reads


def reference_fixture(state, name="test_matrix", fault=""):
    manifest, _, report, supplement = fixture(state)
    schema = name.replace("_", "-") + ".v1"
    body = {"schema_version": schema, "cases": [{"failure": "preserve old bytes"}] * 30}
    if fault == "unknown_schema": body["schema_version"] = "future.v2"
    if fault == "no_schema": body.pop("schema_version")
    path = state / supplement["ref"]
    path.write_text(json.dumps(body))
    supplement["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    source = json.loads((state / report["ref"]).read_text())
    source.pop("plan_ports")
    field = "regression_test_matrix_ref" if fault == "alias" else name + "_ref"
    source[field] = supplement["ref"]
    if fault == "core": source["task_map_ref"] = supplement["ref"]
    if fault == "two_kinds": source["acceptance_matrix_ref"] = supplement["ref"]
    if fault == "alias_conflict": source["regression_test_matrix_ref"] = "artifacts/other.json"
    if fault == "version_conflict":
        manifest["sources"].append({**supplement, "source_id": "child-artifact-other", "sha256": "bad"})
    if fault in {"wrong_port_digest", "other_port_ref", "inline_conflict", "malformed_port_name", "malformed_port_ref"}:
        source["plan_ports"] = [{"logical_name": name, "ref": supplement["ref"],
                                 "sha256": supplement["sha256"]}]
        if fault == "wrong_port_digest": source["plan_ports"][0]["sha256"] = "bad"
        if fault == "other_port_ref": source["plan_ports"][0]["ref"] = "artifacts/other.json"
        if fault == "inline_conflict": source["plan_ports"] = [{"logical_name": name, "body": {"other": True}}]
        if fault == "malformed_port_name": source["plan_ports"][0]["logical_name"] = []
        if fault == "malformed_port_ref": source["plan_ports"][0]["ref"] = []
    if fault == "digest": path.write_text("{}")
    if fault == "replan":
        context_path = state / "artifacts/replan.json"
        context_path.write_text(json.dumps({"schema_version": "plan-rework-context.v1", "rework_of": "old"}))
        manifest["sources"].append({"source_id": "plan-rework-context", "artifact_id": "replan.json",
            "ref": "artifacts/replan.json", "sha256": hashlib.sha256(context_path.read_bytes()).hexdigest(),
            "allowed_paths": ["$"]})
    (state / report["ref"]).write_text(json.dumps(source))
    report["sha256"] = hashlib.sha256((state / report["ref"]).read_bytes()).hexdigest()
    if fault == "two_authors":
        manifest["sources"].append({**report, "source_id": "child-result-other"})
    required = build_canonical_required_reads(manifest, output_profile_id="plan-synth")
    return manifest, required, supplement


@pytest.mark.parametrize("name", ["source_index", "source_inventory", "capability_matrix",
    "acceptance_matrix", "test_matrix", "real_e2e_matrix", "coverage_ledger"])
def test_standard_reference_is_optional_but_original_is_available(tmp_path, name):
    manifest, required, source = reference_fixture(tmp_path, name)
    selected, strategy = select_plan_input_reads(tmp_path, manifest, required,
        policy={"mode": "targeted", "focus": ["verification ownership"]})
    assert strategy == "targeted_navigation"
    assert not any(row["source_id"] == source["source_id"] for row in selected)
    original = [row for row in required if row["source_id"] == source["source_id"]]
    assert "preserve old bytes" in read_rows(tmp_path, manifest, original)[0]["content"]


@pytest.mark.parametrize("fault", ["alias", "replan", "unknown_schema", "no_schema", "core", "two_kinds", "alias_conflict", "version_conflict",
    "wrong_port_digest", "other_port_ref", "inline_conflict", "malformed_port_name", "malformed_port_ref", "digest", "two_authors", "full", "explicit"])
def test_reference_conflict_unknown_authority_or_digest_keeps_full_read(tmp_path, fault):
    manifest, required, source = reference_fixture(tmp_path, fault=fault)
    selected, strategy = select_plan_input_reads(tmp_path, manifest, required,
        policy={"mode": "full" if fault == "full" else "targeted", "focus": ["failure"]},
        explicit=fault == "explicit")
    assert any(row["source_id"] == source["source_id"] for row in selected) == (fault not in {"alias", "replan"})
    if fault == "replan": assert strategy == "targeted_replan_navigation"


@pytest.mark.parametrize("field", ["product_spec_ref", "prd_ref", "objective_ref", "issue_spec_ref",
    "issue_ref", "planning_result_ref", "accepted_plan_ref"])
def test_authoritative_aliases_are_not_demoted_by_supplementary_ref(tmp_path, field):
    manifest, _, source = reference_fixture(tmp_path)
    report = next(s for s in manifest["sources"] if s["source_id"] == "child-result-planner")
    path = tmp_path / report["ref"]
    body = json.loads(path.read_text())
    body[field] = source["ref"]
    path.write_text(json.dumps(body))
    report["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    required = build_canonical_required_reads(manifest, output_profile_id="plan-synth")
    selected, _ = select_plan_input_reads(tmp_path, manifest, required,
        policy={"mode": "targeted", "focus": ["failure"]})
    assert any(row["source_id"] == source["source_id"] for row in selected)


def test_named_reference_navigation_is_wired_to_actual_dispatch(tmp_path):
    from tests.test_call_result_runtime import _runtime
    from zf.runtime.call_result_runtime import prepare_call_operation
    runtime = _runtime(tmp_path)
    manifest, _, source = reference_fixture(runtime.state_dir, fault="replan")
    runtime.config.workflow.flow_metadata["plan_review"] = {"mode": "targeted", "focus": ["failure"], "reason": "Owner"}
    payload = {"workflow_run_id": "run", "role_instance": "critic", "output_profile_id": "plan-synth",
               "input_refs": manifest["sources"], "canonical_success_event": "fanout.synth.completed",
               "canonical_failure_event": "fanout.synth.completed"}
    prepared = prepare_call_operation(runtime, payload=payload, operation_type="fanout_synth",
        operation_key="named-ref-replan", stage_id="plan", task_id="", dispatch_id="named-replan")
    assert prepared.should_dispatch
    assert payload["plan_review_optimization"]["strategy"] == "targeted_replan_navigation"
    assert not any(row["source_id"] == source["source_id"] for row in payload["required_reads"])
    assert not payload["plan_review_optimization"]["inherited_approval"]
