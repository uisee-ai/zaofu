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


def test_digest_only_is_not_an_editable_full_plan(review):
    client, service, _, body = review
    service.writer.append(ZfEvent(type="plan.approval.requested", payload={
        "plan_id": "P-digest", "digest_ref": "artifacts/plan.md",
    }))
    preview = client.get("/api/projects/default/plans/P-digest/preview").json()
    assert preview["content_kind"] == "digest_only"
    assert preview["markdown"] == body
    assert not any(a["name"] in {"accepted_plan", "plan_digest"} for a in preview["artifacts"])
    response = client.post("/api/projects/default/plan-artifact-review",
        headers={"x-zf-web-token": "test-token"},
        json={"operation": "context", "plan_id": "P-digest", "name": "plan_digest"})
    assert response.status_code == 422


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


def test_chat_can_discuss_then_propose_only_the_quoted_change(review, monkeypatch):
    client, service, artifact, body = review
    quote = "玩家😀可以移动。"
    payload = {"operation": "discuss", "mode": "chat", "plan_id": "P1", "name": "accepted_plan",
               "message": "这里需要什么边界？", "quote": {"text": quote, "start": body.index(quote), "end": body.index(quote) + len(quote)}}
    agent = Agent({"answer": "可以约定禁止越过边界。"})
    monkeypatch.setattr("zf.web.headless_agent.KanbanHeadlessAgent", lambda **kwargs: agent)
    headers = {"x-zf-web-token": "test-token"}
    url = "/api/projects/default/plan-artifact-review"
    discussion = client.post(url, headers=headers, json=payload)
    assert discussion.status_code == 200, discussion.text
    assert discussion.json()["draft_ref"] is None
    assert agent.calls[0]["permission_profile"] == "read_only"
    agent.reply = {"answer": "仅修改引用片段。", "replacement": "玩家😀可以移动，不能越过边界。"}
    revised = client.post(url, headers=headers, json={**payload, "message": "加入不能越过边界"})
    assert revised.status_code == 200, revised.text
    candidate = hydrate_sidecar_ref(service.state_dir, revised.json()["draft_ref"]).payload
    assert candidate["body"] == body.replace(quote, agent.reply["replacement"])
    assert artifact.read_text() == body
    assert not any(e.type in {"plan.approved", "plan.rejected"} for e in service.writer.event_log.read_all())


@pytest.mark.parametrize("approved_during_turn", [False, True])
def test_chat_cannot_propose_changes_after_plan_approval(review, approved_during_turn):
    _, service, _, body = review
    def approve():
        service.writer.append(ZfEvent(type="plan.approved", payload={"plan_id": "P1"}))
    if not approved_during_turn:
        approve()
    agent = Agent({"answer": "不应接受", "replacement": "不能写入"}, callback=approve if approved_during_turn else None)
    with pytest.raises(PreviewReviewError, match="该计划已进入执行"):
        service.discuss({"plan_id": "P1", "name": "accepted_plan", "mode": "chat", "message": "修改这段",
                        "quote": {"text": body, "start": 0, "end": len(body)}}, agent=agent)
    assert not list((service.state_dir / "artifacts/workflow/plan-reviews").glob("*.json"))
