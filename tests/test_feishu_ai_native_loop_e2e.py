"""E2E: the Feishu-driven AI-native closed loop (plan-approval gate ↔ fanout).

The away-from-desk loop of design doc 93 §7.3, driven through the REAL
Orchestrator gate. Unlike tests/test_feishu_channel_kanban_e2e.py (which tests
the Feishu card/callback plumbing over synthesized events), this exercises the
mechanism those depend on:

  task_map.ready → plan.approval.requested (writer fanout HELD)
    → Feishu surfaces the held plan (real card, signed button)
    → operator approves via the REAL ControlledAction path (Feishu/Web share it)
    → plan.approved (actor=operator) → fanout.started (gate truly unlocked)

It reuses the proven real-Orchestrator harness from test_writer_fanout_runtime
(_approval_orch / _approval_start) so the gate + fanout are the production code
paths, not stubs. See tests/e2e/feishu-ai-native-loop-e2e-plan.md.
"""

from __future__ import annotations

import json
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.integrations.feishu.plan_approval_card import push_plan_approval_cards_once
from zf.integrations.feishu.transport import MockFeishuTransport
from zf.runtime.control_actions import ControlledActionService

from tests.test_writer_fanout_runtime import _approval_orch, _approval_start

SECRET = b"loop-e2e-secret"
CHAT = "oc_owner"


def _requested(log):
    return [e for e in log.read_all() if e.type == "plan.approval.requested"]


def _has(log, etype: str) -> bool:
    return any(e.type == etype for e in log.read_all())


def _operator_approve_via_controlled_action(state_dir, log, orch, plan_id, *, action="plan-approve", reason=""):
    """Drive the SAME ControlledAction path the Feishu/Web callback uses.

    Mirrors cli/feishu.py _handle_plan_approval_result: emit the request
    precursor, then ControlledActionService.execute → plan.approved/rejected
    with actor=operator, source=feishu.
    """
    writer = EventWriter(log)
    payload = {"plan_id": plan_id}
    if reason:
        payload["reason"] = reason
    requested = writer.emit(
        "feishu.command.enveloped", actor="feishu:owner",
        payload={"command": action, "request": payload},
    )
    service = ControlledActionService(
        state_dir, writer, config=orch.config,
        actor="feishu:owner", source="feishu", surface="feishu",
    )
    return service.execute(
        action=action, requested_action=f"/zf {action}",
        payload=payload, requested=requested,
    )


# --- E1: the closed loop -- gate holds → feishu → real approve → fanout ----

def test_e1_gate_holds_feishu_surfaces_real_approve_unlocks_fanout(tmp_path):
    state_dir, log, orch = _approval_orch(tmp_path, enabled=True)
    _approval_start(orch, log)

    # (1) gate HOLDS writer fanout, surfaces a plan.approval.requested
    assert not _has(log, "fanout.started"), "writer fanout must HOLD before approval"
    requested = _requested(log)
    assert requested and requested[0].payload["task_count"] == 2
    plan_id = requested[0].payload["plan_id"]
    assert requested[0].payload.get("digest_ref") or requested[0].payload.get("task_map_ref")

    # (2) Feishu surfaces the held plan as a real card with a signed approve button
    t = MockFeishuTransport()
    pushed = push_plan_approval_cards_once(
        state_dir, t, receive_id=CHAT, action_secret=SECRET,
        web_base_url="http://w")
    assert pushed["sent"] == [plan_id]
    card = json.loads(t.sent_messages[-1].content)
    card_str = json.dumps(card, ensure_ascii=False)
    assert plan_id in card_str and "page=inbox" in card_str
    # signed approve button present (feishu-A2)
    token = ""
    for el in card.get("elements", []):
        for b in el.get("actions", []) if el.get("tag") == "action" else []:
            if str(b.get("value", {}).get("action", "")).startswith("plan-approve"):
                token = b["value"].get("t", "")
    assert token, "card must carry a signed plan-approve button"

    # (3) operator approves via the REAL ControlledAction path (Feishu/Web share it)
    result = _operator_approve_via_controlled_action(state_dir, log, orch, plan_id)
    assert result.get("ok") is True
    approved = [e for e in log.read_all() if e.type == "plan.approved"]
    assert approved and approved[-1].actor == "operator"
    assert "controlled-action" in approved[-1].payload.get("via", "")
    assert not approved[-1].payload.get("auto"), "operator approval is never auto"

    # (4) the gate truly UNLOCKS the real writer fanout
    orch.run_once(events=[approved[-1]])
    assert _has(log, "fanout.started"), "approval must unlock real writer fanout"


# --- E2: gate disabled → kernel auto-mint, no human, behaviour equivalent ---

def test_e2_disabled_auto_mints_and_proceeds(tmp_path):
    _, log, orch = _approval_orch(tmp_path, enabled=False)
    _approval_start(orch, log)
    assert _has(log, "fanout.started"), "disabled gate proceeds without approval"
    approved = [e for e in log.read_all() if e.type == "plan.approved"]
    assert approved and approved[0].payload.get("auto") is True


# --- E3: reject via real ControlledAction → stays held, reason auditable ----

def test_e3_reject_holds_and_records_reason(tmp_path):
    state_dir, log, orch = _approval_orch(tmp_path, enabled=True)
    _approval_start(orch, log)
    plan_id = _requested(log)[0].payload["plan_id"]

    result = _operator_approve_via_controlled_action(
        state_dir, log, orch, plan_id, action="plan-reject", reason="缺 assembly 段")
    assert result.get("ok") is True
    rejected = [e for e in log.read_all() if e.type == "plan.rejected"]
    assert rejected and rejected[-1].payload.get("reason") == "缺 assembly 段"

    orch.run_once(events=[rejected[-1]])
    assert not _has(log, "fanout.started"), "rejected plan must stay held"


# --- E4: idempotent — re-processing approval does not double-incubate -------

def test_e4_reapproval_does_not_double_incubate(tmp_path):
    state_dir, log, orch = _approval_orch(tmp_path, enabled=True)
    _approval_start(orch, log)
    plan_id = _requested(log)[0].payload["plan_id"]
    _operator_approve_via_controlled_action(state_dir, log, orch, plan_id)
    approved = [e for e in log.read_all() if e.type == "plan.approved"][-1]

    orch.run_once(events=[approved])
    first = sum(1 for e in log.read_all() if e.type == "fanout.started")
    # re-process the same approval event
    orch.run_once(events=[approved])
    second = sum(1 for e in log.read_all() if e.type == "fanout.started")
    assert first >= 1 and second == first, "re-approval must not re-incubate fanout"
