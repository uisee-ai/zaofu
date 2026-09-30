"""Feishu callback -> controlled action -> Portable Session integration."""
from dataclasses import replace
import json
import re

import pytest

from issue_core import JsonIssueSessionStore, SessionStatus
from zf.cli.feishu import _handle_event_data
from zf.core.config.project_context import ProjectContext
from zf.core.config.schema import (
    FeishuIdentityConfig, FeishuIdentityUserConfig, FeishuRouteConfig,
    SelfIssueConfig, ZfConfig,
)
from zf.core.events import EventLog
from zf.integrations.feishu.gateway import CommandGateway, FeishuCommandEnvelope
from zf.integrations.feishu.issue_adapter import handle_issue_command
from zf.integrations.feishu.self_issue_cards import (
    _report_parts, build_self_issue_session_card, push_self_issue_cards_once,
)
from zf.integrations.feishu.transport import MockFeishuTransport
from zf.runtime.self_issue_session_host import SelfIssueSessionHost


@pytest.fixture
def context(tmp_path, monkeypatch):
    config = ZfConfig(self_issue=SelfIssueConfig(enabled=True))
    config.integrations.feishu_identity = FeishuIdentityConfig(
        enabled=True, require_signed_actions=True,
        users={"owner": FeishuIdentityUserConfig("owner", "approver")},
    )
    config.integrations.feishu_routing = {"chat": FeishuRouteConfig()}
    monkeypatch.setenv("ZF_FEISHU_ACTION_TOKEN_SECRET", "test-secret")
    return ProjectContext(tmp_path, tmp_path / "zf.yaml", config, tmp_path / "custom-state")


def _message(text, *, message_id="m1", chat_id="chat", user_id="owner"):
    return {"type": "message", "payload": {"text": text, "message_id": message_id},
            "user_id": user_id, "chat_id": chat_id}


def _button(value, *, message_id="b1"):
    return {"type": "button_action", "payload": {
        "action": value["action"], "action_token": value["t"], "message_id": message_id,
    }, "user_id": "owner", "chat_id": "chat"}


def _actions(card):
    return [button["value"] for row in card["elements"] if row.get("tag") == "action"
            for button in row["actions"] if "value" in button]


def _waiting(context):
    store = JsonIssueSessionStore(context.state_dir / "self-issues/portable-sessions")
    initial = store.create("上传后崩溃")
    session = store.save_session(replace(initial, status=SessionStatus.WAITING_FOR_USER,
        revision=initial.revision + 1, user_question="哪个版本？"), expected_revision=initial.revision)
    transport = MockFeishuTransport()
    push_self_issue_cards_once(context.state_dir, transport, receive_id="chat", action_secret=b"test-secret")
    card = json.loads(transport.sent_messages[0].content)
    content = next(row["text"]["content"] for row in card["elements"]
                   if "self-issue-session-answer" in row.get("text", {}).get("content", ""))
    command = next(line for line in content.splitlines() if line.startswith("/zf ")).replace("你的补充回答", "版本 1.2")
    return store, session, command


@pytest.mark.parametrize("status,action", [
    (SessionStatus.CREATED, "pause"), (SessionStatus.INVESTIGATING, "pause"),
    (SessionStatus.PAUSED, "retry"), (SessionStatus.FAILED, "retry"),
    (SessionStatus.READY_FOR_CONFIRMATION, "confirm"),
    (SessionStatus.COMPLETED, "materialize"), (SessionStatus.CANCELLED, None),
])
def test_session_cards_offer_valid_lifecycle_controls(tmp_path, status, action):
    store = JsonIssueSessionStore(tmp_path / "sessions")
    session = replace(store.create("bug"), status=status)
    card = build_self_issue_session_card(session, store.load_working_set(session.session_id))
    assert [value["action"] for value in _actions(card)] == (
        [f"self-issue-session-{action}:{session.session_id}"] if action else []
    )


