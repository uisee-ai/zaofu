"""ZF-E2E-PRDCTL-P2-8:operator 手术动作词表(payload 补键 / 简报重投 /
死决策 dismiss / ship 重试)——RM 可提案,router 恒 needs_approval。"""

from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import ProjectConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.run_manager_router import decide_action_policy


def _service(tmp_path: Path) -> tuple[ControlledActionService, EventLog]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir(exist_ok=True)
    log = EventLog(state_dir / "events.jsonl")
    service = ControlledActionService(
        state_dir,
        EventWriter(log),
        config=ZfConfig(project=ProjectConfig(name="t")),
    )
    return service, log


def _exec(service, action: str, payload: dict) -> dict:
    requested = ZfEvent(
        type="control.action.requested", actor="web", payload=payload,
    )
    return service._execute_action(
        action=action,
        requested_action=action,
        payload=payload,
        requested=requested,
    )


def test_payload_repair_reemit_adds_missing_ref_key(tmp_path: Path):
    service, log = _service(tmp_path)
    source = ZfEvent(
        type="test.passed", actor="zf-cli",
        payload={"fanout_id": "f1", "stage_id": "verify"},
    )
    log.append(source)
    result = _exec(service, "payload-repair-reemit", {
        "source_event_id": source.id,
        "patch": {"candidate_ref": "candidate/PDD-X"},
    })
    assert result["ok"] is True
    reemitted = [e for e in log.read_all() if e.type == "test.passed"][-1]
    assert reemitted.payload["candidate_ref"] == "candidate/PDD-X"
    assert reemitted.payload["rework_of"] == source.id


def test_payload_repair_reemit_rejects_overwrite_and_non_ref_keys(tmp_path: Path):
    service, log = _service(tmp_path)
    source = ZfEvent(
        type="test.passed", actor="zf-cli",
        payload={"candidate_ref": "candidate/OLD"},
    )
    log.append(source)
    overwrite = _exec(service, "payload-repair-reemit", {
        "source_event_id": source.id,
        "patch": {"candidate_ref": "candidate/NEW"},
    })
    assert overwrite.get("ok") is not True
    illegal = _exec(service, "payload-repair-reemit", {
        "source_event_id": source.id,
        "patch": {"status": "completed"},
    })
    assert illegal.get("ok") is not True


def test_briefing_redeliver_emits_request_event(tmp_path: Path):
    service, log = _service(tmp_path)
    result = _exec(service, "briefing-redeliver", {
        "role_instance": "dev-lane-0",
        "briefing_path": ".zf/briefings/dev-lane-0.md",
    })
    assert result["ok"] is True
    requests = [e for e in log.read_all() if e.type == "briefing.redeliver.requested"]
    assert requests and requests[-1].payload["role_instance"] == "dev-lane-0"


def test_human_decision_dismiss_acknowledges_open_escalation(tmp_path: Path):
    service, log = _service(tmp_path)
    log.append(ZfEvent(
        type="human.escalate", actor="run-manager",
        payload={"decision_token": "hdec-abc123", "reason": "stuck"},
    ))
    result = _exec(service, "human-decision-dismiss", {
        "decision_token": "hdec-abc123",
    })
    assert result["ok"] is True
    acks = [e for e in log.read_all() if e.type == "human.escalation.acknowledged"]
    assert acks and acks[-1].payload["status"] == "dismissed"

    again = _exec(service, "human-decision-dismiss", {
        "decision_token": "hdec-abc123",
    })
    assert again.get("ok") is not True  # already acknowledged → 409


def test_ship_retry_requires_judge_passed(tmp_path: Path):
    service, _log = _service(tmp_path)
    result = _exec(service, "ship-retry", {})
    assert result.get("ok") is not True


def test_surgery_actions_policy_is_needs_approval():
    for action in (
        "payload-repair-reemit",
        "briefing-redeliver",
        "human-decision-dismiss",
        "ship-retry",
    ):
        decision = decide_action_policy(action=action, payload={})
        assert decision["decision"] == "needs_approval"
        assert decision["executable"] is False
