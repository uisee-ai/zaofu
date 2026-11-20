"""Candidate input selection remains producer-declared and globally accountable."""
import pytest
from zf.runtime.task_scoped_plan_inputs import task_input_paths
from zf.runtime.artifact_read_requirements import build_canonical_required_reads


def candidate_body(count=2):
    return {
        "shared": {"revoked_access": "deny"},
        "acceptance": [{"id": f"AC-{i}", "task_id": f"T{i}", "expected": "retains error behavior"}
                       for i in range(count)],
        "implementation": [{"task_id": f"T{i}", "steps": "implementation detail " * 500}
                           for i in range(count)],
        "task_input_policy": {
            "schema_version": "task-input-policy.v1",
            "shared_paths": ["$.shared"],
            "candidate_paths": ["$.acceptance"],
            "tasks": [{"task_id": f"T{i}", "paths": [f"$.acceptance[{i}]", f"$.implementation[{i}]"]}
                      for i in range(count)],
        },
    }


def test_candidate_keeps_shared_and_full_acceptance_without_impl_instructions():
    from zf.runtime.artifact_read_ledger import _select_json_path
    body = candidate_body(16)
    paths = task_input_paths(body, task_id="", candidate=True)
    assert paths == ["$.shared", "$.acceptance"]
    assert _select_json_path(body, paths[1]) == body["acceptance"]
    source = {"source_id": "plan-port-execution_spec", "artifact_id": "execution_spec",
              "kind": "candidate_scoped_plan_input", "sha256": "a" * 64, "allowed_paths": paths}
    reads = build_canonical_required_reads({"sources": [source]}, output_profile_id="candidate-verify")
    assert [row["json_path"] for row in reads] == paths
    assert all(not row["allow_truncated"] for row in reads)
    assert task_input_paths(body, task_id="T0") == ["$.shared", "$.acceptance[0]", "$.implementation[0]"]


@pytest.mark.parametrize("paths", [[], ["$.missing"], ["$.task_input_policy"], ["$.acceptance", "$.acceptance"]])
def test_invalid_candidate_selection_is_rejected(paths):
    body = candidate_body()
    body["task_input_policy"]["candidate_paths"] = paths
    with pytest.raises(ValueError):
        task_input_paths(body, task_id="", candidate=True)


def test_candidate_policy_never_replaces_global_contract_or_changes_legacy():
    body = candidate_body()
    del body["task_input_policy"]["candidate_paths"]
    assert task_input_paths(body, task_id="", candidate=True) == ["$"]
    sources = [
        {"source_id": "contract", "artifact_id": "contract", "sha256": "a" * 64, "allowed_paths": ["$"]},
        {"source_id": "plan-port-scope", "artifact_id": "scope", "sha256": "b" * 64,
         "kind": "candidate_scoped_plan_input", "allowed_paths": ["$.shared"]},
    ]
    reads = build_canonical_required_reads({"sources": sources}, output_profile_id="candidate-verify")
    assert any(row["source_id"] == "contract" and row["json_path"] == "$" for row in reads)


def test_pinned_package_materializes_candidate_scope_and_rejects_outside_read(tmp_path):
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    from zf.runtime.plan_artifact_package import build_plan_artifact_package, write_plan_artifact_package
    from zf.runtime.run_contract import stable_json_sha256, write_run_contract_snapshot
    from zf.runtime.task_scoped_plan_inputs import current_plan_port_sources
    from zf.runtime.artifact_read_ledger import ArtifactReadError, read_attempt_artifact

    state = tmp_path / "state"
    port = write_immutable_json_sidecar(state, candidate_body(), root="fixtures", kind="execution_spec",
                                      schema_version="v1", created_by="test")
    body = {"schema_version": "run-contract.v1", "workflow": {"kind": "prd"}}
    contract = write_run_contract_snapshot(state, {**body, "contract_digest": stable_json_sha256(body)})
    package = build_plan_artifact_package(workflow_run_id="run", flow_kind="prd", producer_stage_id="plan",
        run_contract=contract, plan_revision="r1", task_map_generation="g1",
        produced=[{**port, "logical_name": "execution_spec", "artifact_kind": "execution_spec",
                   "producer_stage_id": "plan"}], required_ports=["execution_spec"])
    ref = write_plan_artifact_package(state, package)
    sources = current_plan_port_sources(state_dir=state, workflow_run_id="run", task_id="",
        allow_without_task=True, candidate=True, profile="candidate-verify",
        currentness={"task_map_generation": "g1", "plan_artifact_package_ref": ref["ref"],
                     "plan_artifact_package_digest": ref["sha256"]})
    assert sources[0]["kind"] == "candidate_scoped_plan_input"
    assert sources[0]["ref"] == port["ref"] and sources[0]["sha256"] == port["sha256"]
    manifest = {"schema_version": "attempt-source-manifest.v1", "attempt_id": "candidate",
                "workflow_run_id": "run", "dispatch_id": "dispatch", "sources": sources}
    reads = build_canonical_required_reads(manifest, output_profile_id="candidate-verify")
    assert [row["json_path"] for row in reads] == ["$.shared", "$.acceptance"]
    with pytest.raises(ArtifactReadError, match="not allowed"):
        read_attempt_artifact(state, manifest=manifest, source_id=sources[0]["source_id"],
                              artifact_id="execution_spec", json_path="$.implementation")
