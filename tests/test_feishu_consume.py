"""feishu-S1/S2: inbound ingestion dispatch (message → channel / card.action → handler)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml

from zf.cli.feishu_consume import consume_stream, ingest_feishu_event
from zf.cli.main import main
from zf.core.config.project_context import resolve_project_context
from zf.core.events.log import EventLog
from zf.integrations.feishu.callback_token import sign_action


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ZF_FEISHU_ACTION_TOKEN_SECRET", "sek")
    config = {
        "version": "1.0",
        "project": {"name": "consume-test", "state_dir": ".zf"},
        "roles": [{"name": "dev", "backend": "mock"}],
        "integrations": {
            "feishu_identity": {
                "enabled": True, "require_signed_actions": True,
                "users": {"ou_boss": {"operator": "alice", "level": "approver"}},
            },
            "feishu_routing": {
                "oc_team": {"target": "channel", "channel_id": "ch-dev",
                            "default_member": "dev"},
                "oc_dm": {
                    "target": "kanban_agent",
                    "backend": "fake",
                    "default_member": "zf-product-manager",
                },
            },
        },
    }
    (tmp_path / "zf.yaml").write_text(yaml.dump(config))
    main(["init"])
    return tmp_path


def _events(sd: Path):
    return EventLog(sd / "events.jsonl").read_all()


def _msg(chat_id: str, text: str, message_id: str) -> dict:
    return {
        "type": "message",
        "payload": {"text": text, "message_id": message_id},
        "user_id": "ou_member", "chat_id": chat_id,
    }


# --- message → channel (S1, via channel-post-message ControlledAction) -----

def test_message_to_mapped_channel_posts(project: Path):
    ctx = resolve_project_context()
    r = ingest_feishu_event(_msg("oc_team", "hi @dev", "m1"), context=ctx)
    assert r.get("ok") is True or r.get("status") in ("completed", "recorded")
    posted = [e for e in _events(ctx.state_dir)
              if e.type == "channel.message.posted"]
    assert posted and posted[-1].payload["channel_id"] == "ch-dev"
    assert posted[-1].payload["refs"]["feishu"]["chat_id"] == "oc_team"


def test_unmapped_chat_dropped(project: Path):
    ctx = resolve_project_context()
    r = ingest_feishu_event(_msg("oc_stranger", "hello", "m2"), context=ctx)
    assert r["status"] == "dropped" and r["reason"] == "unmapped_chat"
    assert not [e for e in _events(ctx.state_dir)
                if e.type == "channel.message.posted"]


def test_kanban_agent_route_enters_agent_conversation(project: Path):
    ctx = resolve_project_context()
    r = ingest_feishu_event(_msg("oc_dm", "fix this", "m3"), context=ctx)
    assert r["status"] == "replied"
    assert r["kind"] == "kanban_agent_conversation"
    assert [e for e in _events(ctx.state_dir)
            if e.type == "channel.message.posted"
            and e.payload.get("member_id") == "zf-product-manager"]


def test_inbound_message_idempotent(project: Path):
    ctx = resolve_project_context()
    data = _msg("oc_team", "twice", "m4")
    first = ingest_feishu_event(data, context=ctx)
    second = ingest_feishu_event(data, context=ctx)
    assert second["status"] == "duplicate"
    posted = [e for e in _events(ctx.state_dir)
              if e.type == "channel.message.posted"
              and e.payload.get("message_id") == "m4"]
    assert len(posted) == 1


# --- card.action → handler (S2, gated by B + A2) ---------------------------

def test_card_action_routes_to_handler_and_approves(project: Path):
    ctx = resolve_project_context()
    token = sign_action(b"sek", action="plan-approve", target="P1",
                        chat_id="oc_team", ttl_seconds=100, now=time.time())
    event = {
        "header": {"event_type": "card.action.trigger", "event_id": "e1"},
        "event": {
            "action": {"tag": "button",
                       "value": {"action": "plan-approve:P1", "t": token}},
            "operator": {"operator_id": {"open_id": "ou_boss"}},
            "open_chat_id": "oc_team",
        },
    }
    r = ingest_feishu_event(event, context=ctx)
    assert r.get("ok") is True
    assert [e for e in _events(ctx.state_dir) if e.type == "plan.approved"]


def test_card_action_forged_token_rejected(project: Path):
    ctx = resolve_project_context()
    event = {
        "header": {"event_type": "card.action.trigger", "event_id": "e2"},
        "event": {
            "action": {"tag": "button",
                       "value": {"action": "plan-approve:P1", "t": "zf1.bad.sig"}},
            "operator": {"operator_id": {"open_id": "ou_boss"}},
            "open_chat_id": "oc_team",
        },
    }
    r = ingest_feishu_event(event, context=ctx)
    assert r["status"] == "rejected"
    assert not [e for e in _events(ctx.state_dir) if e.type == "plan.approved"]


# --- stream loop (NDJSON) --------------------------------------------------

def test_consume_stream_counts_by_status(project: Path):
    ctx = resolve_project_context()
    import json
    lines = [
        json.dumps(_msg("oc_team", "a", "s1")),
        json.dumps(_msg("oc_stranger", "b", "s2")),
        "",  # blank skipped
        "{not json",  # bad json
        json.dumps(_msg("oc_dm", "c", "s3")),
    ]
    counts = consume_stream(lines, context=ctx)
    assert counts.get("dropped") == 1
    assert counts.get("bad_json") == 1
    assert counts.get("replied") == 1
    # the channel post returns ok/completed status key
    assert sum(counts.values()) == 4  # blank not counted
