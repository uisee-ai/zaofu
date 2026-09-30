"""feishu-C: Channel delivery projector + Interrupt callback.

Covers backlog acceptance #3 (Working → update in place to terminal), #4
(Interrupt → agent.session.run.cancelled, card Interrupted, no tmux/pid), and
#5 (high-frequency streaming does not spam the card).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from zf.cli.feishu import _handle_event_data
from zf.cli.main import main
from zf.core.config.project_context import resolve_project_context
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.integrations.feishu.delivery_card import (
    build_delivery_card,
    push_delivery_cards_once,
    sync_delivery_cards,
)
from zf.integrations.feishu.transport import MockFeishuTransport
from zf.runtime.channel_sidecar import channel_message_event_payload


def test_delivery_reply_collapses_complete_body_without_hiding_stop_control():
    reply = "完整回复：\n\n```python\nprint('hello')\n```\n" + "详情。" * 500
    done = build_delivery_card({"status": "done", "reply_text": reply})
    panel = done["elements"][0]
    assert panel["tag"] == "collapsible_panel"
    assert panel["expanded"] is False
    assert panel["elements"][0]["text"]["content"] == reply
    assert done["config"]["update_multi"] is True
    working = build_delivery_card({"status": "working", "request_id": "reply-test"})
    assert working["elements"][-1]["actions"][0]["value"]["action"] == "agent-cancel:reply-test"


def _writer(state_dir: Path) -> EventWriter:
    return EventWriter(EventLog(state_dir / "events.jsonl"))


def _emit(writer: EventWriter, etype: str, payload: dict) -> None:
    writer.append(ZfEvent(type=etype, actor="test", payload=payload))


def _sync(state_dir, ledger, sent, updated):
    return sync_delivery_cards(
        state_dir,
        send_card=lambda c, state: (sent.append((c, state)), f"msg-{len(sent)}")[1],
        update_card=lambda mid, c: updated.append((mid, c)),
        ledger=ledger,
    )


def _assistant_reply(
    state_dir: Path,
    writer: EventWriter,
    *,
    request_id: str,
    text: str,
    channel_id: str = "ch-feishu",
    member_id: str = "codex-reviewer",
) -> None:
    _emit(writer, "channel.created", {
        "channel_id": channel_id,
        "name": "Feishu review",
        "source": "test",
    })
    _emit(writer, "channel.member.invited", {
        "channel_id": channel_id,
        "member_id": member_id,
        "persona": "Codex Reviewer",
        "display_name": "Codex Reviewer",
        "source": "test",
    })
    payload = channel_message_event_payload(
        state_dir,
        {
            "channel_id": channel_id,
            "thread_id": "om-root",
            "message_id": f"msg-{request_id}-reply",
            "member_id": member_id,
            "role": "assistant",
            "source": "test",
            "text": text,
            "refs": {
                "request_id": request_id,
                "feishu": {
                    "chat_id": "oc-origin",
                    "message_id": "om-user",
                    "thread_id": "om-root",
                    "root_message_id": "om-root",
                },
            },
        },
        created_by="test",
    )
    writer.append(ZfEvent(
        type="channel.message.posted",
        actor=member_id,
        correlation_id=channel_id,
        payload=payload,
    ))


# --- delivery projector folding (acceptance #3, #5) ------------------------

def test_working_card_sent_once_then_updated_in_place(tmp_path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    w = _writer(state_dir)
    _emit(w, "channel.agent.reply.requested",
          {"request_id": "reply-1", "member_id": "dev", "provider": "fake"})
    ledger: dict = {}
    sent, updated = [], []
    r1 = _sync(state_dir, ledger, sent, updated)
    assert r1["sent"] == ["reply-1"] and not updated
    assert "停止回复" in str(sent[0][0])  # working card carries Interrupt

    # rerun: no resend (idempotent), still working
    r2 = _sync(state_dir, ledger, sent, updated)
    assert r2["sent"] == [] and r2["updated"] == []

    # completion → update the SAME message, no new send
    _emit(w, "channel.agent.reply.completed", {"request_id": "reply-1"})
    r3 = _sync(state_dir, ledger, sent, updated)
    assert r3["updated"] == ["reply-1"]
    assert updated[0][0] == "msg-1"
    assert "已回复" in str(updated[0][1])

    # terminal is sticky: rerun does not re-update
    r4 = _sync(state_dir, ledger, sent, updated)
    assert r4["updated"] == []


def test_streaming_deltas_reserve_the_reply_for_the_stream_card(tmp_path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    w = _writer(state_dir)
    _emit(w, "channel.agent.reply.started", {"request_id": "reply-2"})
    for i in range(50):
        _emit(w, "agent.session.part.delta", {"request_id": "reply-2", "seq": i})
    ledger: dict = {}
    sent, updated = [], []
    r = _sync(state_dir, ledger, sent, updated)
    # The stream projector owns a reply once it has emitted deltas. Delivery
    # must not create a second lifecycle card for the same request.
    assert r["sent"] == [] and r["updated"] == []
    assert r["skipped"] == ["reply-2"]
    assert not sent and not updated


def test_cancelled_projects_interrupted(tmp_path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    w = _writer(state_dir)
    _emit(w, "channel.agent.reply.started", {"request_id": "reply-3"})
    ledger: dict = {}
    sent, updated = [], []
    _sync(state_dir, ledger, sent, updated)
    _emit(w, "agent.session.run.cancelled",
          {"request_id": "reply-3", "reason": "operator interrupted from feishu"})
    r = _sync(state_dir, ledger, sent, updated)
    assert r["updated"] == ["reply-3"]
    assert "已停止" in str(updated[0][1])


def test_terminal_state_sticky_against_out_of_order(tmp_path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    w = _writer(state_dir)
    _emit(w, "channel.agent.reply.completed", {"request_id": "reply-4"})
    # a late "started" must not knock it back to working
    _emit(w, "channel.agent.reply.started", {"request_id": "reply-4"})
    ledger: dict = {}
    sent, updated = [], []
    r = _sync(state_dir, ledger, sent, updated)
    # first card sent reflects terminal done, no Interrupt button
    assert r["sent"] == ["reply-4"]
    assert "已回复" in str(sent[0][0]) and "停止回复" not in str(sent[0][0])


def test_terminal_card_hydrates_reply_and_uses_exact_feishu_origin(tmp_path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = _writer(state_dir)
    _emit(writer, "channel.agent.reply.requested", {
        "channel_id": "ch-feishu",
        "thread_id": "om-root",
        "request_id": "reply-readable",
        "target_member_id": "codex-reviewer",
        "member_id": "ou_sender",
    })
    _assistant_reply(
        state_dir,
        writer,
        request_id="reply-readable",
        text="我已完成检查，建议先补充回归测试。",
    )
    _emit(writer, "channel.agent.reply.completed", {
        "channel_id": "ch-feishu",
        "thread_id": "om-root",
        "request_id": "reply-readable",
        "target_member_id": "codex-reviewer",
    })

    transport = MockFeishuTransport()
    result = push_delivery_cards_once(
        state_dir,
        transport,
        receive_id="oc-fallback",
    )

    assert result["sent"] == ["reply-readable"]
    assert len(transport.sent_messages) == 1
    message = transport.sent_messages[0]
    assert (message.chat_id, message.thread_id) == ("oc-origin", "om-root")
    card = json.loads(message.content)
    rendered = json.dumps(card, ensure_ascii=False)
    assert "Codex Reviewer · 已回复" in rendered
    assert "我已完成检查，建议先补充回归测试。" in rendered
    assert "ou_sender" not in rendered
    assert "provider:" not in rendered
    assert "request:" not in rendered


def test_member_scoped_delivery_skips_other_bot_replies(tmp_path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = _writer(state_dir)
    for request_id, member_id in (
        ("reply-kanban", "zf-product-manager"),
        ("reply-run-manager", "run-manager"),
    ):
        _emit(writer, "channel.agent.reply.requested", {
            "channel_id": "ch-feishu",
            "thread_id": "main",
            "request_id": request_id,
            "target_member_id": member_id,
        })
        _assistant_reply(
            state_dir,
            writer,
            request_id=request_id,
            text=f"{member_id} reply",
            member_id=member_id,
        )
        _emit(writer, "channel.agent.reply.completed", {
            "channel_id": "ch-feishu",
            "thread_id": "main",
            "request_id": request_id,
            "target_member_id": member_id,
        })

    ledger: dict = {}
    sent, updated = [], []
    result = sync_delivery_cards(
        state_dir,
        send_card=lambda card, state: (sent.append((card, state)), "msg-run")[1],
        update_card=lambda message_id, card: updated.append((message_id, card)),
        ledger=ledger,
        member="run-manager",
    )

    assert result["sent"] == ["reply-run-manager"]
    assert len(sent) == 1
    assert sent[0][1]["member_id"] == "run-manager"


def test_member_scoped_delivery_ledger_seeds_from_legacy_without_replay(tmp_path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = _writer(state_dir)
    _emit(writer, "channel.agent.reply.requested", {
        "channel_id": "ch-feishu",
        "thread_id": "main",
        "request_id": "reply-legacy",
        "target_member_id": "zf-product-manager",
    })
    _assistant_reply(
        state_dir,
        writer,
        request_id="reply-legacy",
        text="already visible",
        member_id="zf-product-manager",
    )
    _emit(writer, "channel.agent.reply.completed", {
        "channel_id": "ch-feishu",
        "thread_id": "main",
        "request_id": "reply-legacy",
        "target_member_id": "zf-product-manager",
    })
    ledger_dir = state_dir / "integrations" / "feishu"
    ledger_dir.mkdir(parents=True)
    legacy_ledger = {
        "delivery-reply-legacy": {"message_id": "om-existing", "status": "done"},
    }
    (ledger_dir / "delivery_ledger.json").write_text(
        json.dumps(legacy_ledger), encoding="utf-8"
    )

    transport = MockFeishuTransport()
    result = push_delivery_cards_once(
        state_dir,
        transport,
        receive_id="oc-origin",
        member="zf-product-manager",
    )

    assert result["sent"] == []
    assert result["updated"] == []
    assert transport.sent_messages == []
    scoped = json.loads(
        (ledger_dir / "delivery_ledger-zf-product-manager.json").read_text(
            encoding="utf-8"
        )
    )
    assert scoped == legacy_ledger


def test_streamed_reply_does_not_create_delivery_card(tmp_path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = _writer(state_dir)
    _emit(writer, "channel.agent.reply.requested", {
        "request_id": "reply-stream",
        "target_member_id": "codex-reviewer",
        "backend": "codex",
    })
    # Real bridge ordering: Delivery can tick after reply.requested but before
    # the stream-json backend emits its first delta.  That tick must reserve
    # the reply for Stream instead of sending a Working card.
    ledger: dict = {}
    sent, updated = [], []
    initial = _sync(state_dir, ledger, sent, updated)
    assert initial["sent"] == []
    assert initial["skipped"] == ["reply-stream"]
    _emit(writer, "agent.session.part.delta", {
        "request_id": "reply-stream", "kind": "text", "delta": "partial",
    })
    _emit(writer, "channel.agent.reply.completed", {
        "request_id": "reply-stream", "target_member_id": "codex-reviewer",
    })

    result = _sync(state_dir, ledger, sent, updated)

    assert result["sent"] == []
    assert result["updated"] == []
    assert result["skipped"] == ["reply-stream"]


# --- Interrupt callback (acceptance #4, gated by feishu-B) ------------------

@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = {
        "version": "1.0",
        "project": {"name": "feishu-c-test", "state_dir": ".zf"},
        "roles": [{"name": "dev", "backend": "mock"}],
        "integrations": {
            "feishu_identity": {
                "enabled": True,
                "users": {"ou_op": {"operator": "alice", "level": "operator"}},
            }
        },
    }
    (tmp_path / "zf.yaml").write_text(yaml.dump(config))
    main(["init"])
    return tmp_path


def _button(action: str, user_id: str, message_id: str) -> dict:
    return {
        "type": "button_action",
        "payload": {"action": action, "message_id": message_id},
        "user_id": user_id,
        "chat_id": "c1",
    }


def test_interrupt_button_emits_cancelled_no_tmux(project: Path):
    ctx = resolve_project_context()
    result = _handle_event_data(
        _button("agent-cancel:reply-9", "ou_op", "i1"),
        context=ctx, user_levels={},
    )
    assert result["ok"] is True and result["status"] == "cancelled"
    events = EventLog(ctx.state_dir / "events.jsonl").read_all()
    cancelled = [e for e in events if e.type == "agent.session.run.cancelled"]
    assert cancelled and cancelled[0].payload["request_id"] == "reply-9"
    assert cancelled[0].payload["source"] == "feishu"
    # headless contract: no pane/pid kill events
    assert not [e for e in events if "pane" in e.type or "pid.kill" in e.type]


def test_interrupt_button_unmapped_user_rejected(project: Path):
    ctx = resolve_project_context()
    result = _handle_event_data(
        _button("agent-cancel:reply-9", "ou_stranger", "i2"),
        context=ctx, user_levels={},
    )
    assert result["status"] == "rejected"
    events = EventLog(ctx.state_dir / "events.jsonl").read_all()
    assert not [e for e in events if e.type == "agent.session.run.cancelled"]
