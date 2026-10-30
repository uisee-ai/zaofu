from pathlib import Path

import pytest

from tests.test_goal_completion_gate_runtime import _orchestrator
from zf.core.events.model import ZfEvent
from zf.runtime.run_manager import run_goal_completion_gate_event
from zf.runtime.ship import ShipResult, ShipService


def _pending(tmp_path: Path):
    runtime, log = _orchestrator(tmp_path)
    runtime.config.workflow.flow_metadata = {"delivery_policy": "ship_candidate"}
    run_id, target = "RUN-RECOVER", "a" * 40
    identity = {"workflow_run_id": run_id, "task_map_generation": "g1"}
    claim = ZfEvent(type="run.goal.completion.claimed", correlation_id=run_id, payload={
        **identity, "run_id": run_id, "goal_id": run_id, "claim_id": "claim-recover",
        "claim_type": "admitted_goal_closure_result", "target_commit": target,
        "candidate_ref": "candidate/recover", "goal_claim_set_ref": "claims.json",
        "goal_claim_set_digest": "1" * 64, "closure_fact_ref": "closure.json",
        "closure_fact_digest": "2" * 64,
        "admitted_call_result_ref": {"ref": "closure-result.json", "sha256": "3" * 64},
    })
    for event in [
        ZfEvent(type="run.goal.started", correlation_id=run_id, payload={"run_id": run_id}),
        ZfEvent(type="candidate.ready", correlation_id=run_id, payload={
            **identity, "candidate_head_commit": target, "candidate_ref": "candidate/recover",
        }),
        ZfEvent(type="fanout.child.completed", correlation_id=run_id, payload={
            **identity, "candidate_ref": "candidate/recover", "target_commit": target,
            "control_result_schema": "verification-result.v1", "semantic_verdict": "passed",
            "admitted_call_result_ref": {"ref": "verify.json", "sha256": "4" * 64},
        }),
        ZfEvent(type="flow.goal.closed", correlation_id=run_id, payload={
            **identity, "goal_id": run_id, "candidate_head_commit": target,
            "goal_claim_set_digest": "1" * 64, "closure_fact_digest": "2" * 64,
        }),
        claim,
    ]:
        runtime.event_writer.append(event)
    request = run_goal_completion_gate_event(log.read_all(), claim=claim, delivery_policy="ship_candidate")
    assert request is not None and request.type == "run.delivery.requested"
    runtime.event_writer.append(request)
    return runtime, log, request


@pytest.mark.parametrize("phase", ["not_started", "started", "completed", "raises_after_started"])
def test_restart_recovers_delivery_without_repeating_unknown_effect(tmp_path, monkeypatch, phase):
    runtime, log, request = _pending(tmp_path)
    calls = []

    def ship(self, **kwargs):
        calls.append(kwargs)
        if phase == "raises_after_started":
            runtime.event_writer.append(ZfEvent(
                type="ship.started", causation_id=request.id, correlation_id=request.correlation_id,
                payload={"source_commit": request.payload["target_commit"]},
            ))
            raise OSError("transport lost after starting effect")
        return ShipResult(ok=True, status="completed", event_type="ship.completed", payload={})

    monkeypatch.setattr(ShipService, "ship", ship)
    if phase in {"started", "completed"}:
        runtime.event_writer.append(ZfEvent(
            type="ship.started", causation_id=request.id, correlation_id=request.correlation_id,
            payload={"source_commit": request.payload["target_commit"]},
        ))
    if phase == "completed":
        runtime.event_writer.append(ZfEvent(
            type="ship.completed", causation_id=request.id, correlation_id=request.correlation_id,
            payload={"source_commit": request.payload["target_commit"], "final_commit": "b" * 40},
        ))
    runtime._reconcile_run_goal_completion()
    runtime._reconcile_run_goal_completion()
    types = [e.type for e in log.read_all()]
    expected_calls = 1 if phase in {"not_started", "raises_after_started"} else 0
    assert len(calls) == expected_calls
    assert types.count("run.delivery.requested") == 1
    if phase in {"started", "raises_after_started"}:
        assert types.count("run.delivery.blocked") == 1
        assert "run.goal.completed" not in types
        from zf.runtime.run_manager import _pending_semantic_event_actions
        from zf.runtime.control_actions import ControlledActionService
        from tests.test_control_actions_surgery import _exec

        actions = _pending_semantic_event_actions(log.read_all())
        assert actions
        assert not any(a["action"] == "ship-retry" for a in actions)
        service = ControlledActionService(
            runtime.state_dir, runtime.event_writer,
            config=runtime.config, project_root=tmp_path,
        )
        result = _exec(service, "ship-retry", {
            "run_id": request.correlation_id, "claim_id": "claim-recover",
        })
        assert result.get("ok") is not True
        assert len(calls) == expected_calls
    else:
        assert types.count("run.delivery.settled") == 1
        assert types.count("run.goal.completed") == 1


def test_pending_delivery_rechecks_new_feedback_before_side_effect(tmp_path, monkeypatch):
    runtime, log, request = _pending(tmp_path)
    runtime.event_writer.append(ZfEvent(
        type="task.rework.requested", task_id="T1", correlation_id=request.correlation_id,
        payload={"workflow_run_id": request.correlation_id, "finding_ids": ["new-gap"]},
    ))

    def ship(*args, **kwargs):
        pytest.fail("a pending request must not bypass new blockers")

    monkeypatch.setattr(ShipService, "ship", ship)
    runtime._reconcile_run_goal_completion()
    assert "run.goal.completed" not in [e.type for e in log.read_all()]


def test_concurrent_gate_recovery_executes_pending_delivery_once(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    runtime, log, _ = _pending(tmp_path)
    calls = []

    def ship(self, **kwargs):
        calls.append(kwargs)
        return ShipResult(ok=True, status="completed", event_type="ship.completed", payload={})

    monkeypatch.setattr(ShipService, "ship", ship)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: runtime._reconcile_run_goal_completion(), range(2)))
    assert len(calls) == 1
    types = [e.type for e in log.read_all()]
    assert types.count("run.delivery.requested") == 1
    assert types.count("run.delivery.settled") == 1
    assert types.count("run.goal.completed") == 1
