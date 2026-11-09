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
