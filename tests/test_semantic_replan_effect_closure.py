from zf.core.events.model import ZfEvent
from zf.runtime.run_manager import (
    _execute_semantic_replan_request,
    _settle_pending_action_effects,
)
from tests.test_run_manager import _state, _config


def test_replan_effect_closes_on_bound_map_not_reader_negative(tmp_path):
    state, log, writer = _state(tmp_path)
    action = {
        "action": "semantic-replan-request",
        "checkpoint_id": "replan-1",
        "semantic_replan_trigger": "flow.discovery.requested",
        "pdd_id": "PRD-1",
        "task_map_ref": "artifacts/task_map.json",
        "workflow_run_id": "run-1",
        "request_id": "request-1",
        "task_id": "T1",
        "source_event_id": "failure-1",
        "expected_downstream_events": ["flow.discovery.requested"],
    }
    assert (
        _execute_semantic_replan_request(
            writer=writer,
            action=action,
            causation_id="",
            state_dir=state,
            config=_config(),
        )
        == "applied"
    )
    # An admitted discovery reports missing product behavior and produces gaps;
    # this is not a failed attempt to deliver a replan.
    writer.append(
        ZfEvent(
            type="flow.discovery.failed",
            payload={
                "workflow_run_id": "run-1",
                "request_id": "request-1",
                "gap_task_count": 2,
                "task_id": "T1",
            },
        )
    )
    writer.append(
        ZfEvent(
            type="task.attempt.failed",
            task_id="T1",
            payload={"workflow_run_id": "run-1"},
        )
    )
    writer.append(
        ZfEvent(
            type="task_map.ready",
            payload={
                "workflow_run_id": "run-1",
                "request_id": "unrelated",
                "task_map_ref": "other",
            },
        )
    )
    assert _settle_pending_action_effects(log.read_all(), writer) == 0
    ready = writer.append(
        ZfEvent(
            type="task_map.ready",
            payload={
                "workflow_run_id": "run-1",
                "request_id": "request-1",
                "task_map_ref": "new-map",
            },
        )
    )
    assert _settle_pending_action_effects(log.read_all(), writer) == 1
    effect = [
        e for e in log.read_all() if e.type == "run.manager.action.effect.passed"
    ][-1]
    assert effect.payload["observed_event_id"] == ready.id
    assert not any(e.type == "task.done" for e in log.read_all())


def test_discovery_execution_failure_without_gap_still_fails(tmp_path):
    state, log, writer = _state(tmp_path)
    action = {
        "action": "semantic-replan-request",
        "checkpoint_id": "r1",
        "semantic_replan_trigger": "flow.discovery.requested",
        "pdd_id": "P1",
        "task_map_ref": "artifacts/map.json",
        "workflow_run_id": "run-1",
        "request_id": "r1",
        "task_id": "T1",
        "expected_downstream_events": ["flow.discovery.requested"],
    }
    assert (
        _execute_semantic_replan_request(
            writer=writer,
            action=action,
            causation_id="",
            state_dir=state,
            config=_config(),
        )
        == "applied"
    )
    writer.append(
        ZfEvent(
            type="flow.discovery.failed",
            payload={
                "workflow_run_id": "run-1",
                "request_id": "r1",
                "gap_task_count": 0,
            },
        )
    )
    assert _settle_pending_action_effects(log.read_all(), writer) == 1
    assert any(e.type == "run.manager.action.effect.failed" for e in log.read_all())
