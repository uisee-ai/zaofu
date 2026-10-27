"""Headless DSL chat proposes bounded changes; only owner apply advances Preview."""

import pytest

from tests.test_workflow_preview_review import Agent, setup  # noqa: F401
from zf.runtime.workflow_preview_review import PreviewReviewError
from zf.runtime.workflow_requests import load_workflow_request
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def chat(service, binding, changes, **kwargs):
    return service.discuss({**binding, "mode": "chat", "message": "调整当前配置", **kwargs},
                           agent=Agent({"answer": "请检查 Diff", "changes": changes}))


def test_chat_can_ask_without_selecting_a_field_or_creating_a_draft(setup):
    _, service, binding = setup
    before = load_workflow_request(service.state_dir, binding["request_id"])
    agent = Agent({"answer": "请指定要使用的模型名称。", "changes": []})
    result = service.discuss({**binding, "mode": "chat", "message": "换个模型"}, agent=agent)
    assert result["changes"] == [] and result["draft_ref"] is None
    assert load_workflow_request(service.state_dir, binding["request_id"]) == before
    call = agent.calls[0]
    assert call["permission_profile"] == "read_only"
    assert call["context"]["workflow_preview_review"]["profile"] == "workflow-dsl-editor.v1"
    assert "kind: IssueFlow" in call["context"]["workflow_preview_review"]["yaml"]
    assert "workflow-dsl-editor.v1" in call["message"]
    event = [event for event in service.writer.event_log.read_all() if event.type == "user.message"][-1]
    assert "yaml" not in event.payload["review"]
    assert hydrate_sidecar_ref(service.state_dir, event.payload["review"]["effective_config_ref"]).payload["yaml"] == call["context"]["workflow_preview_review"]["yaml"]


def test_multiple_config_changes_compile_once_and_never_start_or_apply_config(setup):
    client, service, binding = setup
    original = service.config_path.read_text()
    patches = [{"field": "config.0.lanes", "replacement": 2},
               {"field": "config.0.roleDefaults.model", "replacement": "test-model"}]
    result = chat(service, binding, patches)
    assert [item["field"] for item in result["changes"]] == [item["field"] for item in patches]
    assert load_workflow_request(service.state_dir, binding["request_id"])["revision"] == binding["revision"]
    applied = service.apply(result["draft_ref"])
    assert applied["revision"] == binding["revision"] + 1 and not applied["started"]
    proposal = hydrate_sidecar_ref(service.state_dir, applied["proposal_ref"]).payload
    snapshot = hydrate_sidecar_ref(service.state_dir, proposal["effective_config_ref"]).payload
    assert "lanes: 2" in snapshot["yaml"] and "model: test-model" in snapshot["yaml"]
    assert service.config_path.read_text() == original
    assert service.apply(result["draft_ref"])["replayed"]


@pytest.mark.parametrize("patches", [
    [{"field": "config.0.project.state_dir", "replacement": "/tmp/escape"}],
    [{"field": "execution_plan", "replacement": []}],
    [{"field": "config.0.lanes", "replacement": 2}, {"field": "config.0.lanes", "replacement": 3}],
    [{"field": "config.0.lanes", "replacement": True}],
    [{"field": "config.0.lanes", "replacement": 2}, {"field": "requirement.objective", "replacement": "Different objective"}],
    [{"field": "config.0.lanes", "replacement": 2, "execute": True}],
    "rewrite the whole YAML",
])
def test_chat_rejects_unbounded_readonly_mixed_or_invalid_changes(setup, patches):
    _, service, binding = setup
    before = load_workflow_request(service.state_dir, binding["request_id"])
    with pytest.raises(PreviewReviewError):
        chat(service, binding, patches)
    assert load_workflow_request(service.state_dir, binding["request_id"]) == before


def test_chat_quote_stays_narrow_and_cannot_change_another_field(setup):
    _, service, binding = setup
    quote = {"text": "Active sessions", "start": 0, "end": 15}
    result = chat(service, binding, [{"field": "requirement.acceptance.0", "replacement": "Active and empty sessions"}],
                  field="requirement.acceptance.0", quote=quote)
    assert result["changes"][0]["after"] == "Active and empty sessions remain valid."
    with pytest.raises(PreviewReviewError, match="Quoted edits"):
        chat(service, binding, [{"field": "config.0.lanes", "replacement": 2}],
             field="requirement.acceptance.0", quote=quote)


def test_chat_rechecks_version_after_provider_reply(setup):
    _, service, binding = setup
    first = chat(service, binding, [{"field": "config.0.lanes", "replacement": 2}])
    agent = Agent({"answer": "模型修改", "changes": [{"field": "config.0.roleDefaults.model", "replacement": "test-model"}]},
                  callback=lambda: service.apply(first["draft_ref"]))
    with pytest.raises(PreviewReviewError):
        service.discuss({**binding, "mode": "chat", "message": "换模型"}, agent=agent)


def test_api_chat_needs_no_field_and_uses_real_review_service(setup, monkeypatch):
    client, service, binding = setup
    agent = Agent({"answer": "两条 lane", "changes": [{"field": "config.0.lanes", "replacement": 2}]})
    monkeypatch.setattr("zf.web.headless_agent.KanbanHeadlessAgent.run_turn", lambda self, **kwargs: agent.run_turn(**kwargs))
    response = client.post("/api/projects/default/workflow-preview-review", headers={"x-zf-web-token": "test-token"},
                           json={**binding, "operation": "discuss", "mode": "chat", "message": "把 lanes 改成 2"})
    assert response.status_code == 200, response.text
    assert response.json()["changes"][0]["after"] == 2