def test_session_card_projects_detailed_activity_timeline(tmp_path):
    store = JsonIssueSessionStore(tmp_path / "sessions")
    session = store.create("基础 wheel 导入失败")
    working = store.load_working_set(session.session_id)
    working = replace(working, workflow={
        "phase": "investigation",
        "label": "正在调查代码、测试、日志和证据",
        "activity_log": [
            {"kind": "tool_call", "label": "调查项目证据", "status": "running",
             "summary": "Agent 正在收集可验证证据"},
            {"kind": "tool_result", "label": "整理调查结果", "status": "completed",
             "summary": "已确认基础 wheel 缺少 httpx"},
        ],
    })
    card = build_self_issue_session_card(session, working)
    content = json.dumps(card, ensure_ascii=False)
    assert "分析过程：" in content
    assert "调查项目证据" in content
    assert "已确认基础 wheel 缺少 httpx" not in content


def test_session_activity_panel_shows_only_action_command_and_status(tmp_path):
    store = JsonIssueSessionStore(tmp_path / "sessions")
    session = store.create("bug")
    working = replace(store.load_working_set(session.session_id), workflow={
        "activity_log": [
            {"label": "搜索项目", "kind": "tool_call", "status": "running",
             "command": "rg httpx pyproject.toml", "summary": "不得显示的工具输出"},
            {"label": "搜索项目", "kind": "tool_result", "status": "completed",
             "command": "rg httpx pyproject.toml", "result_summary": "不得显示的结果正文"},
            {"label": "检查失败", "kind": "tool_result", "status": "failed",
             "command": "uv run pytest", "result_summary": "不得显示的失败正文"},
        ],
    })
    content = json.dumps(build_self_issue_session_card(session, working), ensure_ascii=False)
    assert "动作：搜索项目" in content
    assert "状态：running" in content and "状态：completed" in content and "状态：failed" in content
    assert "rg httpx pyproject.toml" in content and "uv run pytest" in content
    assert "不得显示的工具输出" not in content
    assert "不得显示的结果正文" not in content
    assert "不得显示的失败正文" not in content


def test_activity_command_keeps_user_workspace_path(tmp_path):
    store = JsonIssueSessionStore(tmp_path / "sessions")
    session = store.create("bug")
    working = replace(store.load_working_set(session.session_id), workflow={
        "activity_log": [
            {"label": "读取项目", "status": "running",
             "command": "/bin/bash -lc 'cat /home/uisee/project/pyproject.toml'"},
            {"label": "验证环境", "status": "running",
             "command": "TOKEN=example-private-value cat /home/uisee/project/pyproject.toml"},
        ],
    })
    content = json.dumps(build_self_issue_session_card(session, working), ensure_ascii=False)
    assert "/home/uisee/project/pyproject.toml" in content
    assert "/bin/bash -lc 'cat /home/uisee/project/pyproject.toml'" in content
    assert "[REDACTED_PERSONAL_PATH]" not in content
    assert "example-private-value" not in content


def test_activity_commands_keep_newlines_and_more_than_eight_records(tmp_path):
    store = JsonIssueSessionStore(tmp_path / "sessions")
    session = store.create("bug")
    command = "echo " + "x" * 700 + "\nrg httpx pyproject.toml"
    working = replace(store.load_working_set(session.session_id), workflow={
        "activity_log": [{"label": f"步骤 {index}", "status": "completed",
                          "command": command if index == 0 else f"echo {index}",
                          "result_detail": "不展示" * 10000}
                         for index in range(12)],
    })
    card = build_self_issue_session_card(session, working)
    panel = next(row for row in card["elements"] if row.get("tag") == "collapsible_panel")
    text = panel["elements"][0]["text"]["content"]
    assert command in text
    assert all(f"步骤 {index}；" in text for index in range(12))
    assert "不展示" not in text and panel["expanded"] is False


def test_activity_status_change_updates_same_card_without_result_body(tmp_path):
    store = JsonIssueSessionStore(tmp_path / "self-issues/portable-sessions")
    session = store.create("bug")
    transport = MockFeishuTransport()
    working = store.load_working_set(session.session_id)
    item = {"label": "执行检查", "command": "uv run pytest", "status": "running"}
    store.save_working_set(replace(working, workflow={"activity_log": [item]}), expected_revision=working.revision)
    push = lambda: push_self_issue_cards_once(tmp_path, transport, receive_id="chat")
    assert len(push()["sent"]) == 1
    for status in ("failed", "completed"):
        item = {**item, "status": status, "result_summary": "不可显示的结果" * 5000}
        store.save_working_set(replace(working, workflow={"activity_log": [item]}), expected_revision=working.revision)
        update = push()
        assert not update["sent"] and len(update["updated"]) == 1
        assert f"状态：{status}" in transport.updated_messages[-1][1]
        assert "不可显示的结果" not in transport.updated_messages[-1][1]
    item["result_summary"] = "仅结果正文变化"
    store.save_working_set(replace(working, workflow={"activity_log": [item]}), expected_revision=working.revision)
    assert not push()["updated"]
    assert len(transport.sent_messages) == 1


