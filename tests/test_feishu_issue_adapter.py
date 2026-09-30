from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from zf.core.config.schema import SelfIssueConfig, ZfConfig

from zf.core.issue_triage.models import IssueMirror
from zf.core.issue_triage.store import IssueMirrorStore
from zf.core.events.log import EventLog
from zf.core.events import EventWriter
from zf.core.events.known_types import KNOWN_EVENT_TYPES
from zf.core.self_issue.models import IssueDraft, PublicationBatch, SelfIssueIntake
from zf.core.self_issue.store import (
    IssueDraftStore,
    PublicationBatchStore,
    SelfIssueIntakeStore,
)
from zf.integrations.forge.base import ForgeResult, IssuePublishRequest, PublishedIssue
from zf.integrations.feishu.gateway import CommandGateway
from zf.integrations.feishu.issue_adapter import (
    build_issue_card,
    build_self_issue_card,
    handle_issue_command,
    issue_show,
    issue_summary,
    push_issue_cards_once,
    push_self_issue_cards_once,
)
from zf.integrations.feishu.self_issue_cards import build_self_issue_session_card
from issue_core import JsonIssueSessionStore, SessionStatus
from dataclasses import replace
from zf.integrations.feishu.transport import MockFeishuTransport
from zf.integrations.feishu.transport import parse_webhook_payload
from zf.runtime.control_actions_self_issue import SELF_ISSUE_ACTIONS
from zf.runtime.self_issue_service import SelfIssueService


def _mirror() -> IssueMirror:
    return IssueMirror(
        issue_key="github:repo-1:7",
        provider="github",
        repository_id="repo-1",
        repository="acme/demo",
        number=7,
        node_id="I_7",
        html_url="https://github.com/acme/demo/issues/7",
        title="Bridge issue",
        author_login="owner",
        github_state="open",
        created_at="2026-09-14T00:00:00+00:00",
        updated_at="2026-09-14T00:00:00+00:00",
        last_seen_at="2026-09-14T00:00:00+00:00",
    )


def test_issue_read_projection_is_provider_neutral(tmp_path: Path) -> None:
    store = IssueMirrorStore(tmp_path)
    store.upsert(_mirror(), "body")
    summary = issue_summary(tmp_path)
    assert summary["total"] == 1
    shown = issue_show(tmp_path, "#7")
    assert shown["ok"] is True
    assert shown["issue"]["issue_key"] == "github:repo-1:7"
    assert "body_ref" not in shown["issue"]


def test_issue_card_projection_is_idempotent_and_updates_in_place(tmp_path: Path) -> None:
    IssueMirrorStore(tmp_path).upsert(_mirror(), "body")
    transport = MockFeishuTransport()
    first = push_issue_cards_once(tmp_path, transport, receive_id="oc_test")
    second = push_issue_cards_once(tmp_path, transport, receive_id="oc_test")
    assert len(first["sent"]) == 1
    assert second["sent"] == []
    assert second["updated"] == []
    assert len(transport.sent_messages) == 1
    card = json.loads(transport.sent_messages[0].content)
    assert card["_card_key"].startswith("issue-")
    assert card["config"]["update_multi"] is True
    assert any("开始分诊" in json.dumps(card, ensure_ascii=False) for _ in [0])


def test_signed_issue_card_projection_separates_semantics_from_token_rotation(
    tmp_path: Path,
) -> None:
    IssueMirrorStore(tmp_path).upsert(_mirror(), "body")
    transport = MockFeishuTransport()
    first = push_issue_cards_once(
        tmp_path,
        transport,
        receive_id="oc_test",
        action_secret=b"test-secret",
        action_ttl_seconds=100,
        now=1000,
    )
    second = push_issue_cards_once(
        tmp_path,
        transport,
        receive_id="oc_test",
        action_secret=b"test-secret",
        action_ttl_seconds=100,
        now=1001,
    )
    rotated = push_issue_cards_once(
        tmp_path,
        transport,
        receive_id="oc_test",
        action_secret=b"test-secret",
        action_ttl_seconds=100,
        now=1101,
    )
    assert len(first["sent"]) == 1
    assert second["sent"] == [] and second["updated"] == []
    assert len(rotated["updated"]) == 1


def test_issue_card_provider_failure_is_audited_without_ledger_entry(tmp_path: Path) -> None:
    class FailingTransport(MockFeishuTransport):
        def send_card(self, message):
            raise RuntimeError("provider unavailable")

    IssueMirrorStore(tmp_path).upsert(_mirror(), "body")
    result = push_issue_cards_once(tmp_path, FailingTransport(), receive_id="oc_test")
    assert len(result["failed"]) == 1
    events = EventLog(tmp_path / "events.jsonl").read_all()
    failure = [event for event in events if event.type == "feishu.notification.failed"]
    assert failure and failure[-1].payload["operation"] == "send"


def test_issue_card_structured_fields_use_one_line_each() -> None:
    card = build_issue_card(
        _mirror(),
        {"state": "triage_queued", "triage_id": "TRIAGE-7"},
        project_id="p1",
    )
    text = card["elements"][0]["text"]["content"]
    assert "状态：`triage_queued`" in text
    assert "仓库：`acme/demo`" in text
    assert "状态：`triage_queued`　仓库" not in text


