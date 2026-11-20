from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.cli import feishu as feishu_cli
from zf.integrations.feishu.channel_progress_card import (
    CONFIRM_COMMAND,
    CREATE_TASK_COMMAND,
    FINALIZE_COMMAND,
    PLAN_WORKFLOW_COMMAND,
    build_channel_progress_card,
    fold_channel_progress,
    handle_channel_progress_action,
    progress_target,
    push_channel_progress_cards_once,
    sync_channel_progress_cards,
)
from zf.integrations.feishu import channel_progress_card
from zf.integrations.feishu.transport import MockFeishuTransport
from zf.integrations.feishu.gateway import AuthLevel


CHANNEL_ID = "ch-prd-1"
CHAT_ID = "oc_owner"
ORIGIN_MESSAGE_ID = "om_requirement"
OWNER = "feishu:ou_owner"


def _event(
    event_type: str, payload: dict, *, correlation_id: str = CHANNEL_ID
) -> ZfEvent:
    return ZfEvent(
        type=event_type, actor="test", correlation_id=correlation_id, payload=payload
    )


def _created() -> ZfEvent:
    return _event(
        "channel.created",
        {
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "name": "Minimal PRD",
            "owner_actor_ref": OWNER,
            "origin_binding": {
                "surface": "feishu",
                "chat_id": CHAT_ID,
                "origin_message_id": ORIGIN_MESSAGE_ID,
                "thread_id": "main",
            },
        },
    )


def _discussion_events() -> list[ZfEvent]:
    return [
        _created(),
        _event(
            "channel.discussion.started",
            {
                "channel_id": CHANNEL_ID,
                "thread_id": "main",
            },
        ),
        _event(
            "channel.agent.reply.completed",
            {
                "channel_id": CHANNEL_ID,
                "thread_id": "main",
            },
        ),
    ]


def _append(state_dir: Path, events: list[ZfEvent]) -> None:
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    for event in events:
        writer.append(event)