def test_activity_metadata_budget_keeps_recent_whole_steps_and_marks_omission(tmp_path):
    store = JsonIssueSessionStore(tmp_path / "sessions")
    session = replace(store.create("bug"), status=SessionStatus.READY_FOR_CONFIRMATION)
    working = replace(store.load_working_set(session.session_id), report={"analysis": "报告" * 2000}, workflow={
        "activity_log": [{"label": f"步骤 {index}", "status": "completed",
                          "command": "打印" * 120 + f" end-{index}"}
                         for index in range(200)],
    })
    card = build_self_issue_session_card(session, working)
    text = json.dumps(card, ensure_ascii=False)
    assert len(text.encode()) < 28000
    assert "容量限制" in text and "end-199" in text and "end-0" not in text


def test_session_card_projects_normalized_report_and_signed_confirm_fallback(tmp_path):
    store = JsonIssueSessionStore(tmp_path / "sessions")
    session = replace(store.create("基础 wheel 未声明 CLI 导入所需的 httpx 运行依赖"),
                      status=SessionStatus.READY_FOR_CONFIRMATION)
    working = store.load_working_set(session.session_id)
    working = replace(
        working,
        fields={
            "title": "基础 wheel 缺少 httpx",
            "bug_description": "CLI 启动时导入 httpx 失败",
            "actual_behavior": "zf --help 失败",
            "expected_behavior": "基础安装可运行 CLI",
        },
        report={"severity": "P1", "classification": "configuration",
                "downstream_verification_tasks": ["重建 wheel 并回归"]},
        gaps=("clean-install regression missing",),
    )
    from zf.integrations.feishu.callback_token import sign_action
    token = sign_action(b"secret", action="self-issue-session-confirm",
                        target=session.session_id, chat_id="chat", ttl_seconds=600, now=1000)
    card = build_self_issue_session_card(session, working, confirm_token=token)
    content = json.dumps(card, ensure_ascii=False)
    assert "基础 wheel 缺少 httpx" in content
    assert "CLI 启动时导入 httpx 失败" in content
    assert "严重程度" in content and "后续验证任务" in content
    assert f"self-issue-session-confirm {session.session_id} --token {token}" in content


@pytest.mark.parametrize("command,args,expected", [
    ("answer", ["sid", "answer", "text"], {"answer": "answer text"}),
    ("confirm", ["sid", "github", "owner/repo"], {"provider": "github", "target": "owner/repo", "confirmed_by": "owner"}),
    ("cancel", ["sid"], {}), ("pause", ["sid"], {}), ("retry", ["sid"], {}),
    ("get", ["sid"], {}), ("reanalyze", ["sid", "check", "again"], {"comment": "check again"}),
    ("materialize", ["sid", "2"], {"report_index": 2}),
    ("activity-result", ["sid", "activity"], {"activity_id": "activity"}),
])
def test_session_arguments_reach_controlled_action(tmp_path, monkeypatch, command, args, expected):
    captured = {}
    def execute(self, **kwargs):
        captured.update(kwargs)
        return {"ok": True}
    monkeypatch.setattr("zf.integrations.feishu.issue_adapter.ControlledActionService.execute", execute)
    result = handle_issue_command(FeishuCommandEnvelope(
        command=f"self-issue-session-{command}", args=args, user_id="owner",
        form_values={"session_id": "forged", "confirmed_by": "forged"},
    ), state_dir=tmp_path, config=None)
    assert result["ok"]
    assert captured["action"] == f"self-issue-session-{command}"
    assert captured["payload"]["session_id"] == "sid"
    for key, value in expected.items():
        assert captured["payload"][key] == value
    assert "triage_id" not in captured["payload"]


