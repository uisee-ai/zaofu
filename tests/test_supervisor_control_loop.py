from __future__ import annotations

from zf.runtime.supervisor_control_loop import build_supervisor_control_loop_events


def test_control_loop_routes_low_severity_actionable_attention() -> None:
    events = build_supervisor_control_loop_events(
        {
            "attention_items": [
                {
                    "attention_id": "attn-low-action",
                    "fingerprint": "workflow_resume_batch:ck-low",
                    "source": "workflow_resume",
                    "severity": "low",
                    "status": "open",
                    "title": "Workflow checkpoint can resume",
                    "summary": "failed child retry is available",
                    "suggested_route": "run_manager_recovery",
                    "suggested_action": {
                        "kind": "workflow-batch-resume",
                        "checkpoint_id": "ck-low",
                        "safe_resume_action": "repair_failed_children",
                    },
                },
                {
                    "attention_id": "attn-low-info",
                    "fingerprint": "info:doc-note",
                    "source": "plan_integrity",
                    "severity": "low",
                    "status": "open",
                    "title": "Informational note",
                    "summary": "no owner action",
                    "suggested_route": "observe_only",
                },
            ],
        },
        events=[],
        projection_ref={},
    )

    decisions = [
        event for event in events
        if event.type == "supervisor.decision.recorded"
    ]
    messages = [
        event for event in events
        if event.type == "owner.visible_message.requested"
    ]
    assert len(decisions) == 1
    # 131 §16.3-4 triage-first 机械闸:RM 可先处置且非人类必需 → 只记
    # decision(outcome=run_manager_triage_first),不发 owner 消息。
    assert len(messages) == 0
    assert decisions[0].payload["route"] == "run_manager_recovery"
    assert decisions[0].payload["fingerprint"] == "workflow_resume_batch:ck-low"
    assert decisions[0].payload["problem_envelope"]["problem_class"] == "workflow_progress"
    assert decisions[0].payload["outcome"] == "run_manager_triage_first"


def test_control_loop_preserves_plan_admission_identity_without_owner_message() -> None:
    events = build_supervisor_control_loop_events(
        {"attention_items": [{
            "attention_id": "attn-plan-admission",
            "fingerprint": "plan_admission:incident-1",
            "status": "open",
            "source": "plan_admission",
            "severity": "medium",
            "title": "Plan admission requires bounded replan",
            "summary": "missing task-map ref",
            "task_id": "TASK-PLAN",
            "source_event_ids": ["evt-plan-failed"],
            "suggested_route": "plan_revision",
            "workflow_run_id": "run-1",
            "trace_id": "trace-1",
            "failure_scope": "plan_admission",
            "plan_admission_incident_id": "incident-1",
            "expected_fault": True,
            "notification_policy": "trace_only",
        }]},
        events=[],
        projection_ref={},
    )

    decisions = [
        event for event in events if event.type == "supervisor.decision.recorded"
    ]
    messages = [
        event for event in events if event.type == "owner.visible_message.requested"
    ]
    assert len(decisions) == 1
    payload = decisions[0].payload
    assert payload["route"] == "orchestrator_review"
    assert payload["source_event_ids"] == ["evt-plan-failed"]
    assert payload["workflow_run_id"] == "run-1"
    assert payload["trace_id"] == "trace-1"
    assert payload["failure_scope"] == "plan_admission"
    assert payload["plan_admission_incident_id"] == "incident-1"
    assert payload["expected_fault"] is True
    assert messages == []


def test_control_loop_suppresses_cost_blackout_until_run_manager_escalates() -> None:
    events = build_supervisor_control_loop_events(
        {
            "attention_items": [
                {
                    "attention_id": "attn-cost",
                    "fingerprint": "cost.usage.blackout:zf-cli",
                    "source": "workflow_runtime",
                    "severity": "high",
                    "status": "open",
                    "title": "Cost usage collection stopped while dispatch active",
                    "summary": (
                        "dispatch active but agent.usage stopped updating; "
                        "budget gate is deciding on a frozen total"
                    ),
                    "suggested_route": "run_manager_recovery",
                    "suggested_action": {
                        "kind": "diagnose_cost_collection",
                    },
                },
            ],
        },
        events=[],
        projection_ref={},
    )

    decisions = [
        event for event in events
        if event.type == "supervisor.decision.recorded"
    ]
    messages = [
        event for event in events
        if event.type == "owner.visible_message.requested"
    ]
    assert len(decisions) == 1
    assert decisions[0].payload["route"] == "run_manager_recovery"
    assert decisions[0].payload["outcome"] == "run_manager_triage_first"
    assert messages == []