def _context(state_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(
        state_dir=state_dir,
        project_root=state_dir.parent,
        config=SimpleNamespace(),
    )


def test_guided_kanban_handoff_preserves_bridge_app_id(monkeypatch, tmp_path: Path):
    captured: dict[str, object] = {}

    monkeypatch.setenv("FEISHU_APP_ID", "cli-kanban")

    def capture(event, **kwargs):
        captured["event"] = event
        captured["kwargs"] = kwargs
        return "future"

    from zf.cli import feishu_consume

    monkeypatch.setattr(feishu_consume, "dispatch_inbound_async", capture)

    result = channel_progress_card._dispatch_guided_kanban(
        "Create a Task from the confirmed PRD.",
        {
            "context": _context(tmp_path / ".zf"),
            "command": CREATE_TASK_COMMAND,
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "task_id": "",
            "user_id": "ou_owner",
            "chat_id": CHAT_ID,
            "origin_binding": {"origin_message_id": ORIGIN_MESSAGE_ID},
        },
    )

    event = captured["event"]
    assert result == "future"
    assert event.payload["app_id"] == "cli-kanban"
    assert event.payload["message_id"].startswith("zf-guided-")


def test_fold_exposes_one_controlled_gate_at_each_stage():
    events = _discussion_events()
    item = fold_channel_progress(events)[progress_target(CHANNEL_ID, "main")]
    assert item["stage"] == "awaiting_finalize"
    card = build_channel_progress_card(item)
    assert "Channel  →  Discussion  →  PRD  →  Task  →  Workflow" in card["elements"][0]["text"]["content"]
    assert "当前阶段：Discussion" in card["elements"][0]["text"]["content"]
    assert "等待生成 PRD" in card["elements"][0]["text"]["content"]
    assert card["elements"][1]["actions"][0]["text"]["content"] == "下一步：生成 PRD"
    assert card["elements"][1]["actions"][0]["value"]["action"].startswith(
        f"{FINALIZE_COMMAND}:"
    )

    events.extend(
        [
            _event(
                "channel.synthesis.requested",
                {
                    "channel_id": CHANNEL_ID,
                    "thread_id": "main",
                },
            ),
            _event(
                "channel.consensus.proposed",
                {
                    "channel_id": CHANNEL_ID,
                    "thread_id": "main",
                    "artifact_ref": "artifacts/prd.md",
                    "artifact_digest": "a" * 64,
                    "prd_revision": 1,
                },
            ),
        ]
    )
    item = fold_channel_progress(events)[progress_target(CHANNEL_ID, "main")]
    assert item["stage"] == "awaiting_owner"
    assert build_channel_progress_card(item)["elements"][1]["actions"][0]["value"][
        "action"
    ].startswith(f"{CONFIRM_COMMAND}:")

    events.append(
        _event(
            "channel.consensus.reached",
            {
                "channel_id": CHANNEL_ID,
                "thread_id": "main",
                "artifact_ref": "artifacts/prd.md",
                "artifact_digest": "a" * 64,
                "prd_revision": 1,
            },
        )
    )
    item = fold_channel_progress(events)[progress_target(CHANNEL_ID, "main")]
    assert item["stage"] == "prd_confirmed"
    assert build_channel_progress_card(item)["elements"][1]["actions"][0]["value"][
        "action"
    ].startswith(f"{CREATE_TASK_COMMAND}:")

    events.append(
        _event(
            "channel.result.receipt.recorded",
            {
                "channel_id": CHANNEL_ID,
                "thread_id": "main",
                "receipt_kind": "task_created",
                "task_id": "TASK-1",
            },
        )
    )
    item = fold_channel_progress(events)[progress_target(CHANNEL_ID, "main")]
    assert item["stage"] == "task_created"
    assert build_channel_progress_card(item)["elements"][1]["actions"][0]["value"][
        "action"
    ].startswith(f"{PLAN_WORKFLOW_COMMAND}:")


def test_multi_lens_reply_does_not_offer_finalize_before_synthesis():
    events = [
        _created(),
        _event(
            "channel.discussion.started",
            {
                "channel_id": CHANNEL_ID,
                "thread_id": "main",
                "product_mode": "multi_lens",
            },
        ),
        _event(
            "channel.agent.reply.completed",
            {
                "channel_id": CHANNEL_ID,
                "thread_id": "main",
            },
        ),
    ]

    item = fold_channel_progress(events)[progress_target(CHANNEL_ID, "main")]
    assert item["stage"] == "discussing"
    assert all(
        element.get("tag") != "action"
        for element in build_channel_progress_card(item)["elements"]
    )


def test_progress_projection_is_exact_origin_restart_safe(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    _append(state_dir, _discussion_events())
    sent: list[tuple[dict, dict]] = []
    updated: list[tuple[str, dict, dict]] = []

    first = sync_channel_progress_cards(
        state_dir,
        send_card=lambda item, card: sent.append((item, card)) or "om_progress",
        update_card=lambda message_id, item, card: updated.append(
            (message_id, item, card)
        ),
    )
    assert first["sent"] == [progress_target(CHANNEL_ID, "main")]
    assert sent[0][0]["origin_binding"]["chat_id"] == CHAT_ID

    second = sync_channel_progress_cards(
        state_dir,
        send_card=lambda item, card: sent.append((item, card)) or "duplicate",
        update_card=lambda message_id, item, card: updated.append(
            (message_id, item, card)
        ),
        ledger=first["ledger"],
    )
    assert second["sent"] == []
    assert second["updated"] == []

    _append(
        state_dir,
        [
            _event(
                "channel.synthesis.requested",
                {
                    "channel_id": CHANNEL_ID,
                    "thread_id": "main",
                },
            )
        ],
    )
    third = sync_channel_progress_cards(
        state_dir,
        send_card=lambda item, card: "duplicate",
        update_card=lambda message_id, item, card: updated.append(
            (message_id, item, card)
        ),
        ledger=second["ledger"],
    )
    assert third["updated"] == [progress_target(CHANNEL_ID, "main")]
    assert updated[-1][0] == "om_progress"


def test_push_signs_button_and_threads_to_exact_origin(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    _append(state_dir, _discussion_events())
    transport = MockFeishuTransport()

    result = push_channel_progress_cards_once(
        state_dir,
        transport,
        action_secret=b"progress-secret",
        now=100.0,
    )

    assert result["sent"] == [progress_target(CHANNEL_ID, "main")]
    message = transport.sent_messages[0]
    assert message.chat_id == CHAT_ID
    assert message.thread_id == ORIGIN_MESSAGE_ID
    card = json.loads(message.content)
    value = card["elements"][1]["actions"][0]["value"]
    assert value["action"].startswith(f"{FINALIZE_COMMAND}:")
    assert value["t"].startswith("zf1.")


def test_finalize_click_requires_exact_owner_and_only_requests_synthesis(
    tmp_path: Path,
):
    state_dir = tmp_path / ".zf"
    _append(state_dir, _discussion_events())
    calls: list[tuple[str, dict, str]] = []

    result = handle_channel_progress_action(
        command=FINALIZE_COMMAND,
        target=progress_target(CHANNEL_ID, "main"),
        context=_context(state_dir),
        user_id="ou_owner",
        chat_id=CHAT_ID,
        execute_action=lambda action, payload, actor: (
            calls.append((action, payload, actor))
            or {
                "ok": True,
                "status": "requested",
            }
        ),
    )
    assert result["ok"] is True
    assert calls == [
        (
            "channel-synthesis-request",
            {
                "channel_id": CHANNEL_ID,
                "thread_id": "main",
                "reason": "Finalize the current discussion into a canonical PRD draft.",
                "member_id": OWNER,
            },
            OWNER,
        )
    ]

    forbidden = handle_channel_progress_action(
        command=FINALIZE_COMMAND,
        target=progress_target(CHANNEL_ID, "main"),
        context=_context(state_dir),
        user_id="ou_other",
        chat_id=CHAT_ID,
        execute_action=lambda *_: {"ok": True},
    )
    assert forbidden["status"] == "forbidden"


def test_confirm_rebinds_current_artifact_and_handoff_only_dispatches_agent(
    tmp_path: Path,
):
    state_dir = tmp_path / ".zf"
    digest = "b" * 64
    events = _discussion_events() + [
        _event(
            "channel.synthesis.proposed",
            {
                "channel_id": CHANNEL_ID,
                "thread_id": "main",
                "artifact_ref": "artifacts/prd.md",
                "artifact_digest": digest,
                "prd_revision": 2,
            },
        ),
        _event(
            "channel.consensus.proposed",
            {
                "channel_id": CHANNEL_ID,
                "thread_id": "main",
                "artifact_ref": "artifacts/prd.md",
                "artifact_digest": digest,
                "prd_revision": 2,
            },
        ),
    ]
    _append(state_dir, events)
    actions: list[tuple[str, dict, str]] = []
    confirmed = handle_channel_progress_action(
        command=CONFIRM_COMMAND,
        target=progress_target(CHANNEL_ID, "main"),
        context=_context(state_dir),
        user_id="ou_owner",
        chat_id=CHAT_ID,
        execute_action=lambda action, payload, actor: (
            actions.append((action, payload, actor))
            or {
                "ok": True,
                "status": "confirmed",
            }
        ),
    )
    assert confirmed["ok"] is True
    assert actions[0][0] == "channel-consensus-confirm"
    assert actions[0][1]["artifact_digest"] == digest
    assert actions[0][1]["prd_revision"] == 2

    _append(
        state_dir,
        [
            _event(
                "channel.consensus.reached",
                {
                    "channel_id": CHANNEL_ID,
                    "thread_id": "main",
                    "artifact_ref": "artifacts/prd.md",
                    "artifact_digest": digest,
                    "prd_revision": 2,
                },
            )
        ],
    )
    dispatched: list[tuple[str, dict]] = []
    handoff = handle_channel_progress_action(
        command=CREATE_TASK_COMMAND,
        target=progress_target(CHANNEL_ID, "main"),
        context=_context(state_dir),
        user_id="ou_owner",
        chat_id=CHAT_ID,
        message_id="om_progress",
        dispatch_kanban=lambda prompt, identity: dispatched.append((prompt, identity)),
    )
    assert handoff["status"] == "accepted"
    assert "task_create Plan" in dispatched[0][0]
    assert not any(
        event.type == "task.created"
        for event in EventLog(state_dir / "events.jsonl").read_all()
    )

    _append(
        state_dir,
        [
            _event(
                "channel.result.receipt.recorded",
                {
                    "channel_id": CHANNEL_ID,
                    "thread_id": "main",
                    "receipt_kind": "task_created",
                    "task_id": "TASK-DELIVERY-1",
                },
            )
        ],
    )
    workflow = handle_channel_progress_action(
        command=PLAN_WORKFLOW_COMMAND,
        target=progress_target(CHANNEL_ID, "main", "TASK-DELIVERY-1"),
        context=_context(state_dir),
        user_id="ou_owner",
        chat_id=CHAT_ID,
        dispatch_kanban=lambda prompt, identity: dispatched.append((prompt, identity)),
    )
    assert workflow["status"] == "accepted"
    assert "task_workflow Plan" in dispatched[-1][0]
    assert "TASK-DELIVERY-1" in dispatched[-1][0]
    assert not any(
        event.type == "workflow.invoke.requested"
        for event in EventLog(state_dir / "events.jsonl").read_all()
    )


def test_stale_or_cross_chat_progress_click_fails_closed(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    _append(state_dir, _discussion_events())
    context = _context(state_dir)

    cross_chat = handle_channel_progress_action(
        command=FINALIZE_COMMAND,
        target=progress_target(CHANNEL_ID, "main"),
        context=context,
        user_id="ou_owner",
        chat_id="oc_other",
        execute_action=lambda *_: {"ok": True},
    )
    assert cross_chat["status"] == "origin_mismatch"

    stale = handle_channel_progress_action(
        command=CREATE_TASK_COMMAND,
        target=progress_target(CHANNEL_ID, "main"),
        context=context,
        user_id="ou_owner",
        chat_id=CHAT_ID,
        dispatch_kanban=lambda *_: None,
    )
    assert stale["status"] == "stale_progress_action"


def test_callback_gateway_routes_progress_command_and_classifies_it_signed(
    tmp_path: Path,
    monkeypatch,
):
    state_dir = tmp_path / ".zf"
    context = _context(state_dir)
    context.config = SimpleNamespace(
        integrations=SimpleNamespace(feishu_identity=SimpleNamespace(enabled=False)),
    )
    calls: list[dict] = []
    monkeypatch.setattr(
        feishu_cli,
        "handle_channel_progress_action",
        lambda **kwargs: (
            calls.append(kwargs)
            or {
                "ok": True,
                "status": "accepted",
                "message": "accepted",
            }
        ),
    )
    target = progress_target(CHANNEL_ID, "main")

    result = feishu_cli._handle_event_data(
        {
            "type": "button_action",
            "payload": {
                "action": f"{FINALIZE_COMMAND}:{target}",
                "message_id": "om-progress-click",
            },
            "user_id": "ou_owner",
            "chat_id": CHAT_ID,
        },
        context=context,
        user_levels={"ou_owner": AuthLevel.APPROVER},
    )

    assert result["status"] == "accepted"
    assert calls[0]["target"] == target
    assert FINALIZE_COMMAND in feishu_cli.SIGNED_ACTION_COMMANDS


@pytest.mark.parametrize("readiness_verdict", ["ready", "unassessed"])
def test_confirm_click_is_wired_to_real_controlled_action(tmp_path: Path, readiness_verdict: str):
    state_dir = tmp_path / ".zf"
    digest = "c" * 64
    _append(
        state_dir,
        _discussion_events()
        + [
            _event(
                "channel.synthesis.proposed",
                {
                    "channel_id": CHANNEL_ID,
                    "thread_id": "main",
                    "artifact_ref": "channel-artifacts/prd.md",
                    "artifact_digest": digest,
                    "prd_revision": 1,
                },
            ),
            _event(
                "channel.consensus.proposed",
                {
                    "channel_id": CHANNEL_ID,
                    "thread_id": "main",
                    "artifact_ref": "channel-artifacts/prd.md",
                    "artifact_digest": digest,
                    "prd_revision": 1,
                    "required_signers": [],
                    "owner_actor_ref": OWNER,
                    "readiness_verdict": readiness_verdict,
                },
            ),
        ],
    )
    context = _context(state_dir)

    result = handle_channel_progress_action(
        command=CONFIRM_COMMAND,
        target=progress_target(CHANNEL_ID, "main"),
        context=context,
        user_id="ou_owner",
        chat_id=CHAT_ID,
    )

    if readiness_verdict == "unassessed":
        assert result["ok"] is False
        assert result["action_result"]["status"] == "readiness_unassessed"
        assert not any(e.type == "channel.consensus.signed" for e in EventLog(state_dir / "events.jsonl").read_all())
        return
    assert result["ok"] is True
    assert result["status"] == "confirmed"
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert any(event.type == "channel.consensus.signed" for event in events)
    assert any(event.type == "channel.consensus.reached" for event in events)