@pytest.mark.parametrize("status", [SessionStatus.WAITING_FOR_USER, SessionStatus.READY_FOR_CONFIRMATION])
def test_session_projection_is_idempotent_and_remints_expired_text_token(tmp_path, status):
    store = JsonIssueSessionStore(tmp_path / "self-issues/portable-sessions")
    session = store.create("bug")
    store.save_session(replace(session, status=status), expected_revision=session.revision)
    transport = MockFeishuTransport()
    def push(now):
        return push_self_issue_cards_once(tmp_path, transport, receive_id="chat",
            action_secret=b"test-secret", action_ttl_seconds=100, now=now)
    first = push(1000)
    assert len(first["sent"]) == 1
    second = push(1001)
    assert not second["sent"] and not second["updated"]
    assert len(push(1101)["updated"]) == 1


def test_progress_only_change_updates_card_without_session_revision_change(tmp_path):
    store = JsonIssueSessionStore(tmp_path / "self-issues/portable-sessions")
    session = store.create("bug")
    transport = MockFeishuTransport()
    push = lambda: push_self_issue_cards_once(tmp_path, transport, receive_id="chat")
    assert len(push()["sent"]) == 1
    working = store.load_working_set(session.session_id)
    store.save_working_set(replace(working, workflow={"activity_log": [{
        "label": "调查项目证据", "summary": "泛化摘要",
        "result_summary": "确认 httpx 未声明", "command": "rg httpx pyproject.toml",
    }]}), expected_revision=working.revision)
    assert store.load_session(session.session_id).revision == session.revision
    assert len(push()["updated"]) == 1
    card = transport.updated_messages[-1][1]
    assert "确认 httpx 未声明" not in card and "rg httpx pyproject.toml" in card
    content = json.loads(card)
    panels = [row for row in content["elements"] if row.get("tag") == "collapsible_panel"]
    assert len(panels) == 1 and panels[0]["expanded"] is False
    assert "rg httpx pyproject.toml" in json.dumps(panels[0])
    assert content["config"]["update_multi"] is True
    assert any(row.get("tag") == "action" for row in content["elements"])
    assert not push()["updated"]


def test_long_report_continuations_are_complete_redacted_and_idempotent(tmp_path):
    store = JsonIssueSessionStore(tmp_path / "self-issues/portable-sessions")
    session = store.create("bug")
    store.save_session(replace(session, status=SessionStatus.READY_FOR_CONFIRMATION), expected_revision=session.revision)
    working = store.load_working_set(session.session_id)
    detail = "证据位置与复现结论。" * 1300 + "报告末尾"
    store.save_working_set(replace(working, report={"analysis": detail, "secret": "do-not-leak"}), expected_revision=working.revision)
    transport = MockFeishuTransport()
    push = lambda now: push_self_issue_cards_once(tmp_path, transport, receive_id="chat", action_secret=b"secret", now=now)
    assert len(push(1000)["sent"]) > 2
    contents = [json.loads(message.content) for message in transport.sent_messages]
    panels = [row for card in contents for row in card["elements"]
              if row.get("tag") == "collapsible_panel"]
    assert panels and all(panel["expanded"] is False for panel in panels)
    text = "".join(row["text"]["content"] for panel in panels
                   for row in panel["elements"] if "text" in row)
    parts = _report_parts(store.load_working_set(session.session_id))
    assert detail in "".join(parts)
    assert all(part in text for part in parts)
    assert "do-not-leak" not in json.dumps(contents)
    assert all(len(message.content.encode()) < 28000 for message in transport.sent_messages)
    assert not push(1001)["sent"] and not push(1002)["updated"]


def test_signed_text_confirm_uses_same_gates_and_nonce_as_button(context, monkeypatch):
    monkeypatch.setattr(SelfIssueSessionHost, "_schedule", lambda self, sid: (
        self._run_background(sid), self.get(sid)
    )[1])
    created = _handle_event_data(_message('/issue Refresh fails.\n\nReproduction:\n1. Click Refresh\n\nExpected:\nPage updates'),
                                context=context, user_levels={})
    assert created["session_status"] == "ready_for_confirmation"
    transport = MockFeishuTransport()
    push_self_issue_cards_once(context.state_dir, transport, receive_id="chat", action_secret=b"test-secret")
    card = json.loads(transport.sent_messages[0].content)
    value = _actions(card)[0]
    command = next(row["text"]["content"].split("`")[1] for row in card["elements"]
                   if "--token" in row.get("text", {}).get("content", ""))
    assert value["t"] in command
    for data, reason in [
        (_message(command, user_id="outsider"), "not authorized"),
        (_message(command, chat_id="other"), "exact Feishu project chat"),
        (_message(command.replace(created["session_id"], "another")), "target_mismatch"),
        (_message(re.sub(r" --token \S+", "", command)), "token_required"),
        (_message(command + " github forged/repo"), "target_mismatch"),
    ]:
        result = _handle_event_data(data, context=context, user_levels={})
        assert result["status"] == "rejected" and reason in result["message"]
    result = _handle_event_data(_message(command, message_id="confirm-text"), context=context, user_levels={})
    assert result["ok"] and result["session_status"] == "completed"
    replay = _handle_event_data(_button(value), context=context, user_levels={})
    assert replay["status"] == "rejected" and "nonce_replay" in replay["message"]


