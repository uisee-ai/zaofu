from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.quiescent import quiescent_now


def event(kind, *, age=30, **kwargs):
    return ZfEvent(type=kind, ts=(datetime.now(timezone.utc)-timedelta(minutes=age)).isoformat(), **kwargs)


def status(events):
    return quiescent_now(events, config=SimpleNamespace(goal=SimpleNamespace(
        enabled=True, quiescent_after_escalate=True)), now_epoch=datetime.now(timezone.utc).timestamp())


@pytest.mark.parametrize('kind', ['task.done', 'task.dispatched', 'dev.blocked', 'dev.build.done', 'task.pipeline.verify.completed'])
def test_new_pipeline_fact_wakes_escalation(kind):
    payload = {'workflow_run_id': 'run', 'operation_id': 'op'}
    if kind.startswith('dev.') or kind == 'task.pipeline.verify.completed':
        payload['control_result_ref'] = {'ref': 'artifacts/admitted.json', 'sha256': 'a'*64}
    assert not status([event('human.escalate'), event(kind, age=2, origin='kernel', payload=payload)]).quiescent


def test_live_task_operation_prevents_quiescence_but_settled_does_not():
    payload = {'operation_id': 'op', 'workflow_run_id': 'run', 'operation_type': 'task-stage', 'request_hash': 'hash'}
    rows = [event('workflow.operation.requested', age=40, payload=payload), event('human.escalate')]
    assert not status(rows).quiescent
    rows.insert(1, event('workflow.operation.cancelled', age=35, payload=payload))
    assert status(rows).quiescent


def test_usage_or_unbound_worker_claim_cannot_keep_recovery_awake():
    for kind in ['agent.usage', 'worker.heartbeat', 'dev.build.done', 'dev.blocked']:
        assert status([event('human.escalate'), event(kind, age=2)]).quiescent


def test_actual_tick_services_reaches_run_manager_after_pipeline_failure(tmp_path):
    from tests.test_tick_services import _FakeOrchestrator, _state
    from zf.core.config.schema import ZfConfig, GoalConfig
    import zf.runtime.tick_services as tick
    config = ZfConfig(goal=GoalConfig(enabled=True, quiescent_after_escalate=True))
    orch = _FakeOrchestrator(_state(tmp_path), config)
    orch.event_log.append(event('human.escalate'))
    orch.event_log.append(event('dev.blocked', age=2, payload={
        'workflow_run_id': 'run', 'operation_id': 'op',
        'control_result_ref': {'ref': 'artifacts/admitted.json', 'sha256': 'a'*64},
    }))
    with patch.object(tick, '_run_run_manager', return_value=tick._empty_run_manager_result()) as run:
        tick.run_standard_tick_services(orch, state=tick.TickServiceState(), now=1000)
    assert run.call_count == 1
