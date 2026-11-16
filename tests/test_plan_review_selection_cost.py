from copy import deepcopy
import hashlib
import json

import pytest

from zf.runtime.plan_review_selection_cost import choose_review_reads


def _source(tmp_path, name, body):
    raw = json.dumps(body).encode()
    name = "artifacts/" + name
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    (tmp_path / name).write_bytes(raw)
    return {"source_id": name, "artifact_id": name, "ref": name,
            "sha256": hashlib.sha256(raw).hexdigest(), "allowed_paths": ["$"]}


def _row(source, path="$", **extra):
    return {"source_id": source["source_id"], "artifact_id": source["artifact_id"],
            "artifact_sha256": source["sha256"], "json_path": path, **extra}


@pytest.mark.parametrize("unrelated,mode", [(10000, "incremental"), (0, "full")])
def test_complete_old_and_new_partition_cost_selects_only_a_real_saving(tmp_path, unrelated, mode):
    old = _source(tmp_path, "old.json", {"shared": "common", "tasks": ["old" * 40, "x" * unrelated]})
    new = _source(tmp_path, "new.json", {"shared": "common", "tasks": ["new" * 40, "x" * unrelated]})
    authority = _source(tmp_path, "prd.json", {"acceptance": "mandatory"})
    manifest = {"sources": [old, new, authority]}
    before = [_row(new), _row(authority)]
    candidate = [_row(authority), *[_row(s, p) for s in [old, new] for p in ["$.shared", "$.tasks[0]"]]]
    frozen = deepcopy(manifest)
    chosen, diagnostic = choose_review_reads(tmp_path, manifest, before=before, candidate=candidate, explicit=False)
    assert diagnostic["mode"] == mode
    assert diagnostic["eligibility"] == "verified"
    assert diagnostic["candidate"]["unknown_rows"] == 0
    assert chosen == (candidate if mode == "incremental" else before)
    assert manifest == frozen
    if mode == "full":
        assert diagnostic["reason"] == "no_selection_byte_saving"
        assert diagnostic["after"] == diagnostic["before"]


@pytest.mark.parametrize("fault", ["missing", "tampered", "denied", "duplicate", "selector", "wrong_digest", "partial"])
def test_unknown_partition_cost_conservatively_keeps_original_policy(tmp_path, fault):
    source = _source(tmp_path, "port.json", {"shared": "common", "tasks": ["small", "x" * 10000]})
    before = [_row(source)]
    candidate = [_row(source, "$.tasks[0]")]
    manifest = {"sources": [source]}
    if fault == "missing": (tmp_path / "artifacts/port.json").unlink()
    if fault == "tampered": (tmp_path / "artifacts/port.json").write_text("{}")
    if fault == "denied": source["allowed_paths"] = ["$.shared"]
    if fault == "duplicate": manifest["sources"].append(dict(source))
    if fault == "selector": candidate[0]["json_path"] = "$.missing"
    if fault == "wrong_digest": candidate[0]["artifact_sha256"] = "0" * 64
    if fault == "partial": candidate[0]["max_chars"] = 1
    chosen, diagnostic = choose_review_reads(tmp_path, manifest, before=before, candidate=candidate, explicit=False)
    assert chosen == before
    assert diagnostic["mode"] == "full"
    assert diagnostic["reason"] == "selection_size_unproven"


def test_actual_dispatch_chooses_full_for_qualified_but_more_expensive_scope(tmp_path):
    from tests.test_call_result_runtime import _runtime
    from tests.test_plan_local_review import _policy_fixture
    from zf.runtime.call_result_runtime import prepare_call_operation
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    from zf.runtime.artifact_read_ledger import canonical_required_reads

    runtime = _runtime(tmp_path)
    manifest, _, events = _policy_fixture(runtime.state_dir, unrelated_bytes=0)
    for event in events:
        runtime.event_writer.append(event)
    contract = next(s for s in manifest["sources"] if s["source_id"] == "plan-synth-contract")
    payload = {"workflow_run_id": "run-1", "fanout_id": "new-fanout", "stage_id": "plan",
        "role_instance": "critic", "output_profile_id": "plan-synth", "input_refs": manifest["sources"],
        "canonical_success_event": "fanout.synth.completed", "canonical_failure_event": "fanout.synth.completed",
        "plan_revision": "new-rev", "plan_synth_contract_ref": contract["ref"],
        "plan_synth_contract_digest": contract["sha256"], "reviewed_plan_candidate_digest": contract["sha256"]}
    kwargs = dict(operation_type="fanout_synth", operation_key="critic", stage_id="plan",
                  task_id="", dispatch_id="new-attempt", causation_id="trigger")
    prepared = prepare_call_operation(runtime, payload=payload, **kwargs)
    assert prepared.should_dispatch
    diagnostic = payload["plan_review_optimization"]
    assert diagnostic["mode"] == "full" and diagnostic["eligibility"] == "verified"
    assert diagnostic["reason"] == "no_selection_byte_saving"
    bound = hydrate_sidecar_ref(runtime.state_dir, payload["attempt_source_manifest"]).payload
    assert payload["required_reads"] == canonical_required_reads(bound, output_profile_id="plan-synth")
    replay = prepare_call_operation(runtime, payload=deepcopy(payload), **kwargs)
    assert replay.request_hash == prepared.request_hash