def test_signed_text_answer_continues_session_and_replay_is_rejected(context, monkeypatch):
    store, session, command = _waiting(context)
    # Keep the runner deterministic while exercising the real controlled action.
    monkeypatch.setattr(SelfIssueSessionHost, "_schedule", lambda self, sid: self.get(sid))
    for data, reason in [
        (_message(command, user_id="outsider"), "not authorized"),
        (_message(command, chat_id="other"), "exact Feishu project chat"),
        (_message(command.replace(session.session_id, "another")), "target_mismatch"),
        (_message(re.sub(r" --token \S+", "", command)), "token_required"),
    ]:
        rejected = _handle_event_data(data, context=context, user_levels={})
        assert rejected["status"] == "rejected" and reason in rejected["message"]
    empty = command.replace('"版本 1.2"', '""')
    assert _handle_event_data(_message(empty), context=context, user_levels={})["status"] == "invalid_payload"
    result = _handle_event_data(_message(command), context=context, user_levels={})
    assert result["ok"] and store.load_session(session.session_id).user_replies == ("版本 1.2",)
    assert _handle_event_data(_message(command), context=context, user_levels={})["status"] == "duplicate"
    replay = _handle_event_data(_message(command, message_id="m2"), context=context, user_levels={})
    assert replay["status"] == "rejected" and "nonce_replay" in replay["message"]
    assert any(e.type == "feishu.issue.command.received" for e in EventLog(context.state_dir / "events.jsonl").read_all())


def test_new_session_confirm_materialize_and_project_draft(context, monkeypatch):
    def schedule(self, session_id):
        self._run_background(session_id)
        return self.get(session_id)
    monkeypatch.setattr(SelfIssueSessionHost, "_schedule", schedule)
    created = _handle_event_data(_message(
        '/zf self-issue-new "Refresh fails.\n\nReproduction:\n1. Click Refresh\n\nExpected:\nPage updates"',
    ), context=context, user_levels={})
    assert created["ok"] and created["session_status"] == "ready_for_confirmation"
    assert not (context.state_dir / "self-issues/intakes.json").exists()
    transport = MockFeishuTransport()
    def push():
        return push_self_issue_cards_once(context.state_dir, transport, receive_id="chat", action_secret=b"test-secret")
    push()
    card = json.loads(transport.sent_messages[0].content)
    confirmed = _handle_event_data(_button(_actions(card)[0]), context=context, user_levels={})
    assert confirmed["ok"] and confirmed["session_status"] == "completed"
    assert len(push()["updated"]) == 1
    card = json.loads(transport.updated_messages[-1][1])
    materialized = _handle_event_data(_button(_actions(card)[0], message_id="b2"), context=context, user_levels={})
    assert materialized["ok"] and materialized["publication_draft_id"]
    assert len(push()["sent"]) == 1
    assert "self-issue-preview:" in transport.sent_messages[-1].content


@pytest.mark.parametrize("command", ["self-issue-new", "self-issue-intake-start"])
def test_feishu_new_report_honors_disabled_policy(context, command):
    context.config.self_issue.enabled = False
    result = _handle_event_data(_message(f"/zf {command} bug"), context=context, user_levels={})
    assert result["status"] == "self_issue_disabled"
    assert not (context.state_dir / "self-issues/portable-sessions").exists()
    assert not (context.state_dir / "self-issues/intakes.json").exists()


