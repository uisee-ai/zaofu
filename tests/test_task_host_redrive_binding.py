"""Prior attempts must not block or authorize current host effects."""
from dataclasses import replace

import pytest

from zf.runtime.host_verification import reconcile_host_verification
from zf.runtime.task_host_manifest import preview
from zf.runtime.workflow_operation import reduce_workflow_operations
from tests.test_task_host_verification_handoff import audit_case


def _historical_dispatch(service):
    current = next(e for e in service.writer.event_log.read_all()
                   if e.type == 'task.pipeline.stage.dispatched')
    return service.writer.append(replace(current, id='historical-dispatch', payload={
        **current.payload, 'dispatch_id':'dispatch-0', 'active_attempt_id':'attempt-0',
        'lease_id':'lease-0', 'workdir':'/unrelated/old/workspace'}))


@pytest.mark.parametrize('verify_only', [True, False])
def test_redriven_verify_runs_current_probe_and_resumes_from_actual_tick(tmp_path, monkeypatch, verify_only):
    from zf.runtime.run_manager import run_manager_tick
    service, root, calls = audit_case(tmp_path, monkeypatch, verify_only=verify_only)
    # Late archival/replay ordering does not make a prior dispatch current.
    _historical_dispatch(service)
    manifest = preview(service, operation_id='verify-1', kind='verify', command_id='probe')
    assert manifest['workdir'] == str(root)
    assert manifest['identity']['active_attempt_id'] == 'verify-attempt'
    for _ in range(2):
        run_manager_tick(state_dir=service.state_dir, writer=service.writer,
                         config=service.config, project_root=service.project_root, auto_execute=True)
    assert len(calls) == 1
    events = service.writer.event_log.read_all()
    assert reduce_workflow_operations(events)['verify-1']['status'] == 'superseded'
    assert not any(e.type in {'task.done','task.pipeline.verify.completed'} for e in events)


def test_duplicate_current_dispatch_cannot_execute_a_probe(tmp_path, monkeypatch):
    service, _, calls = audit_case(tmp_path, monkeypatch)
    current = next(e for e in service.writer.event_log.read_all()
                   if e.type == 'task.pipeline.stage.dispatched')
    service.writer.append(replace(current, id='duplicate-current'))
    with pytest.raises(ValueError, match='unique dispatch'):
        preview(service, operation_id='verify-1', kind='verify', command_id='probe')
    assert reconcile_host_verification(service) == 0
    assert calls == []


@pytest.mark.parametrize('fault', [
    'missing_current', 'worker_origin', 'attempt_id', 'conflicting_attempt',
    'lease_id', 'task_id', 'workflow_run_id', 'task_map_generation',
    'operation_generation', 'task_pipeline_stage', 'role_instance',
])
def test_current_dispatch_identity_corruption_cannot_authorize_host_effect(tmp_path, monkeypatch, fault):
    service, _, calls = audit_case(tmp_path, monkeypatch)
    events = service.writer.event_log.read_all()
    current = next(e for e in events if e.type == 'task.pipeline.stage.dispatched')
    payload = dict(current.payload)
    if fault == 'missing_current': payload['dispatch_id'] = 'old-dispatch'
    elif fault == 'conflicting_attempt': payload['attempt_id'] = 'another-attempt'
    elif fault == 'attempt_id': payload['active_attempt_id'] = 'another-attempt'
    elif fault != 'worker_origin': payload[fault] = 'other'
    changed = replace(current, payload=payload, origin='worker' if fault == 'worker_origin' else current.origin)
    monkeypatch.setattr(service.writer.event_log, 'read_all',
                        lambda: [changed if e.id == current.id else e for e in events])
    with pytest.raises(ValueError, match='dispatch'):
        preview(service, operation_id='verify-1', kind='verify', command_id='probe')
    assert reconcile_host_verification(service) == 0
    assert calls == []
