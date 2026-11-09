from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.run_continuation import (
    build_run_continuation_projection,
    enrich_continuation_actions,
    progress_digest,
)


def _goal(status: str = "active") -> dict[str, str]:
    return {
        "run_id": "RUN-1",
        "status": status,
        "source_event_id": "goal-terminal" if status != "active" else "goal-start",
    }


def _events(generation: str = "GEN-1") -> list[ZfEvent]:
    return [
        ZfEvent(
            id="goal-start",
            type="run.goal.started",
            correlation_id="RUN-1",
            payload={"run_id": "RUN-1", "objective": "ship product"},
        ),
        ZfEvent(
            id=f"task-map-{generation}",
            type="task_map.ready",
            correlation_id="RUN-1",
            payload={
                "workflow_run_id": "RUN-1",
                "task_map_generation": generation,
            },
        ),
        ZfEvent(
            id=f"claim-{generation}",
            type="goal.claim_set.pinned",
            correlation_id="RUN-1",
            payload={
                "workflow_run_id": "RUN-1",
                "task_map_generation": generation,
            },
        ),
    ]


def _actions() -> list[dict]:
    return [
        {
            "action": "workflow-batch-resume",
            "safe_resume_action": "repair_failed_children",
            "checkpoint_id": "wfres-1",
            "workflow_run_id": "RUN-1",
            "pdd_id": "P1",
            "failure_class": "implementation_failed",
            "attempt_cap": 2,
            "policy_decision": {"decision": "auto_decide"},
            "preflight": {"status": "passed"},
        },
        {
            "action": "diagnose-attention",
            "safe_resume_action": "diagnose",
            "checkpoint_id": "diag-1",
            "workflow_run_id": "RUN-1",
            "failure_class": "unknown_gap",
            "policy_decision": {"decision": "needs_diagnosis"},
        },
    ]


def test_run_continuation_replay_is_stable_and_selects_one_operation() -> None:
    events = _events()
    digest = progress_digest(events)
    actions = enrich_continuation_actions(
        _actions(),
        run_id="RUN-1",
        generation="GEN-1",
        current_progress_digest=digest,
    )

    first = build_run_continuation_projection(
        events,
        goal=_goal(),
        pending_actions=actions,
        completion_profile={"status": "incomplete"},
    )
    replay = build_run_continuation_projection(
        list(events),
        goal=_goal(),
        pending_actions=[dict(action) for action in actions],
        completion_profile={"status": "incomplete"},
    )

    assert replay == first
    assert first["status"] == "active"
    assert first["pending_operation_count"] == 2
    assert first["next_operation"]["operation_key"] == actions[0]["operation_key"]
    assert first["next_operation"]["operation_attempt_cap"] == 2
    assert first["next_operation"]["operation_deadline"] == {
        "kind": "attempt_cap",
        "max_attempts": 2,
    }


def test_generation_changes_operation_identity_and_progress_digest() -> None:
    first_events = _events("GEN-1")
    second_events = [*first_events, *_events("GEN-2")[1:]]
    first = enrich_continuation_actions(
        _actions(),
        run_id="RUN-1",
        generation="GEN-1",
        current_progress_digest=progress_digest(first_events),
    )[0]
    second = enrich_continuation_actions(
        _actions(),
        run_id="RUN-1",
        generation="GEN-2",
        current_progress_digest=progress_digest(second_events),
    )[0]

    assert first["operation_key"] != second["operation_key"]
    assert first["operation_precondition"]["progress_digest"] != (
        second["operation_precondition"]["progress_digest"]
    )


def test_terminal_run_has_no_next_operation() -> None:
    events = [
        *_events(),
        ZfEvent(
            id="delivery-settled",
            type="run.delivery.settled",
            correlation_id="RUN-1",
            payload={"run_id": "RUN-1"},
        ),
        ZfEvent(
            id="goal-terminal",
            type="run.goal.completed",
            correlation_id="RUN-1",
            payload={"run_id": "RUN-1"},
        ),
    ]
    actions = enrich_continuation_actions(
        _actions(),
        run_id="RUN-1",
        generation="GEN-1",
        current_progress_digest=progress_digest(events),
    )

    projection = build_run_continuation_projection(
        events,
        goal=_goal("complete"),
        pending_actions=actions,
        completion_profile={"status": "complete"},
    )

    assert projection["status"] == "completed"
    assert projection["terminal"] is True
    assert projection["terminal_event_id"] == "goal-terminal"
    assert projection["next_operation"] is None


def test_missing_delivery_evidence_does_not_mark_active_run_completed() -> None:
    projection = build_run_continuation_projection(
        _events(),
        goal=_goal(),
        pending_actions=[],
        completion_profile={"status": "incomplete"},
    )

    assert projection["status"] == "active"
    assert projection["terminal"] is False
    assert projection["next_operation"] is None


