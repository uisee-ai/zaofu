"""A dispatched diagnosis is a handoff, not proof the task fault is gone."""

from tests.test_run_manager_rework_triage import _config, _state
from zf.core.events.model import ZfEvent
from zf.runtime.recovery_case import build_recovery_case_projection
from zf.runtime.run_manager import _post_verify_action, _settle_pending_action_effects


def test_triage_receipt_does_not_resolve_fault_and_sibling_cannot_resolve_it(tmp_path):
    state, log, writer = _state(tmp_path)
    action = {
        "action": "orchestrator-rework-triage",
        "safe_resume_action": "request_orchestrator_triage",
        "checkpoint_id": "triage-1", "request_id": "req-1",
        "task_id": "T1", "workflow_run_id": "run-1",
        "recovery_case_id": "case-1",
        "expected_downstream_events": ["orchestrator.rework.triage.requested"],
    }
    requested = writer.append(ZfEvent(type="orchestrator.rework.triage.requested",
        task_id="T1", correlation_id="run-1", payload=action))
    _post_verify_action(writer, action, {"ok": True, "emitted_event_ids": [requested.id]},
                        causation_id=requested.id, state_dir=state, config=_config())
    projection = build_recovery_case_projection(log.read_all())
    assert projection["cases"][0]["status"] != "resolved"
    log.append(ZfEvent(type="task.done", task_id="T2", correlation_id="run-1"))
    _settle_pending_action_effects(log.read_all(), writer)
    assert build_recovery_case_projection(log.read_all())["cases"][0]["status"] != "resolved"
    log.append(ZfEvent(type="task.done", task_id="T1", correlation_id="run-1"))
    assert _settle_pending_action_effects(log.read_all(), writer) == 1
    assert build_recovery_case_projection(log.read_all())["cases"][0]["status"] == "resolved"
    assert _settle_pending_action_effects(log.read_all(), writer) == 0