def test_gateway_whitelists_issue_commands_and_keeps_reads_viewer() -> None:
    assert "feishu.issue.command.received" in KNOWN_EVENT_TYPES
    assert CommandGateway().parse(
        type("E", (), {
            "event_type": "message",
            "payload": {"text": "/zf issue-show 7", "message_id": "m1"},
            "user_id": "u",
            "chat_id": "c",
        })()
    ).command == "issue-show"
    assert CommandGateway().parse(
        type("E", (), {
            "event_type": "message",
            "payload": {"text": "/zf self-issue-intake-save sii-1 1", "message_id": "m2"},
            "user_id": "u", "chat_id": "c",
        })()
    ).command == "self-issue-intake-save"
    alias = CommandGateway().parse(
        type("E", (), {
            "event_type": "message",
            "payload": {"text": "/zf self-issue new smoke", "message_id": "m3"},
            "user_id": "u", "chat_id": "c",
        })()
    )
    assert alias is not None
    assert alias.command == "self-issue-new"
    assert alias.args == ["smoke"]
    shorthand = CommandGateway().parse(
        type("E", (), {
            "event_type": "message",
            "payload": {"text": "/issue 基础 wheel 导入失败", "message_id": "m4"},
            "user_id": "u", "chat_id": "c",
        })()
    )
    assert shorthand is not None
    assert shorthand.command == "self-issue-new"
    assert shorthand.args == ["基础 wheel 导入失败"]


def test_gateway_parses_signed_portable_session_answer_text() -> None:
    envelope = CommandGateway().parse(type("E", (), {
        "event_type": "message",
        "payload": {"text": "/zf self-issue-session-answer sid-1 --token tok-1 \"版本 1.2\"", "message_id": "m"},
        "user_id": "u", "chat_id": "c",
    })())
    assert envelope is not None
    assert envelope.command == "self-issue-session-answer"
    assert envelope.args == ["sid-1", "版本 1.2"]
    assert envelope.action_token == "tok-1"


def test_portable_session_card_uses_remote_answer_and_lifecycle_controls(tmp_path: Path) -> None:
    store = JsonIssueSessionStore(tmp_path / "sessions")
    session = store.create("上传后崩溃")
    waiting = replace(
        session, status=SessionStatus.WAITING_FOR_USER,
        user_question="请提供版本号",
    )
    store.save_session(waiting, expected_revision=session.revision)
    card = build_self_issue_session_card(
        waiting, store.load_working_set(waiting.session_id),
        web_base_url="https://zf.example",
        answer_token="signed-token",
    )
    rendered = json.dumps(card, ensure_ascii=False)
    assert "self-issue-session-answer" in rendered
    assert "--token signed-token" in rendered
    assert "请提供版本号" in rendered


@pytest.mark.parametrize("lane", ["triage", "fix"])
def test_issue_triage_button_payload_is_bound_to_issue_and_attempt(tmp_path: Path, lane: str) -> None:
    card = build_issue_card(
        _mirror(),
        {"state": f"{lane}_queued", "triage_id": "run-7", "triage_attempt": 2, "queued_cancel_available": True},
        project_id="p1",
    )
    action = next(
        item["value"]["action"]
        for item in card["elements"]
        if item.get("tag") == "action"
        for item in item["actions"]
    )
    assert action == f"issue-{lane}-cancel:7:run-7:2"


@pytest.mark.parametrize("lane", ["triage", "fix"])
def test_remote_queue_without_local_cancel_authority_has_no_cancel_button(lane: str) -> None:
    card = build_issue_card(_mirror(), {
        "state": f"{lane}_queued", "triage_id": "old-run", "queued_cancel_available": False,
    }, project_id="p1")
    assert f"issue-{lane}-cancel" not in json.dumps(card)


@pytest.mark.parametrize("lane", ["triage", "fix"])
def test_queue_projection_and_cancel_use_current_lane_attempt(tmp_path: Path, monkeypatch, lane: str) -> None:
    IssueMirrorStore(tmp_path).upsert(_mirror(), "body")
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    writer.emit("external_issue.triage.queued", actor="test", task_id="ISSUE-7", payload={
        "source_key": _mirror().issue_key, "workflow_run_id": "TRIAGE-7", "triage_attempt": 2,
    })
    if lane == "fix":
        writer.emit("workflow.invoke.requested", actor="test", task_id="ISSUE-7", payload={
            "source_key": _mirror().issue_key, "workflow_run_id": "FIX-7", "flow_kind": "issue", "triage_attempt": 3,
        })
    workflow = issue_show(tmp_path, "7")["issue"]["workflow"]
    assert workflow["triage_attempt"] == (3 if lane == "fix" else 2)
    transport = MockFeishuTransport()
    queued = push_issue_cards_once(tmp_path, transport, receive_id="chat")
    card_key = queued["sent"][0]
    queued_message_id = queued["ledger"][card_key]["message_id"]
    queued_card = json.loads(transport.sent_messages[0].content)
    assert queued_card["config"]["update_multi"] is True
    assert "取消排队" in json.dumps(queued_card, ensure_ascii=False)
    card = build_issue_card(_mirror(), workflow, project_id="p1")
    action = next(button["value"]["action"] for row in card["elements"]
                  if row.get("tag") == "action" for button in row["actions"])
    event = type("E", (), {"event_type": "button_action", "payload": {"action": action},
                            "user_id": "owner", "chat_id": "chat"})()
    monkeypatch.setattr(
        "zf.runtime.control_actions_external_issue.ExternalIssueActionsMixin._sync_cancelled_issue_label",
        lambda self, event: {"status": "mock"},
    )
    result = handle_issue_command(CommandGateway().parse(event), state_dir=tmp_path, config=ZfConfig())
    assert result["status"] == "cancelled"
    assert issue_show(tmp_path, "7")["issue"]["workflow"]["state"] == f"{lane}_cancelled"
    cancelled = push_issue_cards_once(tmp_path, transport, receive_id="chat")
    assert cancelled["sent"] == []
    assert cancelled["updated"] == [card_key]
    message_id, content = transport.updated_messages[-1]
    assert message_id == queued_message_id
    assert json.loads(content)["config"]["update_multi"] is True
    assert f"{lane}_cancelled" in content
    assert "取消排队" not in content
    assert cancelled["ledger"][card_key]["state"] == f"{lane}_cancelled"
    assert push_issue_cards_once(tmp_path, transport, receive_id="chat")["updated"] == []