def _budget_exceeded_item(**overrides) -> dict:
    # Real projection shape: problem_taxonomy.abnormal_event_projection
    # carries the registry spec's human_required_when through to the item.
    item = {
        "attention_id": "attn-budget",
        "fingerprint": (
            "cost.budget.exceeded:scope=global:budget_usd=60.0000"
        ),
        "source": "kernel_budget",
        "severity": "high",
        "status": "open",
        "title": "Cost budget exceeded",
        "summary": "budget exceeded; dispatch paused",
        "suggested_route": "run_manager_recovery",
        "notification_policy": "owner_on_human_required",
        "recovery_policy": "run_manager",
        "human_required_when": [
            "budget_level_changed",
            "owner_budget_decision_needed",
            "run_manager_no_progress",
        ],
        "suggested_action": {
            "kind": "diagnose_budget_exceeded",
            "event_type": "cost.budget.exceeded",
        },
    }
    item.update(overrides)
    return item


def test_control_loop_escalates_budget_exceeded_to_owner_first_fire() -> None:
    """ZF-E2E-RACING-P2: a hard budget cap is an owner decision by nature
    (owner_budget_decision_needed is intrinsic) — the first fire must produce
    an owner-visible message instead of being triaged into silence. Racing
    e2e 2026-07-11: 38 repeats froze the pipeline with zero escalation."""
    events = build_supervisor_control_loop_events(
        {"attention_items": [_budget_exceeded_item()]},
        events=[],
        projection_ref={},
    )

    decisions = [
        event for event in events
        if event.type == "supervisor.decision.recorded"
    ]
    messages = [
        event for event in events
        if event.type == "owner.visible_message.requested"
    ]
    assert len(decisions) == 1
    assert decisions[0].payload["notification_policy"] == "owner_on_human_required"
    assert len(messages) == 1
    assert messages[0].payload.get("human_action_required") is True


def test_control_loop_budget_repeat_folded_by_fingerprint_dedupe() -> None:
    first = build_supervisor_control_loop_events(
        {"attention_items": [_budget_exceeded_item()]},
        events=[],
        projection_ref={},
    )
    prior_messages = [
        event for event in first
        if event.type == "owner.visible_message.requested"
    ]
    assert len(prior_messages) == 1

    repeat = build_supervisor_control_loop_events(
        {"attention_items": [_budget_exceeded_item()]},
        events=first,
        projection_ref={},
    )
    messages = [
        event for event in repeat
        if event.type == "owner.visible_message.requested"
    ]
    assert messages == []


def test_control_loop_stateful_only_conditions_stay_triage_first() -> None:
    # Stateful conditions have no evaluator yet — declaring only those must
    # not force an owner escalation.
    events = build_supervisor_control_loop_events(
        {
            "attention_items": [
                _budget_exceeded_item(
                    human_required_when=[
                        "budget_level_changed",
                        "run_manager_no_progress",
                    ],
                ),
            ],
        },
        events=[],
        projection_ref={},
    )

    decisions = [
        event for event in events
        if event.type == "supervisor.decision.recorded"
    ]
    messages = [
        event for event in events
        if event.type == "owner.visible_message.requested"
    ]
    assert len(decisions) == 1
    assert decisions[0].payload["outcome"] == "run_manager_triage_first"
    assert messages == []


def test_control_loop_routes_human_gate_to_feishu() -> None:
    events = build_supervisor_control_loop_events(
        {
            "attention_items": [
                {
                    "attention_id": "attn-human",
                    "fingerprint": "human-gate:approval",
                    "source": "human_gate",
                    "severity": "high",
                    "status": "open",
                    "title": "Operator approval required",
                    "summary": "approve source repair merge and restart",
                    "suggested_route": "owner_notify",
                    "human_action_required": True,
                },
            ],
        },
        events=[],
        projection_ref={},
    )

    messages = [
        event for event in events
        if event.type == "owner.visible_message.requested"
    ]

    assert len(messages) == 1
    assert messages[0].payload["handled_by"] == "supervisor"
    assert messages[0].payload["human_action_required"] is True
    assert messages[0].payload["delivery_targets"] == ["web", "channel", "feishu"]


