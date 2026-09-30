"""feishu -> kanban_agent inbound routes every message to the agent."""

from __future__ import annotations

from pathlib import Path

import yaml

from zf.cli.feishu_consume import bridge_inbound_message
from zf.cli.main import main
from zf.core.config.project_context import resolve_project_context
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.integrations.feishu.agent_conversation import (
    _latest_assistant_reply_text,
)
from zf.integrations.feishu.transport import MockFeishuTransport
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_sidecar import hydrate_channel_message_text


def _project(tmp_path: Path):
    (tmp_path / "zf.yaml").write_text(yaml.dump({
        "version": "1.0", "project": {"name": "t", "state_dir": ".zf"},
        "roles": [{"name": "dev", "backend": "mock"}],
        "integrations": {"feishu_routing": {
            "oc_km": {
                "target": "kanban_agent",
                "backend": "fake",
                "default_member": "zf-product-manager",
            }}}}))
    main(["init"])
    return resolve_project_context()


def _event(text, mid="m1", **refs):
    return MockFeishuTransport().parse_webhook({
        "type": "message",
        "payload": {"text": text, "message_id": mid, **refs},
        "user_id": "ou_owner", "chat_id": "oc_km"})


def _intent_created(state_dir):
    return [e for e in EventLog(state_dir / "events.jsonl").read_all()
            if e.type == "operator.intent.created"]


def test_status_text_uses_canonical_projection_without_provider_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = _project(tmp_path)
    writer = EventWriter(EventLog(ctx.state_dir / "events.jsonl"))
    writer.emit(
        "channel.created",
        actor="test",
        payload={
            "channel_id": "ch-prd",
            "name": "Feishu PRD",
        },
    )
    writer.emit(
        "channel.discussion.started",
        actor="test",
        correlation_id="ch-prd",
        payload={"channel_id": "ch-prd", "thread_id": "main", "source": "test"},
    )
    before = len(EventLog(ctx.state_dir / "events.jsonl").read_all())
    r = bridge_inbound_message(_event("项目当前状态如何?"), context=ctx)
    assert r["status"] == "replied" and r["kind"] == "kanban_agent_canonical_status"
    assert r["reply_mode"] == "deterministic"
    assert not _intent_created(ctx.state_dir)
    after = len(EventLog(ctx.state_dir / "events.jsonl").read_all())
    assert after > before
    channel = project_channel(ctx.state_dir, "feishu-kanban_agent-oc_km") or {}
    member = next(
        member for member in channel.get("members", [])
        if member.get("member_id") == "zf-product-manager"
    )
    assert member["channel_role"] == "owner_delegate"
    assert member["permission_profile"] == "read_only"
    events = EventLog(ctx.state_dir / "events.jsonl").read_all()
    replies = [
        event for event in events
        if event.type == "channel.message.posted"
        and event.payload.get("role") == "assistant"
    ]
    assert replies
    reply = hydrate_channel_message_text(ctx.state_dir, replies[-1].payload)
    assert "ch-prd" in reply
    assert "已启动讨论" in reply
    assert not [
        event for event in events
        if event.type.startswith("agent.session.run.")
    ]
    assert not [
        event for event in events
        if event.type == "operator.action.proposed"
    ]


def test_kanban_detail_status_queries_use_provider_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = _project(tmp_path)
    from zf.integrations.feishu import agent_conversation

    original = agent_conversation.run_specialist_conversation
    deterministic_replies = []

    def _record_provider_route(**kwargs):
        deterministic_replies.append(kwargs.get("deterministic_reply"))
        return original(**kwargs)

    monkeypatch.setattr(
        agent_conversation,
        "run_specialist_conversation",
        _record_provider_route,
    )

    for index, text in enumerate((
        "当前任务进度如何，合同、时间线、worker状态也都一起汇报一下",
        "你汇报一下当前kanban上的任务啊",
    ), start=1):
        result = bridge_inbound_message(
            _event(text, f"m-detail-{index}"),
            context=ctx,
        )
        assert result["status"] == "replied"
        assert result["kind"] == "kanban_agent_conversation"
        assert result["reply_mode"] == "provider"

    assert deterministic_replies == [None, None]


def test_action_text_also_enters_agent_conversation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = _project(tmp_path)
    r = bridge_inbound_message(_event("帮我重启 runtime dev-1"), context=ctx)
    assert r["status"] == "replied" and r["kind"] == "kanban_agent_conversation"
    assert not _intent_created(ctx.state_dir)


