"""Respawn must deliver the current sent pipeline attempt without claiming it again."""
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_task_pipeline_recovery_briefing import setup_attempt
from tests.integration.test_context_recycle_e2e import _FakeTransport
from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.events import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.workflow_operation import WorkflowOperationService


def recovered_case(tmp_path, monkeypatch):
    state, task, store, attempt, path, _ = setup_attempt(tmp_path)
    TaskStore(state/'kanban.json').add(task)
    config = ZfConfig(project=ProjectConfig(name='respawn', state_dir=str(state)),
        roles=[RoleConfig(name='dev-0', backend='mock')])
    config.workflow.task_attempt.mode = 'enforce'
    transport = _FakeTransport()
    runtime = Orchestrator(state, config, transport, project_root=tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    store.claim_delivery(attempt['attempt_id'], updated_at=now)
    store.mark_sent(attempt['attempt_id'], updated_at=now)
    store.renew_lease(attempt['attempt_id'], updated_at=now,
        lease_expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
    service = WorkflowOperationService(state_dir=state, event_log=runtime.event_log,
        event_writer=runtime.event_writer)
    op = service.ensure_operation(workflow_run_id='RUN', operation_id='op-new',
        operation_type='task-stage', task_id='T', role_instance='dev-0',
        request={'task_pipeline_stage':'impl','operation_generation':2,'task_map_generation':'map'})
    service.mark_started(operation_id='op-new', request_hash=op.request_hash,
        workflow_run_id='RUN', task_id='T', dispatch_id='dispatch-new', role_instance='dev-0',
        active_attempt_id=attempt['attempt_id'], lease_id=attempt['lease_id'])
    monkeypatch.setattr(runtime, '_ensure_recovery_contract_sufficient', lambda *_: True)
    return runtime, store, attempt, transport, path


def test_respawn_entrypoint_preserves_sent_attempt_and_typed_briefing(tmp_path, monkeypatch):
    runtime, store, attempt, transport, path = recovered_case(tmp_path, monkeypatch)
    before = store.current_rows()
    errors = []
    send = runtime._send_transport_task
    def observed_send(*args, **kwargs):
        try:
            return send(*args, **kwargs)
        except Exception as exc:
            errors.append(str(exc))
            raise
    monkeypatch.setattr(runtime, '_send_transport_task', observed_send)
    assert runtime._inject_recovery_briefing(runtime.config.roles[0]), errors
    assert len(transport.send_task_calls) == 1
    assert Path(transport.send_task_calls[0][1]).read_text().startswith(path.read_text())
    assert store.current_rows() == before
    assert not any(e.type in {'task.done', 'task.attempt.delivery_claimed', 'task.attempt.superseded'}
                   for e in runtime.event_log.read_all())


@pytest.mark.parametrize('fault', ['paused', 'expired', 'settled', 'dispatch', 'budget'])
def test_respawn_continuation_keeps_authority_fences(tmp_path, monkeypatch, fault):
    runtime, store, attempt, transport, _ = recovered_case(tmp_path, monkeypatch)
    if fault == 'paused': runtime.event_writer.append(ZfEvent(type='dispatch.paused', origin='kernel'))
    if fault == 'expired': store.renew_lease(attempt['attempt_id'],
        lease_expires_at='2000-01-01T00:00:00Z', updated_at=datetime.now(timezone.utc).isoformat())
    if fault == 'settled': runtime.event_writer.append(ZfEvent(type='workflow.operation.settled', origin='kernel',
        payload={'operation_id':'op-new','workflow_run_id':'RUN'}))
    if fault == 'dispatch': runtime.task_store.update('T', active_dispatch_id='stale')
    if fault == 'budget': monkeypatch.setattr(runtime, '_budget_exceeded', lambda *_: True)
    assert not runtime._inject_recovery_briefing(runtime.config.roles[0], inject_idle_prompt=False)
    assert not transport.send_task_calls
