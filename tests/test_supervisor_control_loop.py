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
    assert len(messages) == 1
    assert decisions[0].payload["route"] == "run_manager_recovery"
    assert decisions[0].payload["fingerprint"] == "workflow_resume_batch:ck-low"
    assert decisions[0].payload["problem_envelope"]["problem_class"] == "workflow_progress"
    assert messages[0].payload["handled_by"] == "run-manager"
    assert messages[0].payload["human_action_required"] is False
    assert messages[0].payload["problem_envelope"]["owner_route"] == "run_manager"
    assert messages[0].payload["delivery_targets"] == ["web", "channel"]


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
