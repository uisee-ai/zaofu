"""feishu-C: Channel delivery projector + Interrupt callback.

Covers backlog acceptance #3 (Working → update in place to terminal), #4
(Interrupt → agent.session.run.cancelled, card Interrupted, no tmux/pid), and
#5 (high-frequency streaming does not spam the card).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zf.cli.feishu import _handle_event_data
from zf.cli.main import main
from zf.core.config.project_context import resolve_project_context
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.integrations.feishu.delivery_card import sync_delivery_cards


def _writer(state_dir: Path) -> EventWriter:
    return EventWriter(EventLog(state_dir / "events.jsonl"))


def _emit(writer: EventWriter, etype: str, payload: dict) -> None:
    writer.append(ZfEvent(type=etype, actor="test", payload=payload))


def _sync(state_dir, ledger, sent, updated):
    return sync_delivery_cards(
        state_dir,
        send_card=lambda c: (sent.append(c), f"msg-{len(sent)}")[1],
        update_card=lambda mid, c: updated.append((mid, c)),
        ledger=ledger,
    )


# --- delivery projector folding (acceptance #3, #5) ------------------------

def test_working_card_sent_once_then_updated_in_place(tmp_path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    w = _writer(state_dir)
    _emit(w, "channel.agent.reply.requested",
          {"request_id": "reply-1", "member_id": "dev", "provider": "claude-code"})
    ledger: dict = {}
    sent, updated = [], []
    r1 = _sync(state_dir, ledger, sent, updated)
    assert r1["sent"] == ["reply-1"] and not updated
    assert "⏹️ Interrupt" in str(sent[0])  # working card carries Interrupt

    # rerun: no resend (idempotent), still working
    r2 = _sync(state_dir, ledger, sent, updated)
    assert r2["sent"] == [] and r2["updated"] == []

    # completion → update the SAME message, no new send
    _emit(w, "channel.agent.reply.completed", {"request_id": "reply-1"})
    r3 = _sync(state_dir, ledger, sent, updated)
    assert r3["updated"] == ["reply-1"]
    assert updated[0][0] == "msg-1"
    assert "✅ Done" in str(updated[0][1])

    # terminal is sticky: rerun does not re-update
    r4 = _sync(state_dir, ledger, sent, updated)
    assert r4["updated"] == []


def test_deltas_do_not_spam_the_card(tmp_path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    w = _writer(state_dir)
    _emit(w, "channel.agent.reply.started", {"request_id": "reply-2"})
    for i in range(50):
        _emit(w, "agent.session.part.delta", {"request_id": "reply-2", "seq": i})
    ledger: dict = {}
    sent, updated = [], []
    r = _sync(state_dir, ledger, sent, updated)
    # one Working card, zero updates despite 50 deltas
    assert r["sent"] == ["reply-2"]
    assert len(sent) == 1 and not updated


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
    assert "⏹️ Interrupted" in str(updated[0][1])


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
    assert "✅ Done" in str(sent[0]) and "Interrupt" not in str(sent[0])


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
