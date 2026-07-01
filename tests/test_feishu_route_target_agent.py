"""feishu P0-2: feishu_routing target=agent (lightweight direct-bind)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zf.cli.feishu_consume import bridge_inbound_message
from zf.cli.main import main
from zf.core.config.loader import ConfigError, _build_integrations
from zf.core.config.project_context import resolve_project_context
from zf.core.events.log import EventLog
from zf.integrations.feishu.routing import resolve_feishu_route
from zf.integrations.feishu.transport import MockFeishuTransport


def test_route_target_agent_resolves_backend_cwd():
    cfg = type("C", (), {"integrations": _build_integrations({"feishu_routing": {
        "oc_x": {"target": "agent", "backend": "claude-code", "cwd": "/repo"}}})})()
    r = resolve_feishu_route(cfg, "oc_x")
    assert r.target == "agent" and r.backend == "claude-code" and r.cwd == "/repo"


def test_agent_requires_backend():
    with pytest.raises(ConfigError, match="target=agent requires"):
        _build_integrations({"feishu_routing": {"oc_x": {"target": "agent"}}})


def test_wildcard_route_serves_unmapped_p2p_chat():
    # group has an explicit route; single/p2p chats (their own chat_id) fall to "*"
    cfg = type("C", (), {"integrations": _build_integrations({"feishu_routing": {
        "oc_group": {"target": "agent", "backend": "codex", "cwd": "/repoA"},
        "*": {"target": "agent", "backend": "claude-code", "cwd": "/repoB"}}})})()
    # exact match wins
    assert resolve_feishu_route(cfg, "oc_group").backend == "codex"
    # unmapped p2p chat_id falls to the wildcard default
    assert resolve_feishu_route(cfg, "oc_p2p_unmapped").backend == "claude-code"


def test_no_wildcard_still_fails_closed():
    cfg = type("C", (), {"integrations": _build_integrations({"feishu_routing": {
        "oc_group": {"target": "agent", "backend": "codex"}}})})()
    assert resolve_feishu_route(cfg, "oc_other") is None


def test_direct_bind_provisions_member_and_replies(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(yaml.dump({
        "version": "1.0", "project": {"name": "t", "state_dir": ".zf"},
        "roles": [{"name": "dev", "backend": "mock"}],
        "integrations": {"feishu_routing": {
            "oc_x": {"target": "agent", "backend": "fake",
                     "channel_id": "ch-direct", "default_member": "dev-agent"}}}}))
    main(["init"])
    ctx = resolve_project_context()
    event = MockFeishuTransport().parse_webhook({
        "type": "message",
        "payload": {"text": "@dev-agent hi", "message_id": "m1"},
        "user_id": "ou_u", "chat_id": "oc_x"})
    r = bridge_inbound_message(event, context=ctx)

    assert r["status"] == "replied" and r["target"] == "agent"
    events = EventLog(ctx.state_dir / "events.jsonl").read_all()
    # the bridge auto-provisioned the member (no manual channel/member setup)
    invited = [e for e in events if e.type == "channel.member.invited"
               and e.payload.get("member_id") == "dev-agent"]
    assert invited and invited[0].payload["backend"] == "fake"
    # and a real agent reply was produced through the channel path
    assert [e for e in events if e.type == "channel.message.posted"
            and e.payload.get("member_id") == "dev-agent"]


def test_direct_bind_does_not_reinvite_on_second_message(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(yaml.dump({
        "version": "1.0", "project": {"name": "t", "state_dir": ".zf"},
        "roles": [{"name": "dev", "backend": "mock"}],
        "integrations": {"feishu_routing": {
            "oc_x": {"target": "agent", "backend": "fake",
                     "channel_id": "ch-d2", "default_member": "a1"}}}}))
    main(["init"])
    ctx = resolve_project_context()

    def _msg(mid):
        return MockFeishuTransport().parse_webhook({
            "type": "message", "payload": {"text": "@a1 hi", "message_id": mid},
            "user_id": "ou_u", "chat_id": "oc_x"})

    bridge_inbound_message(_msg("m1"), context=ctx)
    bridge_inbound_message(_msg("m2"), context=ctx)
    events = EventLog(ctx.state_dir / "events.jsonl").read_all()
    invited = [e for e in events if e.type == "channel.member.invited"
               and e.payload.get("member_id") == "a1"]
    assert len(invited) == 1  # provisioned once, not per message
