from types import SimpleNamespace

from zf.core.config.schema import FeishuIssueMergeConfig, IntegrationsConfig
from zf.integrations.feishu.issue_connection import issue_scope_rejection


def _config() -> SimpleNamespace:
    issue = FeishuIssueMergeConfig(
        enabled=True,
        app_id="cli_issue",
        bot_open_id="ou_bot",
        chat_id="oc_issue",
        operator_open_id="ou_operator",
    )
    config = SimpleNamespace(
        _feishu_issue_only=True,
        integrations=IntegrationsConfig(feishu_issue_merge=issue),
    )
    return config


def _event(**overrides) -> SimpleNamespace:
    values = {
        "chat_id": "oc_issue",
        "user_id": "ou_operator",
        "payload": {"app_id": "cli_issue", "bot_open_id": "ou_bot"},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_issue_only_scope_accepts_issue_commands() -> None:
    assert issue_scope_rejection(_config(), _event(), "self-issue-new") is None


def test_issue_only_scope_rejects_non_issue_commands() -> None:
    result = issue_scope_rejection(_config(), _event(), "status")
    assert result == {
        "ok": False,
        "status": "unsupported_in_issue_merge_mode",
        "message": "当前接入只支持 Issue 流程，请发送 /issue 描述",
    }


def test_issue_only_scope_rejects_wrong_chat_operator_or_app() -> None:
    for event in (
        _event(chat_id="oc_other"),
        _event(user_id="ou_other"),
        _event(payload={"app_id": "cli_other", "bot_open_id": "ou_bot"}),
        _event(payload={"app_id": "cli_issue", "bot_open_id": "ou_other"}),
    ):
        result = issue_scope_rejection(_config(), event, "self-issue-new")
        assert result and result["status"] == "rejected"