def test_issue_needs_info_card_links_reporter_to_remote_issue() -> None:
    card = build_issue_card(
        _mirror(),
        {
            "state": "needs_info",
            "triage_id": "run-7",
            "source_revision": "rev-7",
        },
        project_id="p1",
        clarification={
            "questions": [
                {"id": "logs", "type": "long_text", "question": "请提供日志"},
                {"id": "impact", "type": "single_choice", "question": "影响范围", "options": ["one", "many"]},
            ],
        },
    )
    rendered = json.dumps(card, ensure_ascii=False)
    assert "issue-needs-info-submit" not in rendered
    assert "answer_logs" not in rendered
    assert "直接回复远端 GitHub Issue" in rendered
    assert "https://github.com/acme/demo/issues/7" in rendered


def test_needs_info_uses_current_request_status_and_handles_exhaustion() -> None:
    card = build_issue_card(_mirror(), {
        "state": "needs_info_exhausted", "clarification_request_status": "published",
        "clarification_request_url": "https://github.com/acme/demo/issues/7#issuecomment-1",
    }, project_id="p1", clarification={"questions": [{"question": "哪个版本？"}]})
    rendered = json.dumps(card, ensure_ascii=False)
    assert "published" in rendered and "issuecomment-1" in rendered
    assert "哪个版本？" in rendered and "轮次已达上限" in rendered
    assert "issue-needs-info-submit" not in rendered


def test_issue_candidate_cards_bind_candidate_and_source_revision() -> None:
    candidate = {
        "candidate_head_sha": "a" * 40,
        "source_revision": "source-1",
    }
    for state, expected in (
        ("verified_candidate", "issue-candidate-review:7:"),
        ("approved_for_pr", "issue-candidate-prepare:7:"),
        ("pr_open", "issue-candidate-pr-refresh:7"),
    ):
        card = build_issue_card(
            _mirror(),
            {"state": state},
            project_id="p1",
            delivery={"candidate": candidate},
        )
        assert expected in json.dumps(card)


def test_issue_cards_expose_fix_proposal_review_and_run_controls() -> None:
    proposal = {
        "proposal_event_id": "evt-proposal",
        "proposal_id": "proposal-1",
        "proposal_digest": "digest-1",
        "payload": {
            "task_id": "TASK-7",
            "objective": "修复 Issue",
            "route_id": "delivery:issue:default",
            "parameters": {"allowed_paths": ["src/fix.py"]},
        },
    }
    card = build_issue_card(
        _mirror(),
        {"state": "awaiting_fix_approval", "task_id": "TASK-7"},
        project_id="p1",
        web_base_url="http://web",
        proposal=proposal,
    )
    rendered = json.dumps(card, ensure_ascii=False)
    assert "批准并排队 Fix" in rendered
    assert "拒绝 Proposal" in rendered
    assert "issue-fix-proposal-approve:7:evt-proposal:proposal-1:digest-1" in rendered
    assert "打开 Proposal" in rendered

    running = build_issue_card(
        _mirror(),
        {"state": "fixing", "run_id": "FIX-7"},
        project_id="p1",
    )
    running_text = json.dumps(running, ensure_ascii=False)
    assert "issue-run-pause:7:FIX-7" in running_text
    assert "永久取消 Run" in running_text


def test_verified_candidate_card_exposes_changes_and_reject_review() -> None:
    card = build_issue_card(
        _mirror(),
        {"state": "verified_candidate"},
        project_id="p1",
        delivery={"candidate": {"candidate_head_sha": "a" * 40, "source_revision": "source-1"}},
    )
    rendered = json.dumps(card, ensure_ascii=False)
    assert ":changes_requested" in rendered
    assert ":reject" in rendered


def test_issue_candidate_pr_form_binds_sha_revision_without_treating_sha_as_url(
    monkeypatch, tmp_path: Path,
) -> None:
    candidate = {"candidate_head_sha": "b" * 40, "source_revision": "source-2"}
    card = build_issue_card(
        _mirror(), {"state": "publication_prepared"}, project_id="p1",
        delivery={"candidate": candidate},
    )
    assert "issue-candidate-pr:7:" + "b" * 40 + ":source-2" in json.dumps(card)
    captured: dict = {}

    def fake_execute(self, **kwargs):
        captured.update(kwargs)
        return {"ok": True, "status": "pr_recorded"}

    monkeypatch.setattr(
        "zf.integrations.feishu.issue_adapter.ControlledActionService.execute",
        fake_execute,
    )
    envelope = type("E", (), {
        "command": "issue-candidate-pr",
        "args": ["7", "b" * 40, "source-2"],
        "form_values": {"url": "https://github.com/acme/demo/pull/9"},
        "attachments": [], "user_id": "ou_owner", "chat_id": "oc_project",
        "message_id": "m-pr-form", "idempotency_key": "k-pr-form",
    })()
    result = handle_issue_command(envelope, state_dir=tmp_path, config=None)
    assert result["status"] == "pr_recorded"
    assert captured["payload"]["candidate_sha"] == "b" * 40
    assert captured["payload"]["source_revision"] == "source-2"
    assert captured["payload"]["url"] == "https://github.com/acme/demo/pull/9"


