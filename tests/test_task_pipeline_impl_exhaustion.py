"""An exhausted Impl must reach recovery without authorizing another retry."""

from types import SimpleNamespace

import pytest

from zf.core.events import EventLog, EventWriter
from zf.runtime.run_manager_rework_triage import pending_rework_triage_actions
from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler
from zf.runtime.task_pipeline_semantic_exhaustion import (
    reconcile_task_pipeline_semantic_exhaustion,
)


def _projection(*, generation=4, verdict="blocked", repair=True, prior_verify=False):
    operations = [{
        "task_id": "A", "task_pipeline_stage": "impl", "status": "settled",
        "operation_generation": generation, "operation_id": "impl-current",
        "semantic_verdict": verdict,
        "call_result_admitted_event_id": "impl-admitted",
        "admitted_control_result_ref": {"ref": "result.json", "sha256": "a" * 64},
    }]
    if repair:
        operations.append({
            "task_id": "A", "task_pipeline_stage": "impl", "status": "superseded",
            "operation_generation": 1, "operation_id": "impl-unsent",
            "reason": "missing_flow_kind_before_dispatch",
        })
    if prior_verify:
        operations.append({
            "task_id": "A", "task_pipeline_stage": "verify", "status": "settled",
            "operation_generation": generation - 1, "operation_id": "verify-old",
            "semantic_verdict": "rejected", "last_event_id": "verify-old-event",
        })
    return TaskPipelineReconciler().reconcile(
        policy={"mode": "blocking", "max_rework_attempts": 2, "pools": {}},
        tasks=[{"id": "A", "status": "in_progress", "blocked_by": []}],
        operations=operations, attempts=[],
        impl_rework_requests={"A": {
            "operation_generation": generation, "fault": "task_pipeline_operator_retry",
        }} if verdict != "passed" else {},
    )


@pytest.mark.parametrize("prior_verify", [False, True])
def test_exhausted_impl_exposes_current_result(prior_verify):
    view = _projection(prior_verify=prior_verify)["tasks"][0]
    assert view["stage"] == "blocked"
    assert view["blockers"] == ["semantic_rework_exhausted"]
    assert view["semantic_blocker"]["stage"] == "impl"
    assert view["semantic_blocker"]["operation_id"] == "impl-current"
    assert view["semantic_blocker"]["source_event_id"] == "impl-admitted"
    assert view["semantic_blocker"]["max_rework_attempts"] == 2


@pytest.mark.parametrize("generation,verdict,expected", [
    (3, "blocked", "impl_rework_ready"),  # Unsent repair retains its credit.
    (4, "passed", "verify_ready"),
])
def test_nonexhausted_or_successful_impl_does_not_escalate(generation, verdict, expected):
    view = _projection(generation=generation, verdict=verdict)["tasks"][0]
    assert view["stage"] == expected
    assert view["semantic_blocker"] == {}


def test_impl_exhaustion_reaches_run_manager_once_across_restart(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    contexts = {"A": {"workflow_run_id": "RUN", "task_map_generation": "GEN",
                      "flow_kind": "prd", "profile_id": "prd-v4"}}
    for _ in range(2):
        runtime = SimpleNamespace(event_log=log, event_writer=EventWriter(log))
        reconcile_task_pipeline_semantic_exhaustion(
            runtime, projection=_projection(), generation_contexts=contexts,
        )
    events = log.read_all()
    assert len(events) == 1
    assert events[0].payload["stage_id"] == "impl"
    actions = pending_rework_triage_actions(events, threshold=3, stale_seconds=300)
    assert len(actions) == 1
    assert actions[0]["action"] == "orchestrator-rework-triage"
    assert actions[0]["failure_event_ids"] == ["impl-admitted"]
    assert actions[0]["flow_kind"] == "prd"
    from zf.runtime.run_manager import _pending_semantic_event_actions

    assert _pending_semantic_event_actions(events, triage_threshold=3) == []
    assert len(_pending_semantic_event_actions(events, triage_threshold=0)) == 1
