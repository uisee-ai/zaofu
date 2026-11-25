"""Scoped OA replans preserve the authority that actually admitted the decision."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.test_orchestrator_agent_execution_review import _case
from tests.test_orchestrator_agent_plan_binding import _review, _new_package
from tests.test_orchestrator_agent_semantic_failure import _submit_rework
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
from zf.runtime.orchestrator_agent_semantic_failure import request_semantic_failure_checkpoint
from zf.runtime.plan_synth_handoff import build_plan_handoff_input_refs
from zf.runtime.replan_resynth import (
    append_replan_resynth_once,
    build_replan_resynth_event,
    plan_missing_replan_resynth_events,
)


def _replan(tmp_path):
    runtime, _ = _case(tmp_path)
    _new_package(runtime)
    prepared = request_semantic_failure_checkpoint(runtime, _review(runtime))
    outcome = apply_orchestrator_agent_decision(
        runtime, _submit_rework(runtime, prepared, action="return_to_plan"),
    )
    assert outcome["applied"], outcome
    events = runtime.event_log.read_all()
    marker = next(e for e in events if e.type == "orchestrator.replan_requested")
    trigger = next(e for e in events if e.type == "prd.plan.requested")
    return runtime, marker, trigger


def test_return_to_plan_reader_can_consume_retained_task_authority(tmp_path):
    runtime, marker, trigger = _replan(tmp_path)
    for key in ("task_map_generation", "plan_artifact_package_ref", "plan_artifact_package_digest"):
        assert trigger.payload[key] == marker.payload[key]
    refs = build_plan_handoff_input_refs(
        state_dir=runtime.state_dir, project_root=runtime.project_root, payload=trigger.payload,
    )
    assert "plan-rework-decision" in {r["source_id"] for r in refs}


def _preregister(runtime, trigger, **identity):
    from zf.runtime.call_result_runtime import prepare_call_operation

    refs = build_plan_handoff_input_refs(
        state_dir=runtime.state_dir, project_root=runtime.project_root, payload=trigger.payload,
    )
    return prepare_call_operation(
        runtime, payload={**trigger.payload, "role_instance": "planner", "attempt_domain": "plan",
            "input_refs": refs, "fanout_id": "fanout-scoped-replan", "child_id": "planner",
            "canonical_success_event": "prd.plan.child.completed",
            "canonical_failure_event": "prd.plan.child.failed", **identity},
        operation_type="fanout_reader_child", operation_key="scoped-planner",
        stage_id="prd-plan", task_id="", dispatch_id="scoped-planner-attempt",
    )


def test_retained_baseline_passes_actual_reader_operation_preregistration(tmp_path):
    runtime, _marker, trigger = _replan(tmp_path)
    prepared = _preregister(runtime, trigger)
    assert prepared.operation_id


@pytest.mark.parametrize("mutation", [{}, {"task_map_generation": "unadmitted"},
                                      {"plan_artifact_package_digest": "wrong"}])
@pytest.mark.parametrize("profile", ["workflow-read", "global-rescan"])
def test_discovery_reader_keeps_its_admitted_baseline(tmp_path, mutation, profile):
    from zf.runtime.artifact_read_ledger import ArtifactReadError

    runtime, _marker, trigger = _replan(tmp_path)
    identity = {"role_instance": "flow-discovery", "attempt_domain": "gap", "output_profile_id": profile,
                "canonical_success_event": "flow.discovery.child.completed",
                "canonical_failure_event": "flow.discovery.child.failed", **mutation}
    if mutation:
        with pytest.raises(ArtifactReadError):
            _preregister(runtime, trigger, **identity)
    else:
        assert _preregister(runtime, trigger, **identity).operation_id


@pytest.mark.parametrize("identity", [
    {"task_map_generation": "unadmitted"},
    {"plan_artifact_package_digest": "wrong-digest"},
])
def test_reader_preregistration_rejects_unproven_baseline(tmp_path, identity):
    from zf.runtime.artifact_read_ledger import ArtifactReadError

    runtime, _marker, trigger = _replan(tmp_path)
    with pytest.raises(ArtifactReadError):
        _preregister(runtime, trigger, **identity)


def test_authorized_stage_recovery_restores_exact_oa_marker_baseline(tmp_path):
    from zf.runtime.stage_failure_replan import plan_reader_stage_replan

    runtime, _marker, trigger = _replan(tmp_path)
    runtime.config.workflow.stages = [SimpleNamespace(
        id="prd-plan", flow_kind="prd", topology="fanout_reader", attempt_domain="plan",
        trigger=trigger.type, failure_event="", aggregate=SimpleNamespace(failure_event="prd.plan.failed"),
    )]
    runtime.event_writer.append(ZfEvent(type="fanout.started", payload={
        "workflow_run_id": "run-semantic-1", "stage_id": "prd-plan",
        "fanout_id": "failed-replan", "trigger_event_id": trigger.id,
    }))
    failure = runtime.event_writer.append(ZfEvent(type="prd.plan.failed", payload={
        **trigger.payload, "fanout_id": "failed-replan", "trigger_event_id": trigger.id,
        "failure_class": "harness_failure", "task_map_generation": "g2",
        "plan_artifact_package_id": "new-package", "plan_artifact_package_ref": "new-package.json",
        "plan_artifact_package_digest": "new-digest",
    }))
    recovered, reason = plan_reader_stage_replan(
        runtime.config, runtime.event_log.read_all(), failure,
        authorized_generation="owner-approved-recovery",
    )
    assert recovered is not None, reason
    assert recovered.payload["task_map_generation"] == "g1"
    assert _preregister(runtime, recovered).operation_id


def test_wrong_historical_binding_is_repaired_once_without_replaying_correct_trigger(tmp_path):
    runtime, marker, trigger = _replan(tmp_path)
    events = runtime.event_log.read_all()
    malformed = ZfEvent(type=trigger.type, payload={
        **trigger.payload, "task_map_generation": "g2",
        "plan_artifact_package_ref": "new-package.json", "plan_artifact_package_digest": "new-digest",
    })
    before = events[:next(i for i, e in enumerate(events) if e.id == marker.id) + 1]
    repairs = plan_missing_replan_resynth_events([*before, malformed], config=runtime.config)
    assert len(repairs) == 1
    assert repairs[0].payload["task_map_generation"] == "g1"
    writer = EventWriter(EventLog(tmp_path / "recovery" / "events.jsonl"))
    writer.append(malformed)
    first = append_replan_resynth_once(writer, tmp_path / "recovery", repairs[0])
    assert first.id != malformed.id
    assert append_replan_resynth_once(writer, tmp_path / "recovery", repairs[0]).id == first.id
    assert len(writer.event_log.read_all()) == 2
    assert plan_missing_replan_resynth_events([*before, malformed, first], config=runtime.config) == []


@pytest.mark.parametrize("mutation", [
    {"task_map_generation": "missing"}, {"plan_artifact_package_digest": "wrong"},
    {"plan_artifact_package_ref": "wrong.json"},
])
def test_oa_baseline_cannot_silently_rebind_unadmitted_claims(tmp_path, mutation):
    runtime, marker, _ = _replan(tmp_path)
    plan = SimpleNamespace(**{**marker.payload, **mutation,
        "trace_id": "run-semantic-1", "source_event_id": marker.payload["rework_of"],
        "source_event_type": marker.payload["rework_source"], "attempt": 1})
    with pytest.raises(ValueError, match="pinned admitted package"):
        build_replan_resynth_event(plan=plan, events=runtime.event_log.read_all(), config=runtime.config)


@pytest.mark.parametrize("explicit_previous", [False, True])
def test_candidate_diagnosis_uses_current_marker_inputs_for_planner(tmp_path, explicit_previous):
    from zf.runtime.artifact_read_requirements import required_reads_for_attempt
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar

    runtime, marker, issued = _replan(tmp_path)
    events = runtime.event_log.read_all()
    history = events[:events.index(marker)]
    old_trigger = ZfEvent(type=issued.type, correlation_id=marker.correlation_id, payload={
        "previous_plan_candidate_refs": [{"ref": "unrelated-draft.json", "sha256": "a" * 64}],
    })
    selected = []
    if explicit_previous:
        ref = write_immutable_json_sidecar(runtime.state_dir, {"clause": "selected", "other": "outside scope"},
            root="plan-reviews", kind="review_artifact", schema_version="review.v1", created_by="test")
        selected = [{**ref, "allowed_paths": ["$.clause"]}]
    body = {**marker.payload, "resume_scope": "candidate_diagnosis_only", "task_ids": []}
    if selected:
        body["previous_plan_candidate_refs"] = selected
    marker = replace(marker, payload=body)
    prefix = [*history, old_trigger, marker]
    recovered, = plan_missing_replan_resynth_events(prefix, config=runtime.config, state_dir=runtime.state_dir)
    direct = build_replan_resynth_event(plan=SimpleNamespace(**{
        **body, "trace_id": marker.correlation_id, "source_event_id": body["rework_of"],
        "source_event_type": body["rework_source"], "attempt": body["rework_attempt"],
    }), events=prefix, config=runtime.config, state_dir=runtime.state_dir)
    for trigger in (direct, recovered):
        assert trigger.payload.get("previous_plan_candidate_refs", []) == selected
        assert trigger.payload["resume_scope"] == "candidate_diagnosis_only"
        assert trigger.payload["plan_artifact_package_digest"] == body["plan_artifact_package_digest"]
        assert trigger.payload["orchestrator_decision_ref"] == body["orchestrator_decision_ref"]
    sources = build_plan_handoff_input_refs(state_dir=runtime.state_dir, project_root=runtime.project_root,
                                            payload=recovered.payload)
    assert {s["source_id"] for s in sources} >= {"plan-rework-package", "plan-rework-decision", "plan-rework-context"}
    prior = [s for s in sources if s["source_id"].startswith("previous-plan-candidate-")]
    assert [(s["sha256"], s["allowed_paths"]) for s in prior] == [(s["sha256"], s["allowed_paths"]) for s in selected]
    # Required sections must be selected by a durable kernel marker, not just
    # a previous-ref descriptor copied into an otherwise genuine trigger.
    if selected:
        with pytest.raises(ValueError, match="outside the published source scope"):
            _preregister(runtime, recovered)
    signed_marker = replace(marker, id=ZfEvent(type=marker.type).id)
    runtime.event_writer.append(signed_marker)
    if selected:
        wrong_path = [{**selected[0], "allowed_paths": ["$.other"]}]
        wrong_scope = [{**selected[0], "access_scope": {"purposes": ["workflow-read"]}}]
        for changed in (wrong_path, wrong_scope):
            altered = replace(recovered, payload={**recovered.payload, "previous_plan_candidate_refs": changed})
            with pytest.raises(ValueError, match="outside the published source scope"):
                _preregister(runtime, altered)
    prepared = _preregister(runtime, recovered)

    required = required_reads_for_attempt(runtime.event_log.read_all(), prepared.attempt_id,
                                         state_dir=runtime.state_dir, operation_id=prepared.operation_id)
    assert [(r["artifact_sha256"], r["json_path"]) for r in required
            if r["source_id"].startswith("previous-plan-candidate-")] == [(r["sha256"], "$.clause") for r in selected]
    retry = _preregister(runtime, recovered)
    assert retry.replay_hit and retry.request_hash == prepared.request_hash
    # A checkpoint sweep must not replace a trigger already dispatched by an older release.
    legacy = replace(recovered, payload={**recovered.payload, **old_trigger.payload})
    assert plan_missing_replan_resynth_events([*prefix, legacy], config=runtime.config, state_dir=runtime.state_dir) == []
