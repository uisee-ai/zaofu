"""feishu -> kanban_agent inbound routes every message to the agent."""

from __future__ import annotations

from pathlib import Path

import yaml

from zf.cli.feishu_consume import bridge_inbound_message
from zf.cli.main import main
from zf.core.config.project_context import resolve_project_context
from zf.core.events.log import EventLog
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


def _event(text, mid="m1"):
    return MockFeishuTransport().parse_webhook({
        "type": "message", "payload": {"text": text, "message_id": mid},
        "user_id": "ou_owner", "chat_id": "oc_km"})


def _intent_created(state_dir):
    return [e for e in EventLog(state_dir / "events.jsonl").read_all()
            if e.type == "operator.intent.created"]


def test_status_text_enters_agent_conversation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = _project(tmp_path)
    before = len(EventLog(ctx.state_dir / "events.jsonl").read_all())
    r = bridge_inbound_message(_event("项目当前状态如何?"), context=ctx)
    assert r["status"] == "replied" and r["kind"] == "kanban_agent_conversation"
    assert not _intent_created(ctx.state_dir)
    after = len(EventLog(ctx.state_dir / "events.jsonl").read_all())
    assert after > before
    channel = project_channel(ctx.state_dir, "feishu-kanban_agent-oc_km") or {}
    member = next(
        member for member in channel.get("members", [])
        if member.get("member_id") == "zf-product-manager"
    )
    assert member["channel_role"] == "owner_delegate"
    assert member["permission_profile"] == "dangerous_full"


def test_action_text_also_enters_agent_conversation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = _project(tmp_path)
    r = bridge_inbound_message(_event("帮我重启 runtime dev-1"), context=ctx)
    assert r["status"] == "replied" and r["kind"] == "kanban_agent_conversation"
    assert not _intent_created(ctx.state_dir)


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
