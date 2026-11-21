"""A repair step's new outcome wakes recovery without waiting five minutes."""
from unittest.mock import MagicMock, patch

import pytest

from tests.test_tick_services import _FakeOrchestrator, _state
from zf.core.config.schema import ZfConfig
from zf.core.events import ZfEvent
from zf.runtime.tick_services import TickServiceIntervals, TickServiceState, run_standard_tick_services


@pytest.mark.parametrize("kind", ["run.manager.action.applied", "run.manager.action.failed"])
@pytest.mark.parametrize("action", ["repair-closeout-validate", "repair-closeout-apply", "repair-closeout-dispose"])
def test_repair_outcome_wakes_once_before_supervisor_inspection(tmp_path, kind, action):
    orch = _FakeOrchestrator(_state(tmp_path), ZfConfig())
    before = ZfEvent(type="runtime.attention.needed", origin="kernel")
    orch.event_log.append(before)
    state = TickServiceState(last_supervisor_inspection_at=100, last_run_manager_tick_at=100,
                             last_run_manager_input_event_id=before.id)
    orch.event_log.append(ZfEvent(type=kind, origin="kernel", payload={
        "action": action, "queue_id": "candidate-one", "repair_contract_digest": "frozen-contract",
    }))
    with patch("zf.runtime.tick_services._run_run_manager", return_value=MagicMock(changed=True, repairs_dispatched=0)) as manager:
        run_standard_tick_services(orch, state=state, now=105,
                                   intervals=TickServiceIntervals(supervisor_inspection_s=300))
        manager.assert_called_once()
        orch.event_log.append(ZfEvent(type="run.manager.tick.completed", origin="kernel"))
        run_standard_tick_services(orch, state=state, now=110,
                                   intervals=TickServiceIntervals(supervisor_inspection_s=300))
        manager.assert_called_once()


def test_owner_decision_wakes_runtime_before_periodic_inspection(tmp_path):
    orch = _FakeOrchestrator(_state(tmp_path), ZfConfig())
    before = ZfEvent(type="human.escalate", origin="kernel", payload={"decision_token": "owner-1"})
    orch.event_log.append(before)
    state = TickServiceState(last_supervisor_inspection_at=100, last_run_manager_tick_at=100,
                             last_run_manager_input_event_id=before.id)
    orch.event_log.append(ZfEvent(type="human.escalation.acknowledged", origin="external",
        payload={"decision_token": "owner-1", "decision": "approve_controlled_action"}))
    with patch("zf.runtime.tick_services._run_run_manager", return_value=MagicMock(changed=True, repairs_dispatched=0)) as manager:
        run_standard_tick_services(orch, state=state, now=105,
                                   intervals=TickServiceIntervals(supervisor_inspection_s=300))
        manager.assert_called_once()
        run_standard_tick_services(orch, state=state, now=110,
                                   intervals=TickServiceIntervals(supervisor_inspection_s=300))
        manager.assert_called_once()


@pytest.mark.parametrize("fault", ["worker", "unbound", "ordinary_action", "blocked"])
def test_only_bound_native_repair_outcomes_are_fast_inputs(tmp_path, fault):
    from zf.runtime.recovery_tick_wake import has_new_recovery_input

    payload = {"action": "repair-closeout-validate", "queue_id": "candidate-one", "repair_contract_digest": "frozen-contract"}
    origin, kind = "kernel", "run.manager.action.failed"
    if fault == "worker": origin = "worker"
    elif fault == "unbound": payload.pop("repair_contract_digest")
    elif fault == "ordinary_action": payload["action"] = "resident-agent-reprompt"
    else: kind = "run.manager.action.blocked"
    assert not has_new_recovery_input([ZfEvent(type=kind, origin=origin, payload=payload)], "")
