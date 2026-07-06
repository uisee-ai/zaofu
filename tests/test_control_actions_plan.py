"""B15: plan-approve/plan-reject controlled actions(doc 93 §7.1)。"""

from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import ProjectConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.events.model import ZfEvent
from zf.runtime.control_actions import ControlledActionService


def _service(tmp_path: Path) -> tuple[ControlledActionService, EventLog]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    service = ControlledActionService(
        state_dir,
        EventWriter(log),
        config=ZfConfig(project=ProjectConfig(name="t")),
    )
    return service, log


def _exec(service, log, action: str, payload: dict) -> dict:
    requested = ZfEvent(
        type="control.action.requested", actor="web", payload=payload,
    )
    return service._execute_action(
        requested=requested,
        action=action,
        requested_action=action,
        payload=payload,
    )


def test_plan_approve_emits_operator_event(tmp_path: Path):
    service, log = _service(tmp_path)
    result = _exec(service, log, "plan-approve", {"plan_id": "evt-abc123"})
    assert result.get("ok") is True
    approved = [e for e in log.read_all() if e.type == "plan.approved"]
    assert approved and approved[0].actor == "operator"
    assert approved[0].payload["plan_id"] == "evt-abc123"
    assert "controlled-action" in approved[0].payload["via"]


def test_plan_reject_requires_reason(tmp_path: Path):
    service, log = _service(tmp_path)
    result = _exec(service, log, "plan-reject", {"plan_id": "evt-abc123"})
    assert result.get("ok") is False
    assert not [e for e in log.read_all() if e.type == "plan.rejected"]
    result = _exec(
        service, log, "plan-reject",
        {"plan_id": "evt-abc123", "reason": "缺 assembly"},
    )
    assert result.get("ok") is True
    rejected = [e for e in log.read_all() if e.type == "plan.rejected"]
    assert rejected and rejected[0].payload["reason"] == "缺 assembly"


def test_review_advice_is_suggestion_not_decision(tmp_path: Path):
    # B16: agent 预审只是建议;simple serial issue 不应因缺 assembly 被拒。
    import json

    from zf.cli.plan_approval import review_advice

    simple = tmp_path / "task_map.json"
    simple.write_text(json.dumps({"tasks": [
        {"task_id": "pi-core", "allowed_paths": ["packages/pi/**"],
         "verification": "pnpm test"},
    ]}), encoding="utf-8")
    advice = review_advice(simple)
    assert advice["advice"] == "approve"
    assert advice["binding"] is False
    assert advice["checklist_warnings"] == []

    bad = tmp_path / "bad_map.json"
    bad.write_text(json.dumps({"tasks": [
        {"task_id": "pi-core", "allowed_paths": ["packages/pi/**"],
         "verification": "pnpm test"},
        {"task_id": "pi-web", "allowed_paths": ["packages/web/**"],
         "verification": "pnpm test"},
    ]}), encoding="utf-8")
    advice = review_advice(bad)
    assert advice["advice"] == "reject"
    assert advice["binding"] is False
    assert any("assembly" in w for w in advice["checklist_warnings"])

    good = tmp_path / "good_map.json"
    good.write_text(json.dumps({"tasks": [
        {"task_id": "CJMIN-ASSEMBLY-001", "root_owner_class": "assembly",
         "allowed_paths": ["packages/assembly/**", "package.json"],
         "verification": "pnpm -r build && node boot.js"},
    ]}), encoding="utf-8")
    assert review_advice(good)["advice"] == "approve"


def test_intent_approve_with_execute_runs_proposals(tmp_path: Path):
    service, log = _service(tmp_path)
    (tmp_path / ".zf" / "kanban.json").write_text("[]\n", encoding="utf-8")
    r = _exec(service, log, "idea-to-product", {
        "objective": "three.js 赛车 MVP(已澄清)",
        "artifact_ref": ".zf/channel-artifacts/c.md",
        "contract": {"spec_ref": ".zf/channel-artifacts/c.md", "handoff_artifacts": [".zf/channel-artifacts/c.md"]},
    })
    assert r.get("ok")
    proposal = [e for e in log.read_all() if e.type == "operator.action.proposed"][-1]
    intent_id = proposal.payload["intent_id"]

    r = _exec(service, log, "operator-intent-approve", {
        "intent_id": intent_id, "approved_by": "operator",
        "reason": "ship it", "execute_proposals": True,
    })
    assert r.get("ok") and r.get("status") == "approved"
    executed = r.get("executed") or {}
    assert executed.get("ok"), str(executed)
    task_id = executed.get("created_task_id")
    assert task_id
    invokes = [e for e in log.read_all() if e.type == "workflow.invoke.requested"]
    assert invokes and invokes[-1].payload.get("task_id") == task_id, \
        "workflow-invoke must target the freshly created task, not the placeholder"
    assert [e.type for e in log.read_all()].count("operator.action.executed") == 1

    # idempotent: approving again with the flag must not run the chain twice
    r2 = _exec(service, log, "operator-intent-approve", {
        "intent_id": intent_id, "approved_by": "operator",
        "reason": "double click", "execute_proposals": True,
    })
    assert (r2.get("executed") or {}).get("reason") == "already_executed"
    assert len([e for e in log.read_all() if e.type == "workflow.invoke.requested"]) == 1


def test_intent_approve_without_flag_unchanged(tmp_path: Path):
    service, log = _service(tmp_path)
    r = _exec(service, log, "operator-intent-approve", {
        "intent_id": "opint-x", "approved_by": "operator", "reason": "record only",
    })
    assert r.get("ok") and "executed" not in r
    types = [e.type for e in log.read_all()]
    assert "operator.action.executed" not in types


def test_clarified_artifact_reaches_workflow_prompt(tmp_path: Path):
    """doc 122 §9 last mile: the clarified artifact must land in the child's
    workflow prompt package, or prd-author starts blind."""
    service, log = _service(tmp_path)
    (tmp_path / ".zf" / "kanban.json").write_text("[]\n", encoding="utf-8")
    ref = ".zf/channel-artifacts/clarified-racing.md"
    _exec(service, log, "idea-to-product", {
        "objective": "three.js 赛车 MVP", "artifact_ref": ref,
        "contract": {"spec_ref": ref, "handoff_artifacts": [ref]},
        "pattern_id": "prd-refine",
    })
    proposal = [e for e in log.read_all() if e.type == "operator.action.proposed"][-1]
    _exec(service, log, "operator-intent-approve", {
        "intent_id": proposal.payload["intent_id"], "approved_by": "op",
        "reason": "go", "execute_proposals": True,
    })
    invoke = [e for e in log.read_all() if e.type == "workflow.invoke.requested"][-1]
    refs = [a if isinstance(a, str) else a.get("ref")
            for a in invoke.payload.get("artifact_refs") or []]
    assert ref in refs
    prompt_ref = invoke.payload.get("workflow_prompt_ref")
    assert prompt_ref
    prompt_text = (tmp_path / ".zf" / prompt_ref).read_text(encoding="utf-8")
    assert "clarified-racing.md" in prompt_text
