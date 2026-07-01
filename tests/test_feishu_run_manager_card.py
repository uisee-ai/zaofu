"""Run Manager Feishu cards + human-decision callbacks."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from zf.cli.feishu import _handle_event_data
from zf.cli.main import main
from zf.core.config.project_context import resolve_project_context
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.integrations.feishu.run_manager_card import (
    build_run_manager_escalation_card,
    sync_run_manager_cards,
)


def _writer(state_dir: Path) -> EventWriter:
    return EventWriter(EventLog(state_dir / "events.jsonl"))


def _emit(state_dir: Path, etype: str, payload: dict, *, actor: str = "test") -> None:
    _writer(state_dir).append(ZfEvent(type=etype, actor=actor, payload=payload))


def _sync(state_dir, ledger, sent, updated):
    return sync_run_manager_cards(
        state_dir,
        send_card=lambda c: (sent.append(c), f"msg-{len(sent)}")[1],
        update_card=lambda mid, c: updated.append((mid, c)),
        ledger=ledger,
    )


def test_escalation_card_inlines_evidence_and_buttons() -> None:
    card = build_run_manager_escalation_card({
        "decision_token": "hdec-1",
        "run_id": "R1",
        "failure_class": "workflow_batch_resume",
        "checkpoint_id": "ck-1",
        "fingerprint": "fp-1",
        "safe_resume_action": "trigger_rework",
        "reason": "needs approval",
    })
    text = json.dumps(card, ensure_ascii=False)
    assert "workflow_batch_resume" in text
    assert "ck-1" in text and "fp-1" in text
    assert "human-decision-approve:hdec-1" in text
    assert "human-decision-diagnose:hdec-1" in text
    assert "human-decision-halt:hdec-1" in text


def test_sync_sends_escalation_then_updates_on_ack(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    _emit(state_dir, "human.escalation.sent", {
        "decision_token": "hdec-2",
        "run_id": "R2",
        "failure_class": "needs_human",
    })
    ledger: dict = {}
    sent, updated = [], []
    r1 = _sync(state_dir, ledger, sent, updated)
    assert r1["escalation_sent"] == ["hdec-2"]
    assert "批准并执行" in str(sent[0])

    _emit(state_dir, "human.escalation.acknowledged", {
        "decision_token": "hdec-2",
        "decision": "request_autoresearch",
    })
    r2 = _sync(state_dir, ledger, sent, updated)
    assert r2["escalation_updated"] == ["hdec-2"]
    assert updated[0][0] == "msg-1"
    assert "request_autoresearch" in str(updated[0][1])
    assert "human-decision-approve" not in str(updated[0][1])


def test_status_card_updates_in_place_when_digest_changes(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    projection_dir = state_dir / "projections"
    projection_dir.mkdir(parents=True)
    projection = {
        "generated_at": "2026-06-25T00:00:00Z",
        "summary": {
            "goal_status": "active",
            "completion_status": "active",
            "pending_actions": 1,
            "blocked_actions": 0,
            "no_progress_status": "clear",
        },
        "monitor": {"state": "running", "next_wait": "runtime_event"},
        "status_explain": {"wait_reason": "runtime_event", "blocking": False},
        "completion_profile": {"pending_human_decisions": []},
    }
    (projection_dir / "run_manager.json").write_text(
        json.dumps(projection), encoding="utf-8",
    )
    ledger: dict = {}
    sent, updated = [], []
    r1 = _sync(state_dir, ledger, sent, updated)
    assert r1["status_sent"] is True
    assert "pending_actions" in str(sent[0])

    projection["summary"]["blocked_actions"] = 1
    (projection_dir / "run_manager.json").write_text(
        json.dumps(projection), encoding="utf-8",
    )
    r2 = _sync(state_dir, ledger, sent, updated)
    assert r2["status_updated"] is True
    assert updated[0][0] == "msg-1"


def test_push_persists_ledger_and_signs_decision_buttons(tmp_path: Path) -> None:
    from zf.integrations.feishu.run_manager_card import push_run_manager_cards_once
    from zf.integrations.feishu.transport import MockFeishuTransport

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    _emit(state_dir, "human.escalation.sent", {
        "decision_token": "hdec-3",
        "run_id": "R3",
        "failure_class": "needs_human",
    })
    transport = MockFeishuTransport()

    result = push_run_manager_cards_once(
        state_dir,
        transport,
        receive_id="oc_chat",
        action_secret=b"secret",
        now=1000,
    )
    assert result["escalation_sent"] == ["hdec-3"]
    card = json.loads(transport.sent_messages[0].content)
    assert '"t"' in json.dumps(card)
    ledger = json.loads(
        (state_dir / "integrations" / "feishu" / "run_manager_ledger.json")
        .read_text(encoding="utf-8")
    )
    assert ledger["run-manager-escalation-hdec-3"]["state"] == "pending"


def _button(action: str, user_id: str, message_id: str) -> dict:
    return {
        "type": "button_action",
        "payload": {"action": action, "message_id": message_id},
        "user_id": user_id,
        "chat_id": "c1",
    }


def test_human_decision_button_emits_acknowledged_event(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(yaml.dump({
        "version": "1.0",
        "project": {"name": "feishu-rm-test", "state_dir": ".zf"},
        "roles": [{"name": "dev", "backend": "mock"}],
        "integrations": {
            "feishu_identity": {
                "enabled": True,
                "users": {
                    "ou_appr": {"name": "alice", "level": "approver"},
                    "ou_viewer": {"name": "bob", "level": "viewer"},
                },
            },
        },
    }))
    main(["init"])
    ctx = resolve_project_context()

    result = _handle_event_data(
        _button("human-decision-approve:hdec-4", "ou_appr", "m1"),
        context=ctx,
        user_levels={},
    )
    assert result["ok"] is True and result["status"] == "acknowledged"
    events = EventLog(ctx.state_dir / "events.jsonl").read_all()
    ack = [e for e in events if e.type == "human.escalation.acknowledged"]
    assert ack and ack[0].payload["decision_token"] == "hdec-4"
    assert ack[0].payload["decision"] == "approve_controlled_action"

    rejected = _handle_event_data(
        _button("human-decision-approve:hdec-5", "ou_viewer", "m2"),
        context=ctx,
        user_levels={},
    )
    assert rejected["status"] == "rejected"
    events = EventLog(ctx.state_dir / "events.jsonl").read_all()
    assert not [
        event for event in events
        if event.type == "human.escalation.acknowledged"
        and event.payload.get("decision_token") == "hdec-5"
    ]
