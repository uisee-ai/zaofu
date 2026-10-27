"""Running YAML can be discussed without reopening configuration mutation."""

import pytest

from tests.test_workflow_preview_review import Agent, revision, setup  # noqa: F401
from tests.test_web_workflow_proposals import _write_config
from zf.runtime.workflow_preview_review import PreviewReviewError
from zf.runtime.workflow_requests import load_workflow_request, mark_workflow_request


def start(service, binding):
    for status in ("approved", "submitted", "running"):
        mark_workflow_request(service.state_dir, binding["request_id"], status=status,
                              run_id="snapshot-test-run", actor="test", writer=service.writer)


def test_running_snapshot_chat_api_uses_frozen_yaml_not_live_config(setup, monkeypatch):
    client, service, binding = setup
    start(service, binding)
    before = load_workflow_request(service.state_dir, binding["request_id"])
    _write_config(service.config_path, lanes=9)
    baseline = service.config_path.read_bytes()
    agent = Agent({"answer": "这是运行快照中的并行配置。", "changes": []})
    monkeypatch.setattr("zf.web.headless_agent.KanbanHeadlessAgent.run_turn", lambda self, **kw: agent.run_turn(**kw))
    endpoint = "/api/projects/default/workflow-preview-review"
    headers = {"x-zf-web-token": "test-token"}
    assert client.post(endpoint, json={**binding, "operation": "context"}).status_code == 403
    context = client.post(endpoint, headers=headers, json={**binding, "operation": "context"})
    assert context.status_code == 200, context.text
    assert context.json()["read_only"]
    assert not any(field["editable"] for field in context.json()["fields"].values())
    response = client.post(endpoint, headers=headers,
                           json={**binding, "operation": "discuss", "mode": "chat", "message": "解释并行配置"})
    assert response.status_code == 200, response.text
    assert response.json()["changes"] == [] and response.json()["draft_ref"] is None
    contract = agent.calls[0]["context"]["workflow_preview_review"]
    assert contract["read_only"] and "kind: IssueFlow" in contract["yaml"]
    assert "lanes: 1" in contract["yaml"] and "lanes: 9" not in contract["yaml"]
    assert agent.calls[0]["permission_profile"] == "read_only"
    assert service.config_path.read_bytes() == baseline
    assert load_workflow_request(service.state_dir, binding["request_id"]) == before
    assert not any(event.type == "workflow.invoke.requested" for event in service.writer.event_log.read_all())


def test_running_snapshot_rejects_provider_patches_and_pre_start_drafts(setup):
    _, service, binding = setup
    draft = revision(service, binding)["draft_ref"]
    start(service, binding)
    before = load_workflow_request(service.state_dir, binding["request_id"])
    agent = Agent({"answer": "不应接受的修改", "changes": [{"field": "yaml", "replacement": "modified: true"}]})
    with pytest.raises(PreviewReviewError) as error:
        service.discuss({**binding, "mode": "chat", "message": "修改配置"}, agent=agent)
    assert error.value.code == "snapshot_read_only"
    with pytest.raises(PreviewReviewError, match="unstarted"):
        service.apply(draft)
    assert load_workflow_request(service.state_dir, binding["request_id"]) == before
    with pytest.raises(PreviewReviewError) as error:
        service.discuss({**binding, "mode": "revise", "message": "修改配置"}, agent=agent)
    assert error.value.code == "snapshot_read_only"
    assert len(agent.calls) == 1


@pytest.mark.parametrize("fault", ["stale_binding", "corrupt_snapshot", "missing_snapshot"])
def test_invalid_running_snapshot_never_reaches_provider(setup, fault):
    client, service, binding = setup
    context = service.context(binding)
    start(service, binding)
    if fault == "stale_binding":
        binding = {**binding, "revision": binding["revision"] + 1}
    else:
        path = service.state_dir / context["proposal"]["effective_config_ref"]["ref"]
        if fault == "corrupt_snapshot":
            path.write_text("{}\n")
        else:
            path.unlink()
    response = client.post("/api/projects/default/workflow-preview-review",
                           headers={"x-zf-web-token": "test-token"},
                           json={**binding, "operation": "context"})
    assert response.status_code in {409, 422}, response.text
    assert not any(event.type == "user.message" for event in service.writer.event_log.read_all())


def test_start_during_preview_discussion_still_rejects_late_patch(setup):
    _, service, binding = setup
    agent = Agent({"answer": "late", "changes": []}, lambda: start(service, binding))
    with pytest.raises(PreviewReviewError, match="unstarted"):
        service.discuss({**binding, "mode": "chat", "message": "解释"}, agent=agent)
