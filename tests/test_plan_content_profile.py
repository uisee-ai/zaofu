"""Content completeness is an explicit producer claim, not a Markdown heuristic."""

import json

import pytest

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.operator_inbox import build_operator_inbox
from zf.runtime.operator_plan_preview import build_plan_preview
from zf.runtime.plan_preview_artifacts import bind_plan_approval_preview
from tests.test_operator_plan_preview import _artifacts, _preview
from tests.test_writer_fanout_runtime import _approval_orch


@pytest.mark.parametrize("profile", [None, "", "derived_plan", "summary_only", "derived_plan_full"])
def test_approval_bundle_and_preview_preserve_declared_completeness(tmp_path, profile):
    state, full, refs = _artifacts(tmp_path)
    trigger = {**refs, "plan_content_profile": profile, "workflow_run_id": "run-1"}
    payload = bind_plan_approval_preview(
        state, trigger, tmp_path, {**refs, "trace_id": "run-1"},
    )
    expected = profile or "derived_plan"
    assert payload["plan_content_profile"] == expected
    bundle = json.loads((state / payload["plan_preview_bundle_ref"]).read_text())
    assert bundle["content_profile"] == expected
    result = _preview(state, payload)
    assert result["content_profile"] == expected
    assert result["markdown"] == full
    assert result["status"] == "pending"


@pytest.mark.parametrize("profile", [None, "", "summary_only", "derived_plan_full"])
def test_legacy_markdown_does_not_infer_full_semantic_coverage(tmp_path, profile):
    state, full, payload = _artifacts(tmp_path)
    if profile is not None:
        payload["plan_content_profile"] = profile
    result = _preview(state, payload)
    assert result["markdown"] == full
    assert result["content_kind"] == "full_plan"
    assert result["content_profile"] == (profile or "derived_plan")
    assert result["actions"]["approve"] == "plan-approve"


@pytest.mark.parametrize("profile", [None, "summary_only", "derived_plan_full"])
def test_runtime_approval_and_inbox_do_not_promote_missing_profile(tmp_path, profile):
    state, log, orch = _approval_orch(tmp_path, enabled=True)
    trigger = ZfEvent(
        type="task_map.ready", actor="plan-critic", correlation_id="trace-1",
        payload={"pdd_id": "F-11111111", **(
            {"plan_content_profile": profile} if profile else {}
        )},
    )
    EventWriter(log).append(trigger)
    orch.run_once(events=[trigger])
    events = log.read_all()
    request = next(e for e in events if e.type == "plan.approval.requested")
    assert request.payload["plan_content_profile"] == (profile or "derived_plan")
    assert not any(e.type in {"plan.approved", "fanout.started"} for e in events)
    preview = build_plan_preview(
        state, events, plan_id=request.payload["plan_id"], project_root=tmp_path,
    )
    assert preview["content_profile"] == (profile or "derived_plan")
    inbox = build_operator_inbox(state, events, project_root=tmp_path)
    item = next(i for i in inbox["items"] if i.get("plan_id") == request.payload["plan_id"])
    assert item["preview"]["available"] is True
    assert item["preview"]["api_path"] == f"/plans/{request.payload['plan_id']}/preview"


@pytest.mark.parametrize("profile", [None, "summary_only", "derived_plan_full"])
def test_synthesized_plan_keeps_declared_profile_at_task_map_handoff(tmp_path, profile):
    from tests.test_reader_fanout_runtime import _task_map_synth_orchestrator
    _, log, orch, fanout_id = _task_map_synth_orchestrator(tmp_path)
    event = orch.event_writer.append(ZfEvent(
        type="fanout.synth.completed", actor="task-map-synth", correlation_id="trace-product",
        payload={
            "fanout_id": fanout_id, "stage_id": "product-task-map", "role_instance": "task-map-synth",
            "run_id": f"run-{fanout_id}-synth", "status": "completed", "recommendation": "approve",
            **({"plan_content_profile": profile} if profile else {}),
            "report": {"status": "passed", "recommendation": "approve", "summary": "Reviewed plan",
                       "findings": [], "task_map_ref": ".zf/artifacts/PDD-1/task_map.json",
                       "source_index_ref": ".zf/artifacts/PDD-1/source_index.json"},
        },
    ))
    orch.run_once(events=[event])
    ready = next(e for e in log.read_all() if e.type == "task_map.ready")
    assert ready.payload.get("plan_content_profile") == profile


@pytest.mark.parametrize("profile", [None, "summary_only", "derived_plan_full"])
def test_direct_planner_aggregate_preserves_profile_without_inheriting_upstream(tmp_path, profile):
    from tests.test_reader_fanout_runtime import _task_map_synth_orchestrator
    _, _, orch, _ = _task_map_synth_orchestrator(tmp_path)
    manifest = {"children": [], "trigger_payload": {"plan_content_profile": "derived_plan_full"}}
    report = {"plan_artifact_ref": "docs/plan.md", **({"plan_content_profile": profile} if profile else {})}
    payload = orch._generic_fanout_success_payload(manifest=manifest,
        success_event="task_map.ready", extra_payloads=[report])
    assert payload.get("plan_content_profile") == profile


