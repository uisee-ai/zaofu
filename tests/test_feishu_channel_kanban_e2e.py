"""E2E: feishu ↔ channel / kanban-agent over the real event contract.

Credential-free verification (MockFeishuTransport) of the A/B/C/A2 work driving
and consuming real channel/plan events. See tests/e2e/feishu-channel-kanban-e2e-plan.md.

Two levels are exercised:
- real CLI (`main(["feishu","push"/"handle", ...])`) for the wired path + ledgers;
- library closed-loop (push a signed card → extract the issued token from the
  mock transport → click it back) for the round-trip that unit tests can't show.
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
from zf.integrations.feishu.plan_approval_card import push_plan_approval_cards_once
from zf.integrations.feishu.delivery_card import push_delivery_cards_once
from zf.integrations.feishu.transport import MockFeishuTransport

SECRET = "e2e-action-secret"
CHAT = "oc_chat"


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ZF_FEISHU_ACTION_TOKEN_SECRET", SECRET)
    config = {
        "version": "1.0",
        "project": {"name": "feishu-e2e", "state_dir": ".zf"},
        "roles": [{"name": "dev", "backend": "mock"}],
        "integrations": {"feishu_identity": {
            "enabled": True,
            "require_signed_actions": True,
            "users": {
                "ou_boss": {"operator": "alice", "level": "approver"},
                "ou_op": {"operator": "bob", "level": "operator"},
            },
        }},
    }
    (tmp_path / "zf.yaml").write_text(yaml.dump(config))
    main(["init"])
    return tmp_path


def _emit(state_dir: Path, etype: str, payload: dict, actor: str = "test") -> None:
    EventWriter(EventLog(state_dir / "events.jsonl")).append(
        ZfEvent(type=etype, actor=actor, payload=payload))


def _events(state_dir: Path):
    return EventLog(state_dir / "events.jsonl").read_all()


def _button(action: str, user_id: str, message_id: str, token: str = "") -> dict:
    value = {"action": action, "t": token}
    return {
        "type": "button_action",
        "payload": {"action": action, "action_token": token,
                    "action_value": value, "message_id": message_id},
        "user_id": user_id,
        "chat_id": CHAT,
    }


def _token_from_card(transport: MockFeishuTransport, expect_action: str) -> str:
    """Pull the signed action token off the most recent pushed card."""
    card = json.loads(transport.sent_messages[-1].content)
    for element in card.get("elements", []):
        for button in element.get("actions", []) if element.get("tag") == "action" else []:
            value = button.get("value", {})
            if str(value.get("action", "")).startswith(expect_action):
                return str(value.get("t") or "")
    raise AssertionError(f"no signed {expect_action} button in card")


# --- S1: plan approval outbound + ledger idempotency (CLI) -----------------

def test_s1_plan_card_outbound_and_idempotent(project: Path, capsys):
    sd = project / ".zf"
    _emit(sd, "plan.approval.requested", {"plan_id": "P1", "stage_id": "impl"})
    main(["feishu", "push", "--transport", "mock", "--to", CHAT,
          "--state-dir", str(sd)])
    out = capsys.readouterr().out
    assert "plan_cards_sent=1" in out
    ledger = json.loads(
        (sd / "integrations" / "feishu" / "plan_approval_ledger.json").read_text())
    assert ledger["plan-approval-P1"]["state"] == "pending"
    # rerun → idempotent, no resend
    main(["feishu", "push", "--transport", "mock", "--to", CHAT,
          "--state-dir", str(sd)])
    assert "plan_cards_sent=0" in capsys.readouterr().out


# --- S2: signed button round-trip approve (library closed loop) ------------

def test_s2_signed_button_roundtrip_approves(project: Path):
    sd = project / ".zf"
    _emit(sd, "plan.approval.requested", {"plan_id": "P1"})
    t = MockFeishuTransport()
    push_plan_approval_cards_once(sd, t, receive_id=CHAT, action_secret=SECRET.encode())
    token = _token_from_card(t, "plan-approve")
    assert token  # card carried a signed approve button

    ctx = resolve_project_context()
    result = _handle_event_data(
        _button("plan-approve:P1", "ou_boss", "m1", token=token),
        context=ctx, user_levels={})
    assert result["ok"] is True
    approved = [e for e in _events(sd) if e.type == "plan.approved"]
    assert approved and approved[0].payload["plan_id"] == "P1"
    assert approved[0].payload["surface"] == "feishu"
    assert approved[0].actor == "operator"


# --- S3: tamper / privilege / unsigned all rejected ------------------------

def test_s3_tampered_target_rejected(project: Path):
    sd = project / ".zf"
    _emit(sd, "plan.approval.requested", {"plan_id": "P1"})
    t = MockFeishuTransport()
    push_plan_approval_cards_once(sd, t, receive_id=CHAT, action_secret=SECRET.encode())
    token = _token_from_card(t, "plan-approve")
    ctx = resolve_project_context()
    # same token, different target
    result = _handle_event_data(
        _button("plan-approve:P-EVIL", "ou_boss", "m2", token=token),
        context=ctx, user_levels={})
    assert result["status"] == "rejected"
    assert not [e for e in _events(sd) if e.type == "plan.approved"]


def test_s3_operator_below_approver_rejected(project: Path):
    sd = project / ".zf"
    _emit(sd, "plan.approval.requested", {"plan_id": "P1"})
    t = MockFeishuTransport()
    push_plan_approval_cards_once(sd, t, receive_id=CHAT, action_secret=SECRET.encode())
    token = _token_from_card(t, "plan-approve")
    ctx = resolve_project_context()
    # ou_op has a valid token but only OPERATOR level → identity gate denies
    result = _handle_event_data(
        _button("plan-approve:P1", "ou_op", "m3", token=token),
        context=ctx, user_levels={})
    assert result["status"] == "rejected"
    assert not [e for e in _events(sd) if e.type == "plan.approved"]


def test_s3_unsigned_rejected_when_required(project: Path):
    sd = project / ".zf"
    ctx = resolve_project_context()
    result = _handle_event_data(
        _button("plan-approve:P1", "ou_boss", "m4", token=""),
        context=ctx, user_levels={})
    assert result["status"] == "rejected"
    rej = [e for e in _events(sd) if e.type == "callback.rejected"]
    assert rej and rej[-1].payload["reason"] == "token.token_required"


# --- S4: delivery projector folds reply lifecycle, deltas don't spam -------

def test_s4_delivery_working_then_done_no_delta_spam(project: Path):
    sd = project / ".zf"
    t = MockFeishuTransport()
    _emit(sd, "channel.agent.reply.requested", {"request_id": "R1", "member_id": "dev"})
    r1 = push_delivery_cards_once(sd, t, receive_id=CHAT, action_secret=SECRET.encode())
    assert r1["sent"] == ["R1"]
    assert "⏹️ Interrupt" in t.sent_messages[-1].content  # Working card w/ interrupt

    _emit(sd, "channel.agent.reply.completed", {"request_id": "R1"})
    r2 = push_delivery_cards_once(sd, t, receive_id=CHAT, action_secret=SECRET.encode())
    assert r2["updated"] == ["R1"] and not r2["sent"]
    assert "✅ Done" in t.updated_messages[-1][1]

    for i in range(50):
        _emit(sd, "agent.session.part.delta", {"request_id": "R1", "seq": i})
    r3 = push_delivery_cards_once(sd, t, receive_id=CHAT, action_secret=SECRET.encode())
    assert not r3["sent"] and not r3["updated"]  # deltas never touch the card


# --- S5: interrupt round-trip + nonce single-use ---------------------------

def test_s5_interrupt_roundtrip_and_nonce_replay(project: Path):
    sd = project / ".zf"
    t = MockFeishuTransport()
    _emit(sd, "channel.agent.reply.started", {"request_id": "R2"})
    push_delivery_cards_once(sd, t, receive_id=CHAT, action_secret=SECRET.encode())
    token = _token_from_card(t, "agent-cancel")
    assert token

    ctx = resolve_project_context()
    first = _handle_event_data(
        _button("agent-cancel:R2", "ou_op", "i1", token=token),
        context=ctx, user_levels={})
    assert first["ok"] is True
    cancelled = [e for e in _events(sd) if e.type == "agent.session.run.cancelled"]
    assert cancelled and cancelled[0].payload["request_id"] == "R2"
    assert cancelled[0].payload["source"] == "feishu"
    assert not [e for e in _events(sd) if "pane" in e.type or "pid.kill" in e.type]

    # replay the exact same signed click → nonce already consumed
    replay = _handle_event_data(
        _button("agent-cancel:R2", "ou_op", "i2", token=token),
        context=ctx, user_levels={})
    assert replay["status"] == "rejected"
    assert len([e for e in _events(sd) if e.type == "agent.session.run.cancelled"]) == 1


# --- S6: channel message projection reaches the transport (CLI) ------------

def test_s6_channel_projection_pushes(project: Path, capsys):
    sd = project / ".zf"
    _emit(sd, "human.escalate", {"reason": "need approver"}, actor="orch")
    main(["feishu", "push", "--transport", "mock",
          "--channel", "approval=ch_approval", "--state-dir", str(sd)])
    out = capsys.readouterr().out
    assert "Pushed 1 Feishu message" in out