def test_control_loop_sends_run_manager_human_decision_to_feishu() -> None:
    events = build_supervisor_control_loop_events(
        {
            "attention_items": [
                {
                    "attention_id": "attn-rm-human",
                    "fingerprint": "run_manager_human_decision:hdec-r5",
                    "source": "run_manager_decision",
                    "severity": "high",
                    "status": "open",
                    "title": "Run Manager human decision",
                    "summary": "approve bounded resume",
                    "suggested_route": "run_manager_human_decision",
                    "human_action_required": True,
                    "decision_token": "hdec-r5",
                },
            ],
        },
        events=[],
        projection_ref={},
    )

    decisions = [
        event for event in events
        if event.type == "supervisor.decision.recorded"
    ]
    messages = [
        event for event in events
        if event.type == "owner.visible_message.requested"
    ]
    invocations = [
        event for event in events
        if event.type == "autoresearch.invocation.requested"
    ]

    assert len(decisions) == 1
    assert decisions[0].payload["route"] == "run_manager_human_decision"
    assert len(messages) == 1
    assert messages[0].payload["handled_by"] == "run-manager"
    assert messages[0].payload["delivery_targets"] == ["web", "channel", "feishu"]
    assert invocations == []


def test_triage_first_gate_never_suppresses_human_or_critical() -> None:
    """131 §16.3-4 forcing:human_action_required 与 critical 永不被 triage 闸压制。"""
    def _events_for(item):
        return build_supervisor_control_loop_events(
            {"attention_items": [item]}, events=[], projection_ref={},
        )

    base = {
        "source": "workflow_resume",
        "status": "open",
        "title": "t",
        "summary": "s",
        "suggested_route": "run_manager_recovery",
        "suggested_action": {"kind": "workflow-batch-resume", "checkpoint_id": "ck"},
    }
    human = _events_for({**base, "attention_id": "a-h", "fingerprint": "f:h",
                         "severity": "low", "human_action_required": True})
    critical = _events_for({**base, "attention_id": "a-c", "fingerprint": "f:c",
                            "severity": "critical"})
    triage = _events_for({**base, "attention_id": "a-t", "fingerprint": "f:t",
                          "severity": "medium"})

    def _messages(events):
        return [e for e in events if e.type == "owner.visible_message.requested"]

    assert len(_messages(human)) == 1
    assert len(_messages(critical)) == 1
    assert len(_messages(triage)) == 0
    triage_decisions = [e for e in triage if e.type == "supervisor.decision.recorded"]
    assert triage_decisions[0].payload["outcome"] == "run_manager_triage_first"


# ---------------------------------------------------------------------------
# 2026-07-17 card-quality review: owner_notify no longer stamps
# human_action_required (needs_diagnosis-class items were bypassing the
# Feishu push whitelist via the first gate — /tmp/runm.png).


def test_owner_notify_route_alone_does_not_stamp_human_action() -> None:
    from zf.runtime.supervisor_control_loop import _human_action_required

    item = {"title": "Completion event claims artifacts/head that do not exist",
            "summary": "claimed artifact missing on disk: x.md",
            "suggested_route": "workflow_rework"}
    decision = {"route": "owner_notify"}
    assert _human_action_required(item, decision) is False


def test_human_decision_route_still_stamps() -> None:
    from zf.runtime.supervisor_control_loop import _human_action_required

    assert _human_action_required({}, {"route": "human_decision"}) is True


def test_budget_intrinsic_condition_still_stamps() -> None:
    # ZF-E2E-RACING-P2 regression guard: the budget gate must keep opening
    # through human_required_when even with owner_notify removed.
    from zf.runtime.supervisor_control_loop import _human_action_required

    item = {"human_required_when": ["owner_budget_decision_needed"]}
    assert _human_action_required(item, {"route": "owner_notify"}) is True