def test_mentioned_zf_command_uses_controlled_issue_gateway(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = _project(tmp_path)
    captured: dict = {}

    def fake_handle(data, **kwargs):
        captured.update(data)
        return {"status": "intake_collecting"}

    monkeypatch.setattr("zf.cli.feishu._handle_event_data", fake_handle)
    result = bridge_inbound_message(
        _event("@_user_1  Agent /zf self-issue new Feishu附件验收", "m-command"),
        context=ctx,
    )
    assert result["status"] == "intake_collecting"
    assert captured["payload"]["text"] == "/zf self-issue new Feishu附件验收"


def test_issue_shorthand_uses_controlled_issue_gateway(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = _project(tmp_path)
    captured: dict = {}

    def fake_handle(data, **kwargs):
        captured.update(data)
        return {"status": "intake_collecting"}

    monkeypatch.setattr("zf.cli.feishu._handle_event_data", fake_handle)
    result = bridge_inbound_message(
        _event(
            "/issue 基础 wheel 导入失败",
            "m-issue",
            attachments=[{"resource_key": "report", "filename": "error.log"}],
        ),
        context=ctx,
    )
    assert result["status"] == "intake_collecting"
    assert captured["payload"]["text"] == "/zf self-issue-new '基础 wheel 导入失败'"
    assert captured["payload"]["attachments"][0]["resource_key"] == "report"


def test_feishu_thread_ref_is_preserved_in_channel_conversation(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    ctx = _project(tmp_path)

    result = bridge_inbound_message(
        _event("线程内继续", "m-thread", root_message_id="om-root"),
        context=ctx,
    )

    assert result["thread_id"] == "om-root"
    messages = [
        event for event in EventLog(ctx.state_dir / "events.jsonl").read_all()
        if event.type == "channel.message.posted"
        and event.payload.get("message_id") == "m-thread"
    ]
    assert messages[-1].payload["thread_id"] == "om-root"


def test_proposal_extraction_reads_reply_from_the_same_feishu_thread(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    ctx = _project(tmp_path)
    writer = EventWriter(EventLog(ctx.state_dir / "events.jsonl"))
    writer.emit(
        "channel.created",
        actor="test",
        payload={"channel_id": "ch-threaded", "name": "threaded"},
    )
    for thread_id, text in (
        ("root-a", "proposal from A"),
        ("root-b", "proposal from B"),
    ):
        writer.emit(
            "channel.message.posted",
            actor="test",
            correlation_id="ch-threaded",
            payload={
                "channel_id": "ch-threaded",
                "thread_id": thread_id,
                "member_id": "zf-product-manager",
                "role": "assistant",
                "text": text,
            },
        )

    assert _latest_assistant_reply_text(
        ctx.state_dir,
        channel_id="ch-threaded",
        member_id="zf-product-manager",
        thread_id="root-a",
        after_ts="",
    ) == "proposal from A"


def test_explicit_dangerous_route_reaches_specialist_for_allowed_sender(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(yaml.dump({
        "version": "1.0",
        "project": {"name": "t", "state_dir": ".zf"},
        "roles": [{"name": "dev", "backend": "mock"}],
        "integrations": {"feishu_routing": {
            "oc_km": {
                "target": "kanban_agent",
                "backend": "fake",
                "default_member": "zf-product-manager",
                "permission_profile": "dangerous_full",
                "dangerous_ack": True,
                "allowed_senders": ["ou_owner"],
            },
        }},
    }))
    main(["init"])
    ctx = resolve_project_context()

    result = bridge_inbound_message(_event("执行开发", "m-danger"), context=ctx)

    assert result["status"] == "replied"
    assert result["permission_profile"] == "dangerous_full"
    channel = project_channel(ctx.state_dir, "feishu-kanban_agent-oc_km") or {}
    member = next(
        item for item in channel.get("members", [])
        if item.get("member_id") == "zf-product-manager"
    )
    assert member["permission_profile"] == "dangerous_full"


def test_dedup_kanban_inbound(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = _project(tmp_path)
    bridge_inbound_message(_event("重启 runtime", "mX"), context=ctx)
    r2 = bridge_inbound_message(_event("重启 runtime", "mX"), context=ctx)
    assert r2["status"] == "duplicate"
    messages = [
        event for event in EventLog(ctx.state_dir / "events.jsonl").read_all()
        if event.type == "channel.message.posted"
        and event.payload.get("message_id") == "mX"
        and event.payload.get("role") == "user"
    ]
    assert len(messages) == 1


def test_kanban_inbound_message_body_is_sidecar_backed(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    ctx = _project(tmp_path)
    long_text = "请分析当前项目状态。" + ("补充上下文 " * 500)

    r = bridge_inbound_message(_event(long_text, "m-long"), context=ctx)

    assert r["status"] == "replied"
    messages = [
        event for event in EventLog(ctx.state_dir / "events.jsonl").read_all()
        if event.type == "channel.message.posted"
        and event.payload.get("message_id") == "m-long"
        and event.payload.get("role") == "user"
    ]
    assert len(messages) == 1
    payload = messages[0].payload
    assert payload["body_ref"]
    assert payload["body_byte_count"] > len(payload["text"])
    assert long_text not in payload["text"]
    assert hydrate_channel_message_text(ctx.state_dir, payload) == long_text
