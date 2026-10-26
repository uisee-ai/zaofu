import hashlib
from pathlib import Path

import pytest

from zf.core.events.model import ZfEvent
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.plan_artifact_review import PlanArtifactReview
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_preview_review import PreviewReviewError
from tests.test_workflow_preview_review import Agent
from tests.test_web_workflow_proposals import _client


@pytest.fixture
def review(tmp_path, monkeypatch):
    client, state = _client(tmp_path, monkeypatch)
    body = "# PRD\n\n开头保持。\n玩家😀可以移动。\n结尾保持。\n"
    artifact = state / "artifacts" / "plan.md"
    artifact.parent.mkdir(exist_ok=True)
    artifact.write_text(body)
    writer = EventWriter(EventLog(state / "events.jsonl"))
    writer.append(ZfEvent(type="plan.approval.requested", payload={
        "plan_id": "P1", "plan_artifact_ref": "artifacts/plan.md",
        "plan_artifact_digest": hashlib.sha256(body.encode()).hexdigest()}))
    return client, PlanArtifactReview(state, tmp_path, writer), artifact, body


def draft(service, body, **overrides):
    text = "玩家😀可以移动。"
    return service.discuss({"plan_id": "P1", "name": "accepted_plan", "message": "增加边界", "mode": "revise",
        "quote": {"text": text, "start": body.index(text), "end": body.index(text) + len(text)}, **overrides},
        agent=Agent({"answer": "补充移动边界", "replacement": "玩家😀可以移动，不能越过边界。"}))


def test_local_document_revision_preserves_original_and_routes_replan(review):
    _, service, artifact, body = review
    result = draft(service, body)
    assert artifact.read_text() == body
    candidate = hydrate_sidecar_ref(service.state_dir, result["draft_ref"]).payload
    assert candidate["body"] == body.replace("玩家😀可以移动。", "玩家😀可以移动，不能越过边界。")
    applied = service.apply(result["draft_ref"])
    assert applied["ok"], applied
    assert service.apply(result["draft_ref"])["replayed"]
    assert artifact.read_text() == body
    events = service.writer.event_log.read_all()
    rejected = [e for e in events if e.type == "plan.rejected"]
    assert len(rejected) == 1
    assert result["draft_ref"]["ref"] in rejected[0].payload["reason"]
    assert not any(e.type == "plan.approved" for e in events)


def test_stale_plan_approval_and_invalid_quote_cannot_apply(review):
    _, service, artifact, body = review
    with pytest.raises(PreviewReviewError):
        draft(service, body, quote={"text": "foreign", "start": 0, "end": 7})
    result = draft(service, body)
    service.writer.append(ZfEvent(type="plan.approved", payload={"plan_id": "P1"}))
    with pytest.raises(PreviewReviewError, match="计划已被处理"):
        service.apply(result["draft_ref"])
    assert artifact.read_text() == body


def test_review_api_is_token_gated_and_uses_server_bound_artifacts(review):
    client, _, _, _ = review
    url = "/api/projects/default/plan-artifact-review"
    assert client.post(url, json={"operation": "context", "plan_id": "P1"}).status_code == 403
    headers = {"x-zf-web-token": "test-token"}
    response = client.post(url, headers=headers, json={"operation": "context", "plan_id": "P1"})
    assert response.status_code == 200, response.text
    assert response.json()["editable"]
    response = client.post(url, headers=headers, json={"operation": "context", "plan_id": "P1", "name": "/etc/passwd"})
    assert response.status_code == 422
