"""New Plan read contracts may select the feedback and leave proven duplicate descriptors on demand."""
import copy
import hashlib
import json

import pytest

from tests.test_call_result_runtime import _runtime
from zf.core.events.model import ZfEvent
from zf.runtime.artifact_read_ledger import build_attempt_source_manifest, read_attempt_artifact
from zf.runtime.artifact_read_snapshot import pin_read_selections
from zf.runtime.artifact_source_versions import pin_source_versions
from zf.runtime.call_result_runtime import prepare_call_operation

CONTEXT = "plan-rework-context"
FIELDS = ["$.schema_version", "$.rework_of", "$.rework_attempt", "$.rework_feedback"]


def scenario(tmp_path, count, fault=""):
    runtime = _runtime(tmp_path)
    state = runtime.state_dir

    def artifact(name, value, **kwargs):
        ref = f"artifacts/{name}.json"
        path = state / ref
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(json.dumps(value, ensure_ascii=False))
        return {"ref": ref, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "source_id": name, "kind": "fanout_child_artifact", "artifact_id": path.name,
                "allowed_paths": ["$"], **kwargs}

    history = build_attempt_source_manifest(workflow_run_id="run", task_id="plan", attempt_id="first",
        dispatch_id="first", sources=[artifact(f"prior-{i}", {"manual": f"梨园排练的证据材料 {i}"})
                                     for i in range(count)], metadata={"source_event_id": "first-handoff"})["sources"]
    pin_source_versions(state, history)
    pin_read_selections(state, {"sources": history}, [{"source_id": s["source_id"],
        "artifact_id": s["artifact_id"], "artifact_sha256": s["sha256"], "json_path": "$"} for s in history])
    if fault == "unknown_descriptor_key":
        history[0]["decision"] = "requires Owner approval"
    failure = ZfEvent(type="prd.plan.failed", actor="zf-cli", origin="worker" if fault=="origin" else "kernel",
        payload={"workflow_run_id": "another" if fault == "run" else "run", "status": "failed",
                 "previous_plan_candidate_refs": copy.deepcopy(history)})
    runtime.event_log.append(failure)
    aliases = [{**s, "source_id": f"previous-plan-candidate-{i}", "source_event_id": "",
                "occurrence_id": "", "schema_version": ""} for i, s in enumerate(history, 1)]
    if fault == "missing_alias":
        aliases.pop()
    if fault == "scope":
        aliases[0]["access_scope"] = {"actors": ["other"]}
    if fault == "path":
        aliases[0]["allowed_paths"] = ["$.manual"]
    if fault == "occurrence":
        aliases[0]["occurrence_id"] = "attempt-occurrence:foreign"
    if fault == "current_event":
        aliases[0]["source_event_id"] = "another-trigger"
    if fault == "artifact":
        aliases[0]["artifact_id"] = "other.json"
    if fault == "historical_identity":
        history[0]["source_event_id"] = "different-past"
    if fault == "selection":
        history[0]["read_selections"][0]["selected_bytes"] += 1
    if fault == "recovery":
        history[0].pop("recovery_ref")
    feedback = [{"finding": "下一场排练须具备听证", "source": "证据乙"}]
    body = {"schema_version": "plan-rework-context.v1", "rework_of": failure.id, "rework_attempt": 2,
            "rework_feedback": feedback, "previous_plan_candidate_refs": history}
    if fault == "future":
        body["new_approval"] = {"required": True}
    if fault == "missing":
        body.pop("rework_feedback")
    refs = [artifact(CONTEXT, body, kind="plan_rework_context"), *aliases,
            artifact("plan-rejected-review", {"verdict": "rejected", "finding": "保留审阅历史"})]
    return runtime, refs, body


def prepare(runtime, refs, attempt, extra=None):
    payload = {"workflow_run_id": "run", "role_instance": "critic", "output_profile_id": "plan-synth",
               "input_refs": copy.deepcopy(refs), "canonical_success_event": "fanout.synth.completed",
               "canonical_failure_event": "fanout.synth.completed", **(extra or {})}
    result = prepare_call_operation(runtime,payload=payload,operation_type="fanout_synth",operation_key=attempt,
                                    stage_id="plan",task_id="",dispatch_id=attempt)
    return result, payload


def context_paths(payload):
    return [r["json_path"] for r in payload["required_reads"] if r["source_id"] == CONTEXT]


