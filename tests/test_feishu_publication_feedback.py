"""Feishu preview -> authorization -> GitHub publication using a mock provider."""
import json
from datetime import datetime, timedelta, timezone

from tests.test_self_issue_service import _dual_provider_draft, _dual_provider_service
from zf.cli.feishu import _handle_event_data
from zf.core.config.project_context import ProjectContext
from zf.core.config.schema import FeishuIdentityConfig, FeishuIdentityUserConfig, FeishuRouteConfig, ZfConfig
from zf.integrations.feishu.gateway import FeishuCommandEnvelope
from zf.integrations.feishu.issue_adapter import handle_issue_command
from zf.integrations.feishu.publication_feedback import github_session_binding, read_publication_feedback
from zf.integrations.feishu.self_issue_cards import push_self_issue_cards_once
from zf.integrations.feishu.transport import MockFeishuTransport


def test_preview_authorization_and_publish_from_same_feishu_card(tmp_path, monkeypatch):
    service, _, _, github = _dual_provider_service(tmp_path)
    draft = _dual_provider_draft(service)
    saved_draft = service.drafts.get(draft["draft_id"])
    saved_draft.analysis["portable_session_id"] = "issue-session-distinct-from-draft"
    service.drafts.save(saved_draft)
    service.policy.default_publication_mode = "github"
    config = ZfConfig(self_issue=service.policy)
    config.integrations.feishu_identity = FeishuIdentityConfig(
        enabled=True, require_signed_actions=True,
        users={"owner": FeishuIdentityUserConfig("owner", "approver")},
    )
    config.integrations.feishu_routing = {"chat": FeishuRouteConfig()}
    context = ProjectContext(tmp_path, tmp_path / "zf.yaml", config, service.state_dir)
    monkeypatch.setenv("ZF_FEISHU_ACTION_TOKEN_SECRET", "test-secret")
    monkeypatch.setattr("zf.runtime.control_actions_self_issue.SelfIssueService", lambda *a, **k: service)
    transport = MockFeishuTransport()

    def push():
        result = push_self_issue_cards_once(service.state_dir, transport, receive_id="chat", action_secret=b"test-secret")
        assert not result["failed"]
        key = "self-issue-draft-" + draft["draft_id"]
        mid = result["ledger"][key]["message_id"]
        content = next((content for id_, content in reversed(transport.updated_messages) if id_ == mid), None)
        return json.loads(content) if content else next(json.loads(m.content) for m in transport.sent_messages if json.loads(m.content)["_card_key"] == key)

    def click(card, command):
        value = next(button["value"] for row in card["elements"] for button in row.get("actions", [])
                     if button.get("value", {}).get("action", "").startswith(command + ":"))
        return _handle_event_data({"type": "button_action", "payload": {
            "action": value["action"], "action_token": value["t"], "message_id": "same-card",
        }, "user_id": "owner", "chat_id": "chat"}, context=context, user_levels={})

    assert click(push(), "self-issue-preview")["status"] == "previewed"
    preview_card = push()
    text = json.dumps(preview_card, ensure_ascii=False)
    assert "目标仓库：owner/repo" in text and "发布预览" in text
    assert not github.requests
    assert click(preview_card, "self-issue-confirm")["status"] == "confirmed"
    assert click(push(), "self-issue-publish")["status"] == "authorization_required"
    assert "需要 GitHub 授权" in json.dumps(push(), ensure_ascii=False)
    started = click(push(), "self-issue-github-device-start")
    assert started["status"] == "authorization_required" and started["user_code"]
    authorization_card = push()
    text = json.dumps(authorization_card, ensure_ascii=False)
    assert "ABCD-EFGH" in text and "https://github.com/login/device" in text
    assert "device-code-secret" not in text and "access_token" not in text

    other = handle_issue_command(FeishuCommandEnvelope(
        command="self-issue-github-device-poll", args=[draft["draft_id"], started["transaction_id"]],
        chat_id="chat", user_id="another-approver",
    ), state_dir=service.state_dir, config=config)
    assert not other["ok"] and not github.requests

    real_poll = service.github_oauth.poll
    monkeypatch.setattr(service.github_oauth, "poll", lambda **kw: {"status": "authorization_pending"})
    assert click(push(), "self-issue-github-device-poll")["status"] == "authorization_pending"
    # Deterministically advance the fixture's poll time, without real sleeps.
    rows = service._load_github_device_transactions()
    rows[-1]["next_poll_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    service._write_github_device_transactions(rows)
    monkeypatch.setattr(service.github_oauth, "poll", real_poll)
    ready_card = push()
    assert click(ready_card, "self-issue-github-device-poll")["status"] == "published"
    assert len(github.requests) == 1
    assert click(ready_card, "self-issue-github-device-poll")["status"] == "duplicate"
    assert "https://github.com/owner/repo/issues/23" in json.dumps(push())
    feedback = read_publication_feedback(service.state_dir, "chat", draft["draft_id"])
    assert feedback["status"] == "published" and "source_event_id" in feedback
    assert read_publication_feedback(service.state_dir, "other-chat", draft["draft_id"]) == {}


def test_github_authorization_binding_uses_chat_user_and_draft():
    envelope = FeishuCommandEnvelope(command="self-issue-github-device-start", chat_id="chat", user_id="owner")
    binding = github_session_binding(envelope, "draft")
    assert binding != github_session_binding(envelope, "other-draft")
    envelope.user_id = "other"
    assert binding != github_session_binding(envelope, "draft")


def test_expired_confirmation_can_generate_new_preview(tmp_path):
    service, _, _, _ = _dual_provider_service(tmp_path)
    draft = _dual_provider_draft(service)
    payload = {"draft_id": draft["draft_id"], "publication_mode": "github"}
    first = service.preview(payload)
    confirmed = service.confirm({"batch_id": first["batch_id"], "payload_digest": first["payload_digest"]})
    assert confirmed["status"] == "confirmed"
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    batch = service.batches.get(first["batch_id"])
    batch.confirmation_expires_at = past
    service.batches.save(batch)
    for intent_id in batch.intent_ids.values():
        intent = service.intents.get(intent_id)
        intent.confirmation_expires_at = past
        service.intents.save(intent)
    second = service.preview(payload)
    assert second["status"] == "previewed" and second["batch_id"] != first["batch_id"]
    assert service.confirm({"batch_id": second["batch_id"], "payload_digest": second["payload_digest"]})["ok"]
