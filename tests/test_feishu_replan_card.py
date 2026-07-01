"""Feishu replan owner-decision card (报告场景 13)."""

from __future__ import annotations

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.integrations.feishu.replan_approval_card import (
    build_replan_card,
    build_replan_verdict_update,
    push_replan_cards_once,
    sync_replan_cards,
)
from zf.integrations.feishu.transport import MockFeishuTransport


def test_card_has_proposal_and_deep_link():
    card = build_replan_card(
        {"proposal_ref": "RP-1", "eval_ref": "EV-1"},
        web_base_url="http://w")
    s = str(card)
    assert "RP-1" in s and "page=inbox&replan=RP-1" in s
    assert card["_card_key"] == "replan-RP-1"
    assert card["header"]["template"] == "orange"


def test_verdict_update_per_decision():
    up = build_replan_verdict_update(
        "replan.owner_decision.approved", {"proposal_ref": "RP-1"})
    assert up["header"]["template"] == "green" and "已批准" in str(up)
    rj = build_replan_verdict_update(
        "replan.owner_decision.rejected", {"proposal_ref": "RP-1", "reason": "缺据"})
    assert rj["header"]["template"] == "red" and "缺据" in str(rj)
    df = build_replan_verdict_update(
        "replan.owner_decision.deferred", {"proposal_ref": "RP-1"})
    assert df["header"]["template"] == "grey" and "已推迟" in str(df)


def _w(sd):
    return EventWriter(EventLog(sd / "events.jsonl"))


def test_sync_sends_then_updates_idempotent(tmp_path):
    sd = tmp_path / ".zf"; sd.mkdir()
    w = _w(sd)
    w.append(ZfEvent(type="replan.adoption.awaiting_owner", actor="kernel",
                     payload={"proposal_ref": "RP-9", "eval_ref": "EV-9"}))
    sent, updated, ledger = [], [], {}
    r1 = sync_replan_cards(
        sd, send_card=lambda c: (sent.append(c), f"m-{len(sent)}")[1],
        update_card=lambda mid, c: updated.append((mid, c)), ledger=ledger)
    assert r1["sent"] == ["RP-9"] and not updated

    # rerun → idempotent
    r2 = sync_replan_cards(
        sd, send_card=lambda c: (_ for _ in ()).throw(AssertionError("resend")),
        update_card=lambda mid, c: updated.append((mid, c)), ledger=ledger)
    assert r2["sent"] == []

    # decision → update original card
    w.append(ZfEvent(type="replan.owner_decision.approved", actor="operator",
                     payload={"proposal_ref": "RP-9"}))
    r3 = sync_replan_cards(
        sd, send_card=lambda c: "never",
        update_card=lambda mid, c: updated.append((mid, c)), ledger=ledger)
    assert r3["updated"] == ["RP-9"] and updated[0][0] == "m-1"


def test_push_once_persists_ledger(tmp_path):
    import json
    sd = tmp_path / ".zf"; sd.mkdir()
    _w(sd).append(ZfEvent(type="replan.adoption.awaiting_owner", actor="k",
                          payload={"proposal_ref": "RP-2", "eval_ref": "EV-2"}))
    t = MockFeishuTransport()
    r = push_replan_cards_once(sd, t, receive_id="oc_x", web_base_url="http://w")
    assert r["sent"] == ["RP-2"] and len(t.sent_messages) == 1
    ledger = json.loads(
        (sd / "integrations" / "feishu" / "replan_ledger.json").read_text())
    assert ledger["replan-RP-2"]["state"] == "pending"