def test_blocking_human_decision_pauses_pending_continuation() -> None:
    events = _events()
    actions = enrich_continuation_actions(
        _actions(),
        run_id="RUN-1",
        generation="GEN-1",
        current_progress_digest=progress_digest(events),
    )

    projection = build_run_continuation_projection(
        events,
        goal=_goal(),
        pending_actions=actions,
        completion_profile={
            "status": "incomplete",
            "pending_human_decisions": [{
                "decision_token": "hdec-1",
                "blocking_scope": "run",
                "checkpoint_id": "manual-gate",
                "approval_command": "zf action approve manual-gate",
            }],
        },
    )

    assert projection["terminal"] is False
    assert projection["status"] == "active"
    assert projection["pending_operation_count"] == 2
    assert projection["next_operation"] is None


def test_manual_evidence_gate_can_replace_legacy_bare_human_notice() -> None:
    events = _events()
    actions = enrich_continuation_actions(
        [{
            "action": "orchestrator-triage-advice-apply",
            "safe_resume_action": "blocked_external_gate",
            "checkpoint_id": "manual-evidence-gate-1",
            "task_id": "TASK-VERIFY",
            "failure_class": "manual_evidence_required",
            "attempt_cap": 1,
            "policy_decision": {"decision": "human_escalate"},
            "preflight": {"status": "passed"},
        }],
        run_id="RUN-1",
        generation="GEN-1",
        current_progress_digest=progress_digest(events),
    )

    projection = build_run_continuation_projection(
        events,
        goal=_goal(),
        pending_actions=actions,
        completion_profile={
            "status": "incomplete",
            "pending_human_decisions": [{
                "decision_token": "legacy-event-id",
                "event_id": "legacy-event-id",
                "blocking_scope": "run",
            }],
        },
    )

    assert projection["next_operation"]["checkpoint_id"] == (
        "manual-evidence-gate-1"
    )


def test_bounded_human_gate_diagnosis_can_replace_legacy_bare_notice() -> None:
    events = _events()
    actions = enrich_continuation_actions(
        [{
            "action": "diagnose-attention",
            "safe_resume_action": "diagnose_attention",
            "checkpoint_id": "attention-diagnosis-1",
            "failure_class": "human_attention_delivery",
            "policy_decision": {"decision": "needs_diagnosis"},
        }],
        run_id="RUN-1",
        generation="GEN-1",
        current_progress_digest=progress_digest(events),
    )

    projection = build_run_continuation_projection(
        events,
        goal=_goal(),
        pending_actions=actions,
        completion_profile={
            "status": "incomplete",
            "pending_human_decisions": [{
                "decision_token": "legacy-event-id",
                "event_id": "legacy-event-id",
                "blocking_scope": "run",
            }],
        },
    )

    assert projection["next_operation"]["checkpoint_id"] == (
        "attention-diagnosis-1"
    )


def test_no_progress_diagnosis_does_not_deadlock_on_bare_notice() -> None:
    events = _events()
    action = {
        "action": "diagnose-attention", "safe_resume_action": "diagnose_attention",
        "checkpoint_id": "no-progress-diagnosis", "failure_class": "no_progress_breaker",
        "policy_decision": {"decision": "needs_diagnosis"},
    }
    actions = enrich_continuation_actions(
        [action], run_id="RUN-1", generation="GEN-1",
        current_progress_digest=progress_digest(events),
    )
    bare = {"event_id": "old-escalation", "blocking_scope": "run"}
    completion = {"status": "incomplete", "pending_human_decisions": [bare]}
    projection = build_run_continuation_projection(
        events, goal=_goal(), pending_actions=actions,
        completion_profile=completion,
    )
    assert projection["next_operation"]["checkpoint_id"] == "no-progress-diagnosis"
    assert completion["pending_human_decisions"] == [bare]

    for explicit in (
        {**bare, "checkpoint_id": "human-approval-gate"},
        {**bare, "approval_command": "zf approve gate"},
    ):
        blocked = build_run_continuation_projection(
            events, goal=_goal(), pending_actions=actions,
            completion_profile={
                "status": "incomplete", "pending_human_decisions": [bare, explicit],
            },
        )
        assert blocked["next_operation"] is None

    action["policy_decision"] = {"decision": "auto_decide"}
    other_policy = enrich_continuation_actions(
        [action], run_id="RUN-1", generation="GEN-1",
        current_progress_digest=progress_digest(events),
    )
    blocked = build_run_continuation_projection(
        events, goal=_goal(), pending_actions=other_policy,
        completion_profile=completion,
    )
    assert blocked["next_operation"] is None


def test_explicit_human_gate_blocks_repair_diagnosis() -> None:
    events = _events()
    actions = enrich_continuation_actions(
        [{
            "action": "diagnose-attention",
            "safe_resume_action": "diagnose_attention",
            "kind": "human_gate_repair_resume",
            "checkpoint_id": "human-gate-repair-1",
            "failure_class": "candidate_rework_exhausted_unresolved",
            "policy_decision": {"decision": "needs_diagnosis"},
        }],
        run_id="RUN-1",
        generation="GEN-1",
        current_progress_digest=progress_digest(events),
    )

    projection = build_run_continuation_projection(
        events,
        goal=_goal(),
        pending_actions=actions,
        completion_profile={
            "status": "incomplete",
            "pending_human_decisions": [{
                "decision_token": "hdec-explicit",
                "blocking_scope": "run",
                "checkpoint_id": "manual-evidence-gate-1",
                "approval_command": "zf action approve manual-evidence-gate-1",
            }],
        },
    )

    assert projection["next_operation"] is None


