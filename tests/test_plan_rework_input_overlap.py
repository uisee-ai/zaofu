import json
import pytest

from zf.runtime.artifact_read_requirements import build_canonical_required_reads
from zf.runtime.plan_synth_handoff import build_plan_handoff_input_refs
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def test_rework_retains_distinct_metadata_and_all_meaning_without_repeated_ports(tmp_path):
    state = tmp_path / "state"
    ports = [{"logical_name": "test_matrix", "content": {"cases": ["negative assertion"] * 100}}]
    bodies = [{"plan_ports": ports, "status": "prepared", "unique": {"owner": "planner"}},
              {"plan_ports": ports, "status": "completed", "unique": {"reason": "approved envelope"}}]
    refs = [write_immutable_json_sidecar(state, body, root="test", kind="plan",
                                       schema_version="v1", created_by="test") for body in bodies]
    sources = build_plan_handoff_input_refs(state_dir=state, project_root=tmp_path,
        payload={"previous_plan_candidate_refs": refs}, source_event_id="rework")
    candidates = [s for s in sources if s["source_id"].startswith("previous-plan-candidate-")]
    assert candidates[0]["allowed_paths"] == ["$"]
    assert candidates[1]["allowed_paths"] == ["$.status", "$.unique"]
    alias = candidates[1]["access_scope"]["read_equivalents"][0]
    assert alias["source_id"] == candidates[0]["source_id"]
    assert alias["source_sha256"] == refs[0]["sha256"]
    assert alias["source_json_path"] == "$.plan_ports"
    assert [hydrate_sidecar_ref(state, s).payload for s in candidates] == bodies
    context = hydrate_sidecar_ref(state, next(s for s in sources if s["source_id"] == "plan-rework-context")).payload
    assert context["read_equivalents"][0]["read_equivalents"] == [alias]
    assert context["previous_plan_candidate_refs"] == refs
    from zf.runtime.artifact_read_ledger import build_attempt_source_manifest
    manifest = build_attempt_source_manifest(workflow_run_id="run", task_id="plan", attempt_id="rework",
        dispatch_id="dispatch", sources=sources)
    assert manifest["sources"][-1]["access_scope"]["read_equivalents"] == candidates[1]["access_scope"]["read_equivalents"]
    reads = build_canonical_required_reads(manifest, output_profile_id="plan-synth")
    assert [(r["source_id"], r["json_path"]) for r in reads if r["source_id"].startswith("previous-")] == [
        (candidates[0]["source_id"], "$"), (candidates[1]["source_id"], "$.status"),
        (candidates[1]["source_id"], "$.unique")]
    # Editing a distinctive field cannot be hidden by a partial-body match.
    assert json.dumps(bodies[1]["unique"]) in json.dumps(hydrate_sidecar_ref(state, candidates[1]).payload)


def test_partial_grants_and_nonidentical_bodies_keep_their_scope(tmp_path):
    from zf.runtime.plan_rework_input_overlap import partition_repeated_candidate_bodies
    sources = []
    for i in range(3):
        ref = write_immutable_json_sidecar(tmp_path, {"plan_ports": [{"id": i}], "status": "ok"},
            root="test", kind="plan", schema_version="v1", created_by="test")
        sources.append({**ref, "source_id": f"previous-plan-candidate-{i}",
                        "allowed_paths": ["$.plan_ports"] if i == 0 else ["$"]})
    assert partition_repeated_candidate_bodies(tmp_path, sources) == sources


def test_producer_cannot_assert_runtime_overlap_without_equivalence_proof(tmp_path):
    with pytest.raises(ValueError, match="cannot declare a runtime overlap partition"):
        build_plan_handoff_input_refs(state_dir=tmp_path, project_root=tmp_path,
            payload={"previous_plan_candidate_refs": [{"kind": "plan_rework_candidate_partition",
                "ref": "artifacts/forged.json", "sha256": "a" * 64, "allowed_paths": ["$.status"]}]},
            source_event_id="rework")