@pytest.mark.parametrize("count", [1, 4, 17])
def test_new_review_consumes_all_feedback_keeps_original_and_all_versions(tmp_path, count):
    runtime, refs, body = scenario(tmp_path, count)
    result, payload = prepare(runtime, refs, "new")
    assert context_paths(payload) == FIELDS
    assert payload["plan_review_optimization"]["after"]["distinct_selected_bytes"] < payload["plan_review_optimization"]["before"]["distinct_selected_bytes"]
    manifest = json.loads((runtime.state_dir / payload["attempt_source_manifest_ref"]).read_text())
    source = next(s for s in manifest["sources"] if s["source_id"] == CONTEXT)
    def read(source, path):
        return read_attempt_artifact(runtime.state_dir, manifest=manifest, source_id=source["source_id"],
            artifact_id=source["artifact_id"], artifact_sha256=source["sha256"], json_path=path, record=False)["content"]
    assert json.loads(read(source,"$")) == body
    assert json.loads(read(source,"$.rework_feedback")) == body["rework_feedback"]
    assert len([s for s in manifest["sources"] if s["source_id"].startswith("previous-plan-candidate-")]) == count
    assert all(json.loads(read(s, "$"))["manual"] for s in manifest["sources"] if s["source_id"].startswith("previous-plan-candidate-"))
    assert json.loads(read(next(s for s in manifest["sources"] if s["source_id"] == "plan-rejected-review"), "$"))["verdict"] == "rejected"
    for replay_input in ({}, {"required_reads": payload["required_reads"]}):
        replay, projected = prepare(runtime, refs, "new", replay_input)
        assert (replay.request_hash, projected["attempt_source_manifest_digest"], projected["input_consumption_policy_digest"]) == (
            result.request_hash, payload["attempt_source_manifest_digest"], payload["input_consumption_policy_digest"])
        assert context_paths(projected) == FIELDS


@pytest.mark.parametrize("fault", ["future","missing","origin","run","missing_alias","scope","path","artifact",
                                   "historical_identity","selection","recovery","unknown_descriptor_key","occurrence","current_event"])
def test_unknown_unbound_or_restricted_context_retains_root(tmp_path, fault):
    runtime, refs, _ = scenario(tmp_path, 2, fault)
    if fault in {"path","scope"}:
        # A profile may decline an invalid candidate grant before registration.
        from zf.runtime.plan_input_read_selection import select_plan_input_reads
        from zf.runtime.artifact_read_requirements import build_canonical_required_reads
        manifest = {"sources": refs, "workflow_run_id": "run", "attempt_id": "acl", "dispatch_id": "acl", "task_id": "plan"}
        required = build_canonical_required_reads(manifest,output_profile_id="plan-synth")
        scoped,_ = select_plan_input_reads(runtime.state_dir, manifest, required, policy={},
            lookup_failure=lambda key: runtime.event_log.read_all()[0])
        assert context_paths({"required_reads":scoped}) == ["$"]
        denied = refs[1]
        with pytest.raises(ValueError, match="allowed|authorized"):
            read_attempt_artifact(runtime.state_dir, manifest=manifest, source_id=denied["source_id"],
                artifact_id=denied["artifact_id"], artifact_sha256=denied["sha256"],
                json_path="$", actor="critic", role="critic", record=False)
    else:
        assert context_paths(prepare(runtime,refs,"fresh")[1]) == ["$"]

def test_preupgrade_root_policy_replays_and_explicit_root_stays_authoritative(tmp_path, monkeypatch):
    from zf.runtime import plan_rework_input_overlap
    runtime, refs, _ = scenario(tmp_path, 3)
    with monkeypatch.context() as old:
        old.setattr(plan_rework_input_overlap, "rework_context_paths", lambda *args: ())
        issued, pinned = prepare(runtime, refs, "legacy")
    assert context_paths(pinned) == ["$"]
    before = (runtime.state_dir / pinned["input_consumption_policy_ref"]["ref"]).read_bytes()
    for extra in ({}, {"required_reads": pinned["required_reads"]}):
        replay, body = prepare(runtime, refs, "legacy", extra)
        assert context_paths(body) == ["$"] and replay.request_hash == issued.request_hash
        assert body["attempt_source_manifest_digest"] == pinned["attempt_source_manifest_digest"]
        assert body["input_consumption_policy_digest"] == pinned["input_consumption_policy_digest"]
        assert (runtime.state_dir / body["input_consumption_policy_ref"]["ref"]).read_bytes() == before
    explicit = [r for r in pinned["required_reads"] if r["source_id"] == CONTEXT]
    assert context_paths(prepare(runtime, refs, "owner", {"required_reads": explicit})[1]) == ["$"]
    assert context_paths(prepare(runtime, refs, "fresh")[1]) == FIELDS