def test_new_session_keeps_feishu_attachment_as_verified_private_sidecar(context, monkeypatch):
    monkeypatch.setattr(SelfIssueSessionHost, "_schedule", lambda self, sid: self.get(sid))
    transport = MockFeishuTransport()
    transport.resources[("attached", "file-key")] = (b"error at refresh", "text/plain")
    data = _message("/zf self-issue-new refresh fails", message_id="attached")
    data["payload"]["attachments"] = [{"resource_key": "file-key", "file_name": "error.log"}]
    result = _handle_event_data(data, context=context, user_levels={}, transport=transport)
    assert result["ok"]
    session = JsonIssueSessionStore(context.state_dir / "self-issues/portable-sessions").load_session(result["session_id"])
    attachment, = session.attachments
    assert attachment.name == "error.log" and attachment.disclosure == "private"
    assert (context.state_dir / attachment.ref).read_bytes() == b"error at refresh"
    data["payload"]["message_id"] = "missing-resource"
    failed = _handle_event_data(data, context=context, user_levels={}, transport=transport)
    assert failed["status"] == "attachment_download_failed"
    assert len(list((context.state_dir / "self-issues/portable-sessions").glob("*.session.json"))) == 1


def test_session_answer_persists_uploaded_file_as_private_attachment(tmp_path, monkeypatch):
    store = JsonIssueSessionStore(tmp_path / "self-issues/portable-sessions")
    initial = store.create("上传后崩溃")
    session = store.save_session(
        replace(initial, status=SessionStatus.WAITING_FOR_USER,
                revision=initial.revision + 1, user_question="请上传日志"),
        expected_revision=initial.revision,
    )
    monkeypatch.setattr(SelfIssueSessionHost, "_schedule", lambda self, sid: self.get(sid))
    transport = MockFeishuTransport()
    transport.resources[("reply-file", "log-key")] = (b"traceback", "text/plain")
    result = handle_issue_command(FeishuCommandEnvelope(
        command="self-issue-session-answer",
        args=[session.session_id],
        user_id="owner", chat_id="chat", message_id="reply-file",
        attachments=[{"resource_key": "log-key", "resource_type": "file", "filename": "trace.log"}],
    ), state_dir=tmp_path, config=None, transport=transport)
    assert result["ok"] and result["status"] == "investigating"
    saved = store.load_session(session.session_id)
    assert saved.user_replies == ("用户补充了附件：trace.log",)
    assert len(saved.attachments) == 1
    assert saved.attachments[0].name == "trace.log"
    assert saved.attachments[0].disclosure == "private"
    assert (tmp_path / saved.attachments[0].ref).read_bytes() == b"traceback"


def _uploaded_message(*, user="owner", chat="chat"):
    return {"message_id": "uploaded", "chat_id": chat, "msg_type": "file",
            "sender": {"id": user, "id_type": "open_id", "sender_type": "user"},
            "body": {"content": json.dumps({"file_key": "log-key", "file_name": "ZF-TEST-046.md"})}}


@pytest.mark.parametrize("with_text", [True, False])
def test_signed_answer_accepts_quoted_file(context, monkeypatch, with_text):
    store, session, command = _waiting(context)
    monkeypatch.setattr(SelfIssueSessionHost, "_schedule", lambda self, sid: self.get(sid))
    transport = MockFeishuTransport()
    transport.messages["uploaded"] = _uploaded_message()
    transport.resources[("uploaded", "log-key")] = (b"ZF-TEST-046: No module named httpx", "text/plain")
    if not with_text:
        command = command.replace('"版本 1.2"', '""')
    event = _message(command)
    event["payload"]["parent_message_id"] = "uploaded"
    result = _handle_event_data(event, context=context, user_levels={}, transport=transport)
    assert result["ok"]
    saved = store.load_session(session.session_id)
    assert saved.status == SessionStatus.INVESTIGATING
    assert saved.attachments[0].name == "ZF-TEST-046.md"
    assert (context.state_dir / saved.attachments[0].ref).read_bytes().startswith(b"ZF-TEST-046")


@pytest.mark.parametrize("user,chat", [("other", "chat"), ("owner", "other-chat")])
def test_quoted_file_rejects_different_sender_or_chat(context, monkeypatch, user, chat):
    store, session, command = _waiting(context)
    transport = MockFeishuTransport()
    transport.messages["uploaded"] = _uploaded_message(user=user, chat=chat)
    event = _message(command)
    event["payload"]["quote_message_id"] = "uploaded"
    result = _handle_event_data(event, context=context, user_levels={}, transport=transport)
    assert result["status"] == "attachment_download_failed"
    assert store.load_session(session.session_id) == session