@pytest.mark.parametrize("lane", ["triage", "fix"])
def test_issue_mutation_is_delegated_to_controlled_action(monkeypatch, tmp_path: Path, lane: str) -> None:
    captured: dict = {}

    def fake_execute(self, **kwargs):
        captured.update(kwargs)
        return {"ok": True, "status": "queued"}

    monkeypatch.setattr(
        "zf.integrations.feishu.issue_adapter.ControlledActionService.execute",
        fake_execute,
    )
    envelope = type("E", (), {
        "command": f"issue-{lane}-cancel",
        "args": ["7", "run-7", "2"],
        "form_values": {},
        "user_id": "ou_owner",
        "chat_id": "oc_project",
        "message_id": "m1",
        "idempotency_key": "k1",
    })()
    result = handle_issue_command(envelope, state_dir=tmp_path, config=None)
    assert result["ok"] is True
    assert captured["action"] == f"external-issue-{lane}-queued-cancel"
    assert captured["payload"]["issue_number"] == 7
    assert captured["payload"]["triage_id"] == "run-7"
    assert captured["payload"]["expected_revision"] == 2
    assert captured["payload"]["defer_lifecycle_label_sync"] is True


def test_issue_command_rejects_malformed_numbers_without_invoking_kernel(tmp_path: Path) -> None:
    envelope = type("E", (), {
        "command": "issue-triage-cancel", "args": ["not-a-number", "run", "bad"],
        "form_values": {}, "attachments": [], "user_id": "ou_owner",
        "chat_id": "oc_project", "message_id": "m-bad", "idempotency_key": "k-bad",
    })()
    result = handle_issue_command(envelope, state_dir=tmp_path, config=None)
    assert result == {
        "ok": False,
        "status": "invalid_payload",
        "message": "issue number must be an integer",
    }


def test_self_issue_revision_and_needs_info_numbers_fail_closed(tmp_path: Path) -> None:
    revision_event = type("E", (), {
        "command": "self-issue-intake-save", "args": ["intake-1", "bad"],
        "form_values": {}, "attachments": [], "user_id": "ou_owner",
        "chat_id": "oc_project", "message_id": "m-revision", "idempotency_key": "k-revision",
    })()
    revision_result = handle_issue_command(
        revision_event, state_dir=tmp_path, config=None,
    )
    assert revision_result == {
        "ok": False,
        "status": "invalid_payload",
        "message": "revision must be an integer",
    }

    needs_info_event = type("E", (), {
        "command": "issue-needs-info-submit", "args": [],
        "form_values": {"issue_number": "bad", "triage_id": "run-1"},
        "attachments": [], "user_id": "ou_owner", "chat_id": "oc_project",
        "message_id": "m-needs-info", "idempotency_key": "k-needs-info",
    })()
    needs_info_result = handle_issue_command(
        needs_info_event, state_dir=tmp_path, config=None,
    )
    assert needs_info_result == {
        "ok": False,
        "status": "invalid_payload",
        "message": "issue number must be an integer",
    }


def test_issue_candidate_button_arguments_are_normalized(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    def fake_execute(self, **kwargs):
        captured.update(kwargs)
        return {"ok": True, "status": "accepted"}

    monkeypatch.setattr(
        "zf.integrations.feishu.issue_adapter.ControlledActionService.execute",
        fake_execute,
    )
    envelope = type("E", (), {
        "command": "issue-candidate-review",
        "args": ["7", "a" * 40, "source-1", "approve"],
        "form_values": {},
        "user_id": "ou_owner",
        "chat_id": "oc_project",
        "message_id": "m-candidate",
        "idempotency_key": "k-candidate",
    })()
    result = handle_issue_command(envelope, state_dir=tmp_path, config=None)
    assert result["ok"] is True
    assert captured["action"] == "external-issue-candidate-review"
    assert captured["payload"]["issue_number"] == 7
    assert captured["payload"]["candidate_sha"] == "a" * 40
    assert captured["payload"]["source_revision"] == "source-1"
    assert captured["payload"]["verdict"] == "approve"


@pytest.mark.parametrize("command,expected_action", [
    ("issue-fix-proposal-approve", "workflow-start"),
    ("issue-fix-proposal-reject", "kanban-proposal-dismiss"),
])
def test_fix_proposal_buttons_use_exact_proposal_identity(
    monkeypatch, tmp_path: Path, command: str, expected_action: str,
) -> None:
    IssueMirrorStore(tmp_path).upsert(_mirror(), "body")
    captured: dict = {}

    proposal = {
        "proposal_event_id": "evt-proposal",
        "proposal_id": "proposal-1",
        "proposal_digest": "digest-1",
        "payload": {
            "task_id": "TASK-7",
            "objective": "修复 Issue",
            "route_id": "delivery:issue:default",
            "parameters": {"allowed_paths": ["src/fix.py"]},
        },
    }
    monkeypatch.setattr("zf.integrations.feishu.issue_adapter._workflow", lambda item, state_dir: {
        "state": "awaiting_fix_approval", "task_id": "TASK-7", "proposal_id": "proposal-1",
    })
    monkeypatch.setattr("zf.integrations.feishu.issue_adapter._pending_issue_proposal", lambda state_dir, workflow: proposal)

    def fake_execute(self, **kwargs):
        captured.update(kwargs)
        return {"ok": True, "status": "completed"}

    monkeypatch.setattr("zf.integrations.feishu.issue_adapter.ControlledActionService.execute", fake_execute)
    suffix = "7:evt-proposal:proposal-1:digest-1"
    event = type("E", (), {
        "event_type": "button_action",
        "payload": {"action": f"{command}:{suffix}", "form_values": {"reason": "需要补充测试"}},
        "user_id": "owner", "chat_id": "chat",
    })()
    envelope = CommandGateway().parse(event)
    result = handle_issue_command(envelope, state_dir=tmp_path, config=ZfConfig())
    assert result["ok"] is True
    assert captured["action"] == expected_action
    assert captured["payload"]["proposal_event_id"] == "evt-proposal"
    if command.endswith("reject"):
        assert captured["payload"]["reason"] == "需要补充测试"


def test_message_resource_keys_are_preserved_for_self_issue_download() -> None:
    event = parse_webhook_payload({
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "message": {
                "message_id": "m7",
                "chat_id": "oc1",
                "content": json.dumps({"file_key": "file_v1", "file_name": "log.txt"}),
            },
            "sender": {"sender_id": {"open_id": "ou1"}},
        },
    })
    assert event is not None
    assert event.payload["attachments"][0]["resource_key"] == "file_v1"
    assert event.payload["attachments"][0]["filename"] == "log.txt"