def test_legacy_bare_human_notice_still_pauses_non_gate_operation() -> None:
    events = _events()
    actions = enrich_continuation_actions(
        _actions(),
        run_id="RUN-1",
        generation="GEN-1",
        current_progress_digest=progress_digest(events),
    )

    projection = build_run_continuation_projection(
        events,
        goal=_goal(),
        pending_actions=actions,
        completion_profile={
            "status": "incomplete",
            "pending_human_decisions": [{
                "decision_token": "legacy-event-id",
                "event_id": "legacy-event-id",
                "blocking_scope": "run",
            }],
        },
    )

    assert projection["next_operation"] is None


def test_run_continuation_excludes_interleaved_run_facts() -> None:
    events = [
        ZfEvent(
            type="run.goal.started",
            correlation_id="RUN-A",
            payload={"run_id": "RUN-A"},
        ),
        ZfEvent(
            type="run.goal.started",
            correlation_id="RUN-B",
            payload={"run_id": "RUN-B"},
        ),
        ZfEvent(
            type="task_map.ready",
            correlation_id="RUN-A",
            payload={"run_id": "RUN-A", "task_map_generation": "GEN-A"},
        ),
        ZfEvent(
            type="task_map.ready",
            correlation_id="RUN-B",
            payload={"run_id": "RUN-B", "task_map_generation": "GEN-B"},
        ),
        ZfEvent(
            type="run.goal.completed",
            correlation_id="RUN-B",
            payload={"run_id": "RUN-B"},
        ),
    ]

    projection = build_run_continuation_projection(
        events,
        goal={"run_id": "RUN-A", "status": "active"},
        pending_actions=[],
        completion_profile={"status": "incomplete"},
    )

    assert projection["generation"] == "GEN-A"
    assert projection["status"] == "active"
    assert projection["terminal"] is False


def test_prd_shaped_gap_generation_converges_to_one_delivery_terminal() -> None:
    events = [
        ZfEvent(
            id="invoke",
            type="workflow.invoke.requested",
            correlation_id="RUN-1",
            payload={"run_id": "RUN-1", "prompt_ref": "prompts/product.md"},
        ),
        *_events("GEN-1"),
        ZfEvent(
            id="candidate-gen-1",
            type="candidate.ready",
            correlation_id="RUN-1",
            payload={"run_id": "RUN-1", "task_map_generation": "GEN-1"},
        ),
        ZfEvent(
            id="verify-gap",
            type="verify.failed",
            correlation_id="RUN-1",
            payload={"run_id": "RUN-1", "reason": "goal gap"},
        ),
        ZfEvent(
            id="gap-map-gen-2",
            type="task_map.amended",
            correlation_id="RUN-1",
            payload={
                "run_id": "RUN-1",
                "task_map_generation": "GEN-2",
                "supersedes_generation": "GEN-1",
                "added_task_ids": ["TASK-GAP-1"],
            },
        ),
        ZfEvent(
            id="claim-gen-2",
            type="goal.claim_set.pinned",
            correlation_id="RUN-1",
            payload={"run_id": "RUN-1", "task_map_generation": "GEN-2"},
        ),
        ZfEvent(
            id="gap-task-done",
            type="task.done",
            task_id="TASK-GAP-1",
            correlation_id="RUN-1",
            payload={"run_id": "RUN-1", "task_id": "TASK-GAP-1"},
        ),
        ZfEvent(
            id="candidate-gen-2",
            type="candidate.ready",
            correlation_id="RUN-1",
            payload={"run_id": "RUN-1", "task_map_generation": "GEN-2"},
        ),
        ZfEvent(
            id="verify-pass",
            type="verify.passed",
            correlation_id="RUN-1",
            payload={"run_id": "RUN-1", "target_commit": "a" * 40},
        ),
        ZfEvent(
            id="judge-pass",
            type="judge.passed",
            correlation_id="RUN-1",
            payload={"run_id": "RUN-1", "target_commit": "a" * 40},
        ),
        ZfEvent(
            id="delivery-settled",
            type="run.delivery.settled",
            correlation_id="RUN-1",
            payload={"run_id": "RUN-1", "target_commit": "a" * 40},
        ),
        ZfEvent(
            id="goal-terminal",
            type="run.goal.completed",
            correlation_id="RUN-1",
            payload={"run_id": "RUN-1", "target_commit": "a" * 40},
        ),
    ]

    projection = build_run_continuation_projection(
        events,
        goal=_goal("complete"),
        pending_actions=_actions(),
        completion_profile={"status": "complete"},
    )

    assert projection["generation"] == "GEN-2"
    assert projection["status"] == "completed"
    assert projection["next_operation"] is None
    assert sum(event.type == "run.goal.completed" for event in events) == 1
    assert any(event.type == "run.delivery.settled" for event in events)