def test_host_rejects_unverified_answer_attachment_without_changing_session(context):
    store, session, _ = _waiting(context)
    host = SelfIssueSessionHost(context.state_dir, context.project_root)
    with pytest.raises(ValueError, match="outside"):
        host.answer_and_schedule(session.session_id, "see file", attachments=[{"ref": "/etc/passwd"}])
    assert store.load_session(session.session_id) == session


@pytest.mark.parametrize("command", ["/zf self-issue-new", "/issue"])
def test_new_report_reads_quoted_file_from_original_message(context, monkeypatch, command):
    monkeypatch.setattr(SelfIssueSessionHost, "_schedule", lambda self, sid: self.get(sid))
    transport = MockFeishuTransport()
    transport.messages["uploaded"] = _uploaded_message()
    transport.resources[("uploaded", "log-key")] = (b"ZF-TEST-046", "text/plain")
    event = _message(f"{command} 基础 wheel 的 CLI 导入缺少 httpx，请调查附件。")
    event["payload"]["quote_message_id"] = "uploaded"
    result = _handle_event_data(event, context=context, user_levels={}, transport=transport)
    assert result["ok"]
    saved = JsonIssueSessionStore(context.state_dir / "self-issues/portable-sessions").load_session(result["session_id"])
    assert saved.attachments[0].name == "ZF-TEST-046.md"


@pytest.mark.parametrize("prefix", ["/issue ", "@_user_1 Agent /issue "])
def test_issue_shorthand_creates_portable_session(context, monkeypatch, prefix):
    monkeypatch.setattr(SelfIssueSessionHost, "_schedule", lambda self, sid: self.get(sid))
    description = '基础 wheel 导入失败："No module named httpx"\n路径 C:\\app'
    result = _handle_event_data(
        _message(prefix + description),
        context=context,
        user_levels={},
    )
    assert result["ok"]
    session = JsonIssueSessionStore(
        context.state_dir / "self-issues/portable-sessions"
    ).load_session(result["session_id"])
    assert session.raw_report == description


@pytest.mark.parametrize("text", [
    "/issue+描述", "/issue-merge 描述", "/issues 描述", "请解释 /issue 描述",
])
def test_issue_shorthand_requires_exact_command(text):
    event = MockFeishuTransport().parse_webhook(_message(text))
    assert CommandGateway().parse(event) is None


def test_issue_shorthand_keeps_identity_and_policy_gates(context):
    rejected = _handle_event_data(
        _message("/issue bug", user_id="outsider"), context=context, user_levels={},
    )
    assert rejected["status"] == "rejected"
    context.config.self_issue.enabled = False
    disabled = _handle_event_data(_message("/issue bug"), context=context, user_levels={})
    assert disabled["status"] == "self_issue_disabled"
    assert not (context.state_dir / "self-issues/portable-sessions").exists()


def test_issue_shorthand_keeps_current_message_attachment(context, monkeypatch):
    monkeypatch.setattr(SelfIssueSessionHost, "_schedule", lambda self, sid: self.get(sid))
    transport = MockFeishuTransport()
    transport.resources[("issue-command", "report-file")] = (
        b"ModuleNotFoundError: No module named httpx", "text/plain"
    )
    event = _message(
        "/issue 基础 wheel 导入失败",
        message_id="issue-command",
    )
    event["payload"]["attachments"] = [{
        "resource_key": "report-file",
        "resource_type": "file",
        "filename": "ZF-TEST-046.md",
    }]
    result = _handle_event_data(
        event,
        context=context,
        user_levels={},
        transport=transport,
    )
    assert result["ok"]
    session = JsonIssueSessionStore(
        context.state_dir / "self-issues/portable-sessions"
    ).load_session(result["session_id"])
    assert session.attachments[0].name == "ZF-TEST-046.md"


