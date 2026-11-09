"""Provider failure recovery stays visible to Run Manager without granting retry."""
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from tests.test_provider_turn_error import emit_error, runtime_case
from zf.runtime.task_attempt_operation_settlement import settle_terminal_operation_attempt
from zf.runtime.task_attempt_recovery import pending_task_attempt_recovery_actions
from zf.runtime.task_attempt_runtime import reconcile_task_attempts


def recovery_actions(runtime):
    return pending_task_attempt_recovery_actions(runtime.state_dir / "projections",
        canonical_store_path=runtime.state_dir / "task_attempts.json")


def test_actual_provider_error_exposes_non_retryable_run_manager_case(tmp_path):
    from zf.runtime.run_manager import build_run_manager_projection

    runtime, store, attempt, transport = runtime_case(tmp_path)
    runtime.run_once(events=[emit_error(runtime)])
    row = store.get(attempt["attempt_id"])
    assert row["recovery_owner"] == "run_manager"
    assert row["failure_class"] == "provider_stop:provider_policy_rejected"
    assert row["retryable"] is False
    actions = recovery_actions(runtime)
    assert len(actions) == 1
    assert actions[0]["failure_class"] == row["failure_class"]
    assert actions[0]["action"] == "diagnose-attention"
    assert actions[0]["policy_decision"]["executable"] is False
    assert actions[0]["action_policy"] == "human_escalate"
    assert not transport.sends
    failure = next(e for e in runtime.event_log.read_all() if e.type == "task.attempt.failed")
    assert failure.payload["recovery_owner"] == "run_manager"
    projection = build_run_manager_projection(runtime.state_dir, config=runtime.config,
        events=runtime.event_log.read_all(), project_root=tmp_path)
    pending = [row for row in projection["pending_actions"]
               if row.get("attempt_id") == attempt["attempt_id"]]
    assert len(pending) == 1 and pending[0]["action_policy"] == "human_escalate"


def test_reconcile_repairs_old_terminal_metadata_without_reopening_attempt(tmp_path):
    runtime, store, attempt, transport = runtime_case(tmp_path)
    runtime.run_once(events=[emit_error(runtime)])
    current = store.get(attempt["attempt_id"])
    source_id = current["terminal_event_id"]
    # Simulate the old deterministic projection, preserving the original ledger.
    store.update(attempt["attempt_id"], status="failed", updated_at=datetime.now(timezone.utc).isoformat(), recovery_owner="workflow",
                 failure_class="workflow_operation_terminal")
    before_events = [e.id for e in runtime.event_log.read_all() if e.type == "task.attempt.failed"]
    assert recovery_actions(runtime) == []
    assert reconcile_task_attempts(runtime) >= 1
    row = store.get(attempt["attempt_id"])
    assert row["recovery_owner"] == "run_manager"
    assert row["terminal_event_id"] == source_id
    assert row["status"] == "failed" and row["retryable"] is False
    assert len(recovery_actions(runtime)) == 1
    assert reconcile_task_attempts(runtime) == 0
    assert [e.id for e in runtime.event_log.read_all() if e.type == "task.attempt.failed"] == before_events
    assert not transport.sends


@pytest.mark.parametrize("case", ["ordinary", "reason_only", "wrong_class", "different_terminal"])
def test_unrelated_or_unbound_failure_retains_workflow_ownership(tmp_path, case):
    runtime, store, attempt, _ = runtime_case(tmp_path)
    runtime.run_once(events=[emit_error(runtime)])
    original = next(e for e in runtime.event_log.read_all() if e.type == "workflow.operation.blocked")
    store.update(attempt["attempt_id"], status="failed", updated_at=datetime.now(timezone.utc).isoformat(), recovery_owner="workflow",
                 failure_class="workflow_operation_terminal")
    payload = dict(original.payload)
    if case == "ordinary":
        payload.pop("provider_stop_reason", None)
        payload["failure_class"] = "semantic_result_failed"
    elif case == "reason_only":
        payload.pop("provider_stop_reason", None)
    elif case == "wrong_class":
        payload["failure_class"] = "provider_stop:auth_error"
    else:
        store.update(attempt["attempt_id"], status="failed", updated_at=datetime.now(timezone.utc).isoformat(), terminal_event_id="another-terminal")
    assert not settle_terminal_operation_attempt(runtime, replace(original, payload=payload))
    assert store.get(attempt["attempt_id"])["recovery_owner"] == "workflow"