def test_self_issue_attachment_download_is_normalized_before_controlled_action(
    monkeypatch, tmp_path: Path,
) -> None:
    captured: dict = {}

    def fake_execute(self, **kwargs):
        captured.update(kwargs)
        return {"ok": True, "status": "attachment_added"}

    monkeypatch.setattr(
        "zf.integrations.feishu.issue_adapter.ControlledActionService.execute",
        fake_execute,
    )
    transport = MockFeishuTransport()
    transport.resources[("m8", "file_v1")] = (b"hello", "application/octet-stream")
    envelope = type("E", (), {
        "command": "self-issue-attachment-add",
        "args": ["intake-1"],
        "form_values": {"intake_id": "intake-1", "filename": "log.txt"},
        "attachments": [{"resource_key": "file_v1", "resource_type": "file", "filename": "log.txt"}],
        "user_id": "ou_owner",
        "chat_id": "oc_project",
        "message_id": "m8",
        "idempotency_key": "k8",
    })()
    result = handle_issue_command(
        envelope,
        state_dir=tmp_path,
        config=None,
        transport=transport,
    )
    assert result["status"] == "attachment_added"
    assert captured["action"] == "self-issue-intake-attachment-add"
    assert captured["payload"]["intake_id"] == "intake-1"
    assert captured["payload"]["content_type"] == "text/plain"
    assert captured["payload"]["content_base64"] == "aGVsbG8="


def test_self_issue_new_starts_portable_session(
    monkeypatch, tmp_path: Path,
) -> None:
    captured: dict = {}

    def fake_execute(self, **kwargs):
        captured.update(kwargs)
        return {"ok": True, "status": "investigating"}

    monkeypatch.setattr(
        "zf.integrations.feishu.issue_adapter.ControlledActionService.execute",
        fake_execute,
    )
    envelope = type("E", (), {
        "command": "self-issue-new",
        "args": ["页面", "在上传后崩溃"],
        "form_values": {},
        "attachments": [],
        "user_id": "ou_owner",
        "chat_id": "oc_project",
        "message_id": "m-new",
        "idempotency_key": "k-new",
    })()
    result = handle_issue_command(envelope, state_dir=tmp_path, config=None)
    assert result["status"] == "investigating"
    assert captured["action"] == "self-issue-capture"
    assert captured["payload"]["description"] == "页面 在上传后崩溃"
    assert "self-issue-capture" in SELF_ISSUE_ACTIONS
    assert captured["payload"]["portable_session"] is True
    assert captured["payload"]["direct_report"] is True



