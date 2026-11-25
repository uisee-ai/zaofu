"""Goal projection must not depend on when the old triage timer expires."""
import pytest

from zf.core.events import ZfEvent
from zf.runtime.run_manager import _unresolved_action_effects


def facts():
    scope = {"workflow_run_id": "run", "task_map_generation": "gen"}
    effect = {**scope, "effect_id": "E", "action": "orchestrator-rework-triage",
              "source_event_id": "failure", "fanout_id": "old"}
    repair = {**scope, "stage_replan_generation": "repair-request",
              "stage_replan_generation_source_event_id": "failure"}
    def event(kind, key, body, cause=None):
        return ZfEvent(type=kind, id=key, origin="kernel", payload=body, causation_id=cause)
    return [
        event("candidate.ready", "original", {**scope, "candidate_head_commit": "sha", "status": "frozen"}),
        event("test.failed", "failure", {**scope, "fanout_id": "old", "stage_id": "verify",
                                         "candidate_event_id": "original"}),
        event("run.manager.action.effect.pending", "pending", effect),
        event("candidate.ready", "successor", {**repair, "repaired_failure_event_id": "failure",
              "candidate_snapshot_event_id": "original", "candidate_head_commit": "sha",
              "operator_authorized": True}, "request"),
        event("runtime.action.attempt.completed", "receipt", {"action": "stage-replan-new-generation",
              "status": "applied", "result_event_id": "successor"}),
        event("fanout.child.dispatched", "child", {**repair, "target_commit": "sha",
              "fanout_id": "new", "stage_id": "verify"}, "successor"),
        event("run.manager.action.effect.failed", "timeout", effect, "pending"),
    ]


def test_bound_replacement_retires_pending_and_late_timeout_without_passing_it():
    events = facts()
    assert _unresolved_action_effects(events[:5]) == (["E"], [])
    assert _unresolved_action_effects(events[:6]) == ([], [])
    assert _unresolved_action_effects(events) == ([], [])
    assert events[-1].type == "run.manager.action.effect.failed"
    assert _unresolved_action_effects([*events[:3], events[-1], *events[3:6]]) == ([], [])
    assert _unresolved_action_effects([*events[:4], events[5], events[4], events[6]]) == ([], [])


@pytest.mark.parametrize("idx,key,value", [
    (3, "operator_authorized", False), (3, "candidate_snapshot_event_id", "other"),
    (4, "status", "rejected"), (4, "result_event_id", "other"),
    (5, "target_commit", "other"), (5, "workflow_run_id", "other"),
    (5, "task_map_generation", "other"), (5, "stage_id", "other"),
    (5, "stage_replan_generation_source_event_id", "other"), (5, "fanout_id", "old"),
    (6, "source_event_id", "other"),
])
def test_unrelated_or_unadmitted_reader_cannot_clear_timeout(idx, key, value):
    events = facts()
    events[idx].payload = {**events[idx].payload, key: value}
    assert _unresolved_action_effects(events) == ([], ["E"])
    # A later repaired candidate alone cannot clear it either.
    assert _unresolved_action_effects([*events[:3], events[-1], *events[3:6]]) == ([], ["E"])


def test_goal_gate_keeps_other_blockers_when_replaced_triage_timeout_is_projected():
    from zf.runtime.run_manager import run_goal_completion_gate_event

    events = facts()
    start = ZfEvent(type="run.goal.started", payload={"run_id": "run"})
    claim = ZfEvent(type="run.goal.completion.claimed", correlation_id="run",
                    payload={"run_id": "run", "claim_id": "claim"})
    # Independent human decision remains blocking even after Reader replacement.
    human = ZfEvent(type="human.escalate", correlation_id="run",
                    payload={"decision_token": "owner", "blocking_scope": "release"})
    result = run_goal_completion_gate_event([start, *events, human, claim], claim=claim)
    assert result.type == "run.goal.completion.blocked"
    assert "pending_human_decision" in result.payload["blockers"]
    assert "failed_action_effect" not in result.payload["blockers"]
    del events[4]  # The control service did not apply this successor.
    result = run_goal_completion_gate_event([start, *events, human, claim], claim=claim)
    assert "failed_action_effect" in result.payload["blockers"]
