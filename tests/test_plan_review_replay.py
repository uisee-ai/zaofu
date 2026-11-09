"""Plan Critic requests keep their first usage snapshot across deferred dispatch."""
import pytest

from tests.test_call_result_runtime import _runtime
from zf.runtime.call_result_runtime import prepare_call_operation
from zf.runtime.plan_execution_review import attach_execution_review, review_execution
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import WorkflowOperationError


def _prepare(runtime, *, usage=55, tasks=0, key="critic", usage_details=False):
    payload = {"workflow_run_id": "run-plan", "fanout_id": "fanout-plan",
               "role_instance": "critic", "output_profile_id": "plan-synth",
               "canonical_success_event": "fanout.synth.completed",
               "canonical_failure_event": "fanout.synth.completed"}
    review = review_execution({"tasks": []}, ports={}, policy={}, metadata={})
    review["usage_observations"] = {"unattributed_project_samples": usage}
    if usage_details:
        review["usage_observations"].update({"schema_version": "execution-usage-facts.v1",
            "scope": "run", "available": True, "groups": [{"operation_id": "op", "input_tokens": usage}]})
    review["graph"]["task_count"] = tasks
    attach_execution_review(state_dir=runtime.state_dir, payload=payload,
                            review=review, source_event_id="evt-trigger")
    prepared = prepare_call_operation(runtime, payload=payload, operation_type="fanout_synth",
                                      operation_key=key, stage_id="plan", task_id="",
                                      dispatch_id="synth-attempt")
    return payload, prepared


def test_deferred_synth_replay_keeps_first_usage_snapshot(tmp_path):
    runtime = _runtime(tmp_path)
    first_payload, first = _prepare(runtime)
    second_payload, second = _prepare(runtime, usage=56)
    assert first.request_hash == second.request_hash
    assert second.replay_hit
    source = next(r for r in second_payload["input_refs"] if r["source_id"] == "plan-execution-review")
    assert hydrate_sidecar_ref(runtime.state_dir, source).payload["usage_observations"]["unattributed_project_samples"] == 55
    assert first_payload["attempt_source_manifest_digest"] == second_payload["attempt_source_manifest_digest"]


def test_changed_candidate_still_rejects_same_operation(tmp_path):
    runtime = _runtime(tmp_path)
    _prepare(runtime)
    with pytest.raises(WorkflowOperationError, match="diverged"):
        _prepare(runtime, usage=56, tasks=1)


def test_new_operation_observes_current_usage(tmp_path):
    runtime = _runtime(tmp_path)
    _prepare(runtime)
    payload, _ = _prepare(runtime, usage=56, key="critic-new-generation")
    source = next(r for r in payload["input_refs"] if r["source_id"] == "plan-execution-review")
    assert hydrate_sidecar_ref(runtime.state_dir, source).payload["usage_observations"]["unattributed_project_samples"] == 56


def test_replay_rejects_tampered_pinned_review(tmp_path):
    runtime = _runtime(tmp_path)
    payload, _ = _prepare(runtime)
    source = next(r for r in payload["input_refs"] if r["source_id"] == "plan-execution-review")
    (runtime.state_dir / source["ref"]).write_text('{"tampered":true}')
    with pytest.raises(ValueError, match="mismatch"):
        _prepare(runtime, usage=56)


def test_usage_details_are_on_demand_and_pinned_for_replay(tmp_path):
    from zf.runtime.artifact_read_requirements import build_canonical_required_reads

    runtime = _runtime(tmp_path)
    first_payload, first = _prepare(runtime, usage=55, usage_details=True)
    sources = {row["source_id"]: row for row in first_payload["input_refs"]}
    review = hydrate_sidecar_ref(runtime.state_dir, sources["plan-execution-review"]).payload
    assert "groups" not in review["usage_observations"]
    assert review["usage_observations"]["group_count"] == 1
    assert hydrate_sidecar_ref(runtime.state_dir, sources["plan-execution-usage"]).payload["groups"][0]["input_tokens"] == 55
    required = build_canonical_required_reads({"sources": list(sources.values())}, output_profile_id="plan-synth")
    assert "plan-execution-review" in {item["source_id"] for item in required}
    assert "plan-execution-usage" not in {item["source_id"] for item in required}

    second_payload, second = _prepare(runtime, usage=56, usage_details=True)
    assert first.request_hash == second.request_hash
    second_sources = {row["source_id"]: row for row in second_payload["input_refs"]}
    assert second_sources["plan-execution-usage"]["sha256"] == sources["plan-execution-usage"]["sha256"]
    assert hydrate_sidecar_ref(runtime.state_dir, second_sources["plan-execution-usage"]).payload["groups"][0]["input_tokens"] == 55


def test_replay_rejects_tampered_pinned_usage_details(tmp_path):
    runtime = _runtime(tmp_path)
    payload, _ = _prepare(runtime, usage_details=True)
    source = next(r for r in payload["input_refs"] if r["source_id"] == "plan-execution-usage")
    (runtime.state_dir / source["ref"]).write_text('{"tampered":true}')
    with pytest.raises(ValueError, match="mismatch"):
        _prepare(runtime, usage=56, usage_details=True)


def test_replay_keeps_legacy_inlined_usage(tmp_path):
    runtime = _runtime(tmp_path)
    _, first = _prepare(runtime, usage=55)
    payload, second = _prepare(runtime, usage=56, usage_details=True)
    assert first.request_hash == second.request_hash
    assert not any(r["source_id"] == "plan-execution-usage" for r in payload["input_refs"])
    review = next(r for r in payload["input_refs"] if r["source_id"] == "plan-execution-review")
    assert hydrate_sidecar_ref(runtime.state_dir, review).payload["usage_observations"]["unattributed_project_samples"] == 55