def test_canonical_intake_command_alias_delegates_to_intake_action(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    def fake_execute(self, **kwargs):
        captured.update(kwargs)
        return {"ok": True, "status": "intake_saved"}

    monkeypatch.setattr(
        "zf.integrations.feishu.issue_adapter.ControlledActionService.execute",
        fake_execute,
    )
    envelope = type("E", (), {
        "command": "self-issue-intake-save", "args": ["sii-1", "3"],
        "form_values": {"answers": json.dumps({"title": "canonical"})},
        "attachments": [], "user_id": "ou_owner", "chat_id": "oc_project",
        "message_id": "m-canonical", "idempotency_key": "k-canonical",
    })()
    result = handle_issue_command(envelope, state_dir=tmp_path, config=None)
    assert result["status"] == "intake_saved"
    assert captured["action"] == "self-issue-intake-save"
    assert captured["payload"]["intake_id"] == "sii-1"


def test_text_intake_save_accepts_json_answers_argument(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    def fake_execute(self, **kwargs):
        captured.update(kwargs)
        return {"ok": True, "status": "intake_saved"}

    monkeypatch.setattr(
        "zf.integrations.feishu.issue_adapter.ControlledActionService.execute",
        fake_execute,
    )
    envelope = type("E", (), {
        "command": "self-issue-intake-save",
        "args": ["sii-1", "1", json.dumps({"title": "t", "bug_description": "b"})],
        "form_values": {}, "attachments": [], "user_id": "ou_owner",
        "chat_id": "oc_project", "message_id": "m-json", "idempotency_key": "k-json",
    })()
    result = handle_issue_command(envelope, state_dir=tmp_path, config=None)
    assert result["status"] == "intake_saved"
    assert captured["payload"]["answers"]["bug_description"] == "b"


def test_self_issue_intake_card_contains_text_save_and_submit_actions() -> None:
    intake = SelfIssueIntake(
        intake_id="sii-1",
        subject_scope="zaofu",
        target_binding={"provider": "gitlab", "project": "acme/demo"},
        attachment_refs=[{"attachment_id": "att-1"}],
    )
    card = build_self_issue_card(
        intake,
        kind="intake",
        questions=[
            {"id": "title", "title": "标题", "input_kind": "text", "required": True},
            {"id": "environment", "title": "环境", "input_kind": "environment"},
        ],
        answers={"title": "已有标题", "environment": {"os": "Linux", "version": "6"}},
    )
    rendered = json.dumps(card, ensure_ascii=False)
    assert "/zf self-issue-intake-save sii-1 1" in rendered
    assert "self-issue-submit:sii-1:1" in rendered
    assert "Intake ID：sii-1" in rendered
    assert "/zf self-issue-intake-save sii-1 1" in rendered
    assert "/zf self-issue-intake-attachment-add sii-1" in rendered


def test_feishu_new_intake_projects_to_editable_card(tmp_path: Path) -> None:
    envelope = type("E", (), {
        "command": "self-issue-intake-start",
        "args": ["上传文件后崩溃"],
        "form_values": {},
        "attachments": [],
        "user_id": "ou_owner",
        "chat_id": "oc_project",
        "message_id": "m-intake",
        "idempotency_key": "k-intake",
    })()
    started = handle_issue_command(envelope, state_dir=tmp_path, config=ZfConfig(self_issue=SelfIssueConfig(enabled=True)))
    assert started["status"] == "intake_collecting"
    intake = SelfIssueIntakeStore(tmp_path / "self-issues" / "intakes.json").latest()
    assert intake is not None

    transport = MockFeishuTransport()
    projected = push_self_issue_cards_once(tmp_path, transport, receive_id="oc_project")
    assert len(projected["sent"]) == 1
    card = json.loads(transport.sent_messages[0].content)
    assert "/zf self-issue-intake-save" in json.dumps(card)
    assert "self-issue-submit:" in json.dumps(card)


def test_signed_self_issue_card_projection_is_idempotent_until_token_expiry(
    tmp_path: Path,
) -> None:
    intake = SelfIssueIntake(
        intake_id="sii-signed",
        subject_scope="zaofu",
        target_binding={"provider": "gitlab", "project": "acme/demo"},
    )
    SelfIssueIntakeStore(tmp_path / "self-issues" / "intakes.json").save(intake)
    transport = MockFeishuTransport()
    first = push_self_issue_cards_once(
        tmp_path,
        transport,
        receive_id="oc_project",
        action_secret=b"test-secret",
        action_ttl_seconds=100,
        now=1000,
    )
    second = push_self_issue_cards_once(
        tmp_path,
        transport,
        receive_id="oc_project",
        action_secret=b"test-secret",
        action_ttl_seconds=100,
        now=1001,
    )
    expired = push_self_issue_cards_once(
        tmp_path,
        transport,
        receive_id="oc_project",
        action_secret=b"test-secret",
        action_ttl_seconds=100,
        now=1101,
    )
    assert len(first["sent"]) == 1
    assert second["sent"] == [] and second["updated"] == []
    assert len(expired["updated"]) == 1


def test_feishu_intake_submit_promotes_draft_and_switches_projection(tmp_path: Path) -> None:
    new_envelope = type("E", (), {
        "command": "self-issue-intake-start",
        "args": ["上传后崩溃"],
        "form_values": {},
        "attachments": [],
        "user_id": "ou_owner",
        "chat_id": "oc_project",
        "message_id": "m-promote-new",
        "idempotency_key": "k-promote-new",
    })()
    started = handle_issue_command(new_envelope, state_dir=tmp_path, config=ZfConfig(self_issue=SelfIssueConfig(enabled=True)))
    assert started["status"] == "intake_collecting"
    intake = SelfIssueIntakeStore(tmp_path / "self-issues" / "intakes.json").latest()
    assert intake is not None
    answers = {
        "title": "上传后崩溃",
        "bug_description": "上传文件后页面崩溃",
        "reproduction_steps": "1. 打开页面 2. 上传文件",
        "expected_behavior": "上传成功",
        "attachments_context": "无",
        "environment": {"os": "Linux", "version": "6"},
        "zaofu_version": "0.0.1",
        "additional_context": "",
    }
    submit_envelope = type("E", (), {
        "command": "self-issue-submit",
        "args": [intake.intake_id, str(intake.revision)],
        "form_values": {"answers": json.dumps(answers, ensure_ascii=False)},
        "attachments": [],
        "user_id": "ou_owner",
        "chat_id": "oc_project",
        "message_id": "m-promote-submit",
        "idempotency_key": "k-promote-submit",
    })()
    submitted = handle_issue_command(submit_envelope, state_dir=tmp_path, config=ZfConfig(self_issue=SelfIssueConfig(enabled=True)))
    assert submitted["status"] == "draft_collecting_evidence"
    draft = IssueDraftStore(tmp_path / "self-issues" / "drafts.json").latest()
    assert draft is not None
    assert submitted["draft"]["draft_id"] == draft.draft_id
    transport = MockFeishuTransport()
    projected = push_self_issue_cards_once(tmp_path, transport, receive_id="oc_project")
    assert projected["sent"] == [f"self-issue-draft-{draft.draft_id}"]
    assert "self-issue-evidence-start:" in transport.sent_messages[0].content
    assert "请先完成证据评估" in transport.sent_messages[0].content


def test_feishu_attachment_download_and_disclosure_gate_promotes_draft(
    tmp_path: Path,
) -> None:
    """Exercise the real Feishu resource -> Intake sidecar -> submit boundary."""
    started = handle_issue_command(
        type("E", (), {
            "command": "self-issue-intake-start", "args": ["截图后崩溃"],
            "form_values": {}, "attachments": [], "user_id": "ou_owner",
            "chat_id": "oc_project", "message_id": "m-attach-new",
            "idempotency_key": "k-attach-new",
        })(),
        state_dir=tmp_path, config=ZfConfig(self_issue=SelfIssueConfig(enabled=True)),
    )
    assert started["status"] == "intake_collecting"
    intake_store = SelfIssueIntakeStore(tmp_path / "self-issues" / "intakes.json")
    intake = intake_store.latest()
    assert intake is not None

    # A valid 1x1 PNG lets the core sanitizer prove that bytes, not a provider
    # URL, reached the atomic sidecar writer.
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    transport = MockFeishuTransport()
    transport.resources[("m-attach", "file-png")] = (png, "application/octet-stream")
    added = handle_issue_command(
        type("E", (), {
            "command": "self-issue-attachment-add", "args": [intake.intake_id],
            "form_values": {"filename": "screen.png"},
            "attachments": [{"resource_key": "file-png", "resource_type": "file", "filename": "screen.png"}],
            "user_id": "ou_owner", "chat_id": "oc_project",
            "message_id": "m-attach", "idempotency_key": "k-attach",
        })(),
        state_dir=tmp_path, config=ZfConfig(self_issue=SelfIssueConfig(enabled=True)), transport=transport,
    )
    assert added["status"] == "attachment_added"
    intake = intake_store.get(intake.intake_id)
    assert intake is not None and len(intake.attachment_refs) == 1
    sidecar = tmp_path / str(intake.attachment_refs[0]["ref"])
    assert sidecar.is_file() and sidecar.read_bytes()

    answers = {
        "title": "截图后崩溃", "bug_description": "上传截图后页面崩溃",
        "reproduction_steps": "1. 打开页面 2. 上传截图", "expected_behavior": "上传成功",
        "attachments_context": "页面截图，允许分诊人员查看", "environment": {"os": "Linux", "version": "6"},
        "zaofu_version": "0.0.1", "additional_context": "",
    }
    blocked = handle_issue_command(
        type("E", (), {
            "command": "self-issue-submit", "args": [intake.intake_id, str(intake.revision)],
            "form_values": {"answers": json.dumps(answers, ensure_ascii=False)},
            "attachments": [], "user_id": "ou_owner", "chat_id": "oc_project",
            "message_id": "m-attach-submit-1", "idempotency_key": "k-attach-submit-1",
        })(), state_dir=tmp_path, config=ZfConfig(self_issue=SelfIssueConfig(enabled=True)),
    )
    assert blocked["status"] == "attachment_disclosure_required"
    assert IssueDraftStore(tmp_path / "self-issues" / "drafts.json").latest() is None

    submitted = handle_issue_command(
        type("E", (), {
            "command": "self-issue-submit", "args": [intake.intake_id, str(intake.revision)],
            "form_values": {
                "answers": json.dumps(answers, ensure_ascii=False),
                "attachment_disclosure_confirmed": "true",
            }, "attachments": [], "user_id": "ou_owner", "chat_id": "oc_project",
            "message_id": "m-attach-submit-2", "idempotency_key": "k-attach-submit-2",
        })(), state_dir=tmp_path, config=ZfConfig(self_issue=SelfIssueConfig(enabled=True)),
    )
    assert submitted["status"] == "draft_collecting_evidence"
    draft = IssueDraftStore(tmp_path / "self-issues" / "drafts.json").latest()
    assert draft is not None and len(draft.attachment_refs) == 1


def test_feishu_video_resource_requires_explicit_disclosure_confirmation(
    tmp_path: Path,
) -> None:
    started = handle_issue_command(
        type("E", (), {
            "command": "self-issue-intake-start", "args": ["视频复现"], "form_values": {},
            "attachments": [], "user_id": "ou_owner", "chat_id": "oc_project",
            "message_id": "m-video-new", "idempotency_key": "k-video-new",
        })(), state_dir=tmp_path, config=ZfConfig(self_issue=SelfIssueConfig(enabled=True)),
    )
    intake = SelfIssueIntakeStore(tmp_path / "self-issues" / "intakes.json").latest()
    assert started["status"] == "intake_collecting" and intake is not None
    # The core only needs the ISO BMFF ftyp marker for this boundary test;
    # provider metadata is intentionally not trusted as evidence.
    video = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
    transport = MockFeishuTransport()
    transport.resources[("m-video", "video-key")] = (video, "video/mp4")

    def send(confirm: str, key: str) -> dict:
        return handle_issue_command(
            type("E", (), {
                "command": "self-issue-attachment-add", "args": [intake.intake_id],
                "form_values": {
                    "filename": "repro.mp4",
                    "video_disclosure_confirmed": confirm,
                },
                "attachments": [{"resource_key": "video-key", "resource_type": "file", "filename": "repro.mp4"}],
                "user_id": "ou_owner", "chat_id": "oc_project", "message_id": "m-video",
                "idempotency_key": key,
            })(), state_dir=tmp_path, config=ZfConfig(self_issue=SelfIssueConfig(enabled=True)), transport=transport,
        )

    rejected = send("false", "k-video-reject")
    assert rejected["status"] == "invalid_self_issue_request"
    assert "public-disclosure" in rejected["reason"]
    accepted = send("true", "k-video-accept")
    assert accepted["status"] == "attachment_added"
    refreshed = SelfIssueIntakeStore(tmp_path / "self-issues" / "intakes.json").get(intake.intake_id)
    assert refreshed is not None and refreshed.attachment_refs[0]["content_type"] == "video/mp4"


def test_self_issue_card_exposes_separate_confirm_publish_and_recovery_steps() -> None:
    draft = IssueDraft(
        draft_id="sid-1",
        subject_scope="zaofu",
        target_binding={"provider": "gitlab", "project": "acme/demo"},
        incident_fingerprint="fp-1",
        title="A reproducible bug",
        summary="summary",
        evidence_status="completed",
        publication_state="previewed",
    )
    batch = PublicationBatch(
        batch_id="pubb-1",
        draft_id=draft.draft_id,
        draft_revision=draft.revision,
        publication_mode="gitlab",
        selected_providers=["gitlab"],
        intent_ids={"gitlab": "pub-1"},
        payload_digest="digest-1",
    )

    previewed = build_self_issue_card(draft, kind="draft", batch=batch)
    assert "self-issue-confirm:pubb-1:digest-1" in json.dumps(previewed)

    batch.status = "confirmed"
    batch.confirmation_id = "confirmation-1"
    draft.publication_state = "confirmed"
    confirmed = build_self_issue_card(draft, kind="draft", batch=batch)
    assert "self-issue-publish:pubb-1:confirmation-1" in json.dumps(confirmed)

    batch.status = "outcome_unknown"
    draft.publication_state = "outcome_unknown"
    unknown = build_self_issue_card(draft, kind="draft", batch=batch)
    assert "self-issue-recover:pub-1" in json.dumps(unknown)

    draft.publication_state = "published"
    draft.published_issue_ref = {
        "provider": "gitlab", "project": "acme/demo", "number": "42",
        "url": "https://gitlab.example/acme/demo/-/issues/42",
    }
    published = build_self_issue_card(draft, kind="draft", batch=batch)
    assert "https://gitlab.example/acme/demo/-/issues/42" in json.dumps(published)


def test_self_issue_publication_card_arguments_reach_controlled_action(
    monkeypatch, tmp_path: Path,
) -> None:
    calls: list[dict] = []

    def fake_execute(self, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "status": "accepted"}

    monkeypatch.setattr(
        "zf.integrations.feishu.issue_adapter.ControlledActionService.execute",
        fake_execute,
    )
    for command, args in (
        ("self-issue-confirm", ["pubb-1", "digest-1"]),
        ("self-issue-publish", ["pubb-1", "confirmation-1"]),
        ("self-issue-recover", ["pub-1"]),
    ):
        envelope = type("E", (), {
            "command": command,
            "args": args,
            "form_values": {},
            "attachments": [],
            "user_id": "ou_approver",
            "chat_id": "oc_project",
            "message_id": f"m-{command}",
            "idempotency_key": f"k-{command}",
        })()
        assert handle_issue_command(
            envelope,
            state_dir=tmp_path,
            config=None,
        )["ok"] is True

    assert calls[0]["payload"]["batch_id"] == "pubb-1"
    assert calls[0]["payload"]["payload_digest"] == "digest-1"
    assert calls[1]["payload"]["confirmation_id"] == "confirmation-1"
    assert calls[2]["payload"]["intent_id"] == "pub-1"


def test_feishu_publication_batch_provider_mock_round_trip(monkeypatch, tmp_path: Path) -> None:
    """Preview -> confirm -> publish uses the real service and provider seam."""
    class Provider:
        name = "gitlab"

        def publish(self, request: IssuePublishRequest, *, access_token: str) -> ForgeResult:
            assert access_token == "feishu-test-token"
            return ForgeResult(
                status="published",
                issue=PublishedIssue(
                    provider="gitlab", project=request.project, number="42",
                    url="https://gitlab.example/acme/demo/-/issues/42",
                ),
            )

        def upload_attachment(self, request, *, access_token: str) -> ForgeResult:
            raise AssertionError("attachments are not part of this publication fixture")

        def find_by_marker(self, project: str, marker: str, *, access_token: str):
            return []

    class Secrets:
        def __init__(self) -> None:
            self.values: dict[str, dict[str, str]] = {}

        def put(self, key, value: dict[str, str]) -> None:
            self.values[key.subject] = dict(value)

        def reveal(self, key):
            return self.values.get(key.subject)

        def delete(self, key) -> bool:
            return self.values.pop(key.subject, None) is not None

    event_log = EventLog(tmp_path / "events.jsonl")
    service = SelfIssueService(
        tmp_path, EventWriter(event_log), project_root=tmp_path,
        forge_provider=Provider(), secret_provider=Secrets(),
    )
    draft = IssueDraft(
        draft_id="draft-feishu-pub", subject_scope="zaofu",
        target_binding={"provider": "gitlab", "project": "acme/demo"},
        incident_fingerprint="fp-feishu-pub", title="Feishu publication",
        summary="A provider publication round trip", evidence_status="completed",
        reproduction_status="reproduced", classification="runtime",
    )
    service.drafts.save(draft)
    service.secrets.put(
        service._secret_key({}, draft), {"access_token": "feishu-test-token", "scope": "api"},
    )
    # ControlledActionService constructs the service at the edge; inject the
    # configured fake provider only for this deterministic cross-module test.
    monkeypatch.setattr("zf.runtime.control_actions_self_issue.SelfIssueService", lambda *a, **k: service)

    def envelope(command: str, args: list[str], message_id: str):
        return type("E", (), {
            "command": command, "args": args, "form_values": {}, "attachments": [],
            "user_id": "ou_approver", "chat_id": "oc_project",
            "message_id": message_id, "idempotency_key": message_id,
        })()

    preview = handle_issue_command(
        envelope("self-issue-preview", [draft.draft_id, "1"], "m-pub-preview"),
        state_dir=tmp_path, config=None,
    )
    assert preview["status"] == "previewed"
    batch_store = PublicationBatchStore(tmp_path / "self-issues" / "publication-batches.json")
    batch = batch_store.latest_for_draft(draft.draft_id)
    assert batch is not None and batch.status == "previewed"

    confirmed = handle_issue_command(
        envelope("self-issue-confirm", [batch.batch_id, batch.payload_digest], "m-pub-confirm"),
        state_dir=tmp_path, config=None,
    )
    assert confirmed["status"] == "confirmed"
    batch = batch_store.get(batch.batch_id)
    assert batch is not None and batch.confirmation_id

    published = handle_issue_command(
        envelope("self-issue-publish", [batch.batch_id, batch.confirmation_id], "m-pub-publish"),
        state_dir=tmp_path, config=None,
    )
    assert published["status"] == "published"
    saved = IssueDraftStore(tmp_path / "self-issues" / "drafts.json").get(draft.draft_id)
    assert saved is not None
    assert saved.publication_state == "published"
    assert saved.published_issue_ref["url"].endswith("/issues/42")
