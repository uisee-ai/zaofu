"""FIX-3(bizsim r4 F1):审批阻塞面分级 blocking_scope + approval_command。

r4 实锚:频道侧一个成员回复失败的 failure-closeout 审批(无任何 workflow
锚点)把整个 run 的派发锁死 50min+。side 级审批不得冻结全 run。
"""

from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.run_manager import (
    _status_explain_decision,
    emit_human_escalation_package,
)


def _emit(tmp_path: Path, action: dict) -> dict:
    log = EventLog(tmp_path / "events.jsonl")
    event = emit_human_escalation_package(
        EventWriter(log), action=action, reason="test",
    )
    return event.payload


def test_anchorless_escalation_is_side_scope(tmp_path: Path) -> None:
    payload = _emit(tmp_path, {
        "action": "failure-closeout-activate",
        "checkpoint_id": "failure-closeout-activate-abc",
    })
    assert payload["blocking_scope"] == "side"
    assert "human-decision-approve-controlled-action" in payload["approval_command"]
    assert payload["decision_token"] in payload["approval_command"]


def test_task_anchored_escalation_is_run_scope(tmp_path: Path) -> None:
    payload = _emit(tmp_path, {
        "action": "failure-closeout-activate",
        "checkpoint_id": "ck", "task_id": "T-1",
    })
    assert payload["blocking_scope"] == "run"


def _explain(pending_human: list[dict]) -> dict:
    return _status_explain_decision(
        pending_action=None,
        completion_profile={"pending_human_decisions": pending_human},
        monitor={},
        no_progress={},
        repair_merge_queue={},
    )


def test_side_only_decisions_do_not_block_dispatch() -> None:
    result = _explain([{"decision_token": "hdec-1", "blocking_scope": "side"}])
    assert result["blocking"] is False
    assert result["next_auto_action"] == "continue_dispatch"
    assert result["side_blocking_refs"]
    assert result["blocking_refs"] == []


def test_run_scope_and_legacy_decisions_still_block() -> None:
    mixed = [
        {"decision_token": "hdec-1", "blocking_scope": "side"},
        {"decision_token": "hdec-2", "blocking_scope": "run"},
    ]
    result = _explain(mixed)
    assert result["blocking"] is True
    assert len(result["blocking_refs"]) == 1

    legacy = [{"decision_token": "hdec-3"}]  # 无 scope 字段 → fail-closed 按 run
    assert _explain(legacy)["blocking"] is True


def test_direct_owner_action_auto_acknowledges_escalation(tmp_path: Path) -> None:
    """FIX-4(bizsim r4 F3):直调 failure-closeout-activate 成功后自动补发
    human.escalation.acknowledged,checkpoint lease 随决议消费释放。"""
    import json

    from zf.runtime.control_actions import ControlledActionService
    from zf.runtime.run_manager import _pending_human_decisions

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]")
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)

    escalate = emit_human_escalation_package(
        writer,
        action={
            "action": "failure-closeout-activate",
            "checkpoint_id": "failure-closeout-activate-xyz",
        },
        reason="requires explicit owner approval",
    )
    token = escalate.payload["decision_token"]
    assert _pending_human_decisions(log.read_all())

    manifest = state_dir / "failure-closeout" / "failure-closeout-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"items": []}), encoding="utf-8")

    requested = writer.emit(
        "web.action.requested", actor="web",
        payload={"action": "failure-closeout-activate"},
    )
    service = ControlledActionService(
        state_dir, writer, config=None, actor="web", surface="web",
    )
    result = service.execute(
        action="failure-closeout-activate",
        requested_action="failure-closeout-activate",
        requested=requested,
        payload={
            "approval_ref": "human:" + token,
            "manifest_ref": str(manifest),
        },
    )
    assert result["status"] == "activated"

    acks = [
        e for e in log.read_all()
        if e.type == "human.escalation.acknowledged"
        and e.payload.get("decision_token") == token
    ]
    assert acks, "直调成功必须自动补发决议回执"
    assert acks[0].payload["decision"] == "approve_controlled_action"