def _reviewed_author(tmp_path, author_profile="derived_plan_full", critic_profile="derived_plan"):
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    from zf.runtime.plan_output_lineage import reviewed_plan_generation

    state, full, refs = _artifacts(tmp_path)

    def put(body):
        return write_immutable_json_sidecar(state, body, root="profile-test", kind="profile-test",
            schema_version="profile-test.v1", created_by="test", source_event_id="")

    author = put({"child_id": "planner", "plan_artifact_ref": refs["plan_artifact_ref"],
                  **({"plan_content_profile": author_profile} if author_profile else {})})
    author.update(source_id="child-result-planner", artifact_id="planner.json")
    identity = dict(workflow_run_id="run-1", fanout_id="fanout-1", stage_id="prd-plan", plan_revision="plan-1")
    contract = put({"schema_version": "plan-synth-contract.v1", **identity,
        "child_bindings": [{"child_id": "planner", **{k: author[k] for k in ("source_id", "artifact_id", "sha256")}}],
        "source_bindings": [author, {"source_id": "child-artifact-planner-1", "artifact_id": "plan.md",
            "ref": refs["plan_artifact_ref"], "sha256": refs["plan_artifact_digest"]}]})
    control = put({"plan_content_profile": critic_profile})
    review = ZfEvent(type="fanout.synth.completed", actor="plan-critic", payload={**identity,
        "plan_synth_contract_ref": contract["ref"], "plan_synth_contract_digest": contract["sha256"],
        "reviewed_plan_candidate_digest": contract["sha256"], "control_result_ref": control,
        "plan_content_profile": critic_profile})
    ready = ZfEvent(id="evt-plan", type="task_map.ready", actor="zf-cli", causation_id=review.id,
        payload={**refs, **identity, "plan_revision": reviewed_plan_generation(review),
                 "plan_content_profile": critic_profile})
    payload = bind_plan_approval_preview(state, ready.payload, tmp_path, {**refs, "trace_id": "run-1"})
    return state, full, refs, author, review, ready, payload


@pytest.mark.parametrize("author_profile,critic_profile,expected", [
    ("derived_plan_full", "derived_plan", "derived_plan_full"),
    (None, "derived_plan", "derived_plan"),
    ("summary_only", "derived_plan", "summary_only"),
    ("derived_plan_full", "summary_only", "summary_only"),
])
def test_preview_recovers_only_bound_author_claim(tmp_path, author_profile, critic_profile, expected):
    state, full, _, _, review, ready, payload = _reviewed_author(tmp_path, author_profile, critic_profile)
    before = {p: p.read_bytes() for p in state.rglob("*") if p.is_file()}
    result = _preview(state, payload, review, ready)
    assert result["content_profile"] == expected
    assert result["markdown"] == full
    assert {p: p.read_bytes() for p in state.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("fault", ["review_digest", "run", "generation", "author", "plan", "causation"])
def test_preview_never_promotes_unbound_or_tampered_author_claim(tmp_path, fault):
    state, _, refs, author, review, ready, payload = _reviewed_author(tmp_path)
    if fault == "review_digest":
        review.payload["reviewed_plan_candidate_digest"] = "0" * 64
    elif fault == "run":
        review.payload["workflow_run_id"] = "other-run"
    elif fault == "generation":
        ready.payload["plan_revision"] = "other-generation"
    elif fault == "author":
        (state / author["ref"]).write_text("{}")
    elif fault == "plan":
        (state / refs["plan_artifact_ref"]).write_text("# Different Plan")
    else:
        ready.causation_id = "unrelated"
    assert _preview(state, payload, review, ready)["content_profile"] == "derived_plan"


def test_generic_critic_scope_does_not_replace_author_claim_at_package_admission(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from zf.runtime import plan_artifact_package_runtime as package_runtime

    state, _, refs, _, review, _, _ = _reviewed_author(tmp_path)
    captured = []
    monkeypatch.setattr(package_runtime, "flow_metadata_for", lambda *a, **kw: {})
    monkeypatch.setattr("zf.runtime.plan_local_review_output.validate_local_review_output", lambda *a, **kw: {})
    monkeypatch.setattr(package_runtime, "_admit", lambda *a, **kw: captured.append(kw["payload"]) or {})
    status, _, result = package_runtime.admit_synthesized_plan_package(
        SimpleNamespace(state_dir=state, config=object()), event=review, manifest={}, stage_id="prd-plan",
        trace_id="run-1", success_event="task_map.ready", final_status="completed", recommendation="approve",
        artifact_payload={**refs, "plan_content_profile": "derived_plan"})
    assert status == "completed"
    assert result["plan_content_profile"] == captured[0]["plan_content_profile"] == "derived_plan_full"


def test_author_profile_projection_accepts_serialized_events(tmp_path):
    from dataclasses import asdict

    state, _, _, _, review, ready, payload = _reviewed_author(tmp_path)
    request = ZfEvent(type="plan.approval.requested", actor="zf-cli", payload=payload)
    result = build_plan_preview(state, [asdict(e) for e in [review, ready, request]],
        plan_id="evt-plan", project_root=tmp_path)
    assert result["content_profile"] == "derived_plan_full"