@pytest.mark.parametrize("entrypoint", ["consume", "bridge", "kanban"])
@pytest.mark.parametrize("resource_type", ["file", "image"])
def test_issue_entrypoints_preserve_quoted_upload(context, monkeypatch, entrypoint, resource_type):
    from zf.cli.feishu_consume import bridge_inbound_message, ingest_feishu_event
    from zf.integrations.feishu.kanban_agent_inbound import kanban_agent_inbound_reply

    monkeypatch.setattr(SelfIssueSessionHost, "_schedule", lambda self, sid: self.get(sid))
    context.config.integrations.feishu_routing["chat"].target = "kanban_agent"
    transport = MockFeishuTransport()
    message = _uploaded_message()
    message["msg_type"] = resource_type
    message["body"]["content"] = json.dumps(
        {"image_key": "log-key"} if resource_type == "image"
        else {"file_key": "log-key", "file_name": "ZF-TEST-046.md"}
    )
    transport.messages["uploaded"] = message
    content = b"\x89PNG\r\n\x1a\nimage" if resource_type == "image" else b"missing httpx"
    transport.resources[("uploaded", "log-key")] = (
        content, "image/png" if resource_type == "image" else "text/plain",
    )
    raw = _message("@_user_1 Agent /issue ZF-TEST-046")
    raw["payload"]["quote_message_id"] = "uploaded"

    def dispatch():
        if entrypoint == "consume":
            return ingest_feishu_event(raw, context=context, transport=transport)
        event = transport.parse_webhook(raw)
        if entrypoint == "bridge":
            return bridge_inbound_message(event, context=context, transport=transport)
        return kanban_agent_inbound_reply(
            context.state_dir, context.config, event, None,
            context=context, transport=transport,
        )

    result = dispatch()
    assert result["ok"]
    session = JsonIssueSessionStore(
        context.state_dir / "self-issues/portable-sessions"
    ).load_session(result["session_id"])
    assert session.raw_report == "ZF-TEST-046"
    assert len(session.attachments) == 1
    assert session.attachments[0].disclosure == "private"
    assert (context.state_dir / session.attachments[0].ref).read_bytes() == content
    assert dispatch()["status"] == "duplicate"


@pytest.mark.parametrize("with_upload", [False, True])
def test_one_shot_bridge_accepts_issue_shorthand(context, monkeypatch, capsys, with_upload):
    from types import SimpleNamespace
    from zf.cli.feishu_consume import run_bridge

    monkeypatch.setattr(SelfIssueSessionHost, "_schedule", lambda self, sid: self.get(sid))
    monkeypatch.setattr("zf.cli.feishu_consume.resolve_project_context", lambda **kw: context)
    transport = MockFeishuTransport()
    monkeypatch.setattr("zf.cli.feishu_consume.FeishuHttpTransport", lambda: transport)
    raw = _message("/issue ZF-TEST-046")
    if with_upload:
        raw["payload"]["quote_message_id"] = "uploaded"
        transport.messages["uploaded"] = _uploaded_message()
        transport.resources[("uploaded", "log-key")] = (b"missing httpx", "text/plain")
    result = run_bridge(SimpleNamespace(event_json=json.dumps(raw)))
    assert result == 0
    session_id = json.loads(capsys.readouterr().out)["session_id"]
    session = JsonIssueSessionStore(
        context.state_dir / "self-issues/portable-sessions"
    ).load_session(session_id)
    assert len(session.attachments) == int(with_upload)


@pytest.mark.parametrize("command", ["/zf self-issue-new", "/issue"])
@pytest.mark.parametrize("upload_first", [True, False])
def test_batched_upload_download_uses_original_message_id(context, monkeypatch, command, upload_first):
    from zf.integrations.feishu.bridge_watch import merge_batch
    monkeypatch.setattr(SelfIssueSessionHost, "_schedule", lambda self, sid: self.get(sid))
    transport = MockFeishuTransport()
    transport.resources[("uploaded", "log-key")] = (b"wheel install failed", "text/plain")
    batch = [
        {"message_id": "uploaded", "user_id": "owner", "chat_id": "chat",
         "attachments": [{"resource_key": "log-key", "filename": "install.log"}]},
        {"message_id": "command", "user_id": "owner", "chat_id": "chat",
         "text": f"{command} ZF-TEST-046"},
    ]
    event = merge_batch(batch if upload_first else list(reversed(batch)))
    result = _handle_event_data(event, context=context, user_levels={}, transport=transport)
    assert result["ok"]
    saved = JsonIssueSessionStore(context.state_dir / "self-issues/portable-sessions").load_session(result["session_id"])
    assert saved.attachments[0].name == "install.log"
