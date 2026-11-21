"""Historical task generations cannot own the current publication checkpoint."""
import pytest

from tests.test_task_host_actions import case
from tests.test_task_publication import prepare
from zf.core.events import ZfEvent
from zf.runtime.task_host_manifest import git
from zf.runtime.task_publication import reconcile_task_publication


def other_operation(service, identity, *, scope, status):
    other = {**identity, 'operation_id': 'other', 'operation_generation': 3}
    if scope == 'old_map':
        other['task_map_generation'] = 'historical-map'
    elif scope == 'old_run':
        other['workflow_run_id'] = 'historical-run'
    elif scope == 'unknown':
        other.pop('task_map_generation')
    service.writer.append(ZfEvent(type='workflow.operation.requested', payload=other))
    if status != 'requested':
        service.writer.append(ZfEvent(type='workflow.operation.' + status, payload=other))


@pytest.mark.parametrize('scope', ['old_map', 'old_run'])
@pytest.mark.parametrize('status', ['settled', 'cancelled', 'superseded'])
def test_historical_counter_cannot_block_current_publication_and_retry(case, scope, status):
    service, root = prepare(case)
    other_operation(service, case[2], scope=scope, status=status)
    before = git(root, 'rev-parse', 'HEAD')
    assert reconcile_task_publication(service) == 1
    assert git(root, 'rev-parse', 'HEAD') != before
    assert reconcile_task_publication(service) == 1
    assert reconcile_task_publication(service) == 0
    events = service.writer.event_log.read_all()
    assert sum(e.type == 'task.rework.requested' for e in events) == 1


@pytest.mark.parametrize('scope,status', [
    ('current', 'settled'), ('current', 'requested'),
    ('old_map', 'requested'), ('old_run', 'requested'), ('unknown', 'settled'),
])
def test_current_successor_or_live_or_ambiguous_owner_still_denies_publication(case, scope, status):
    service, root = prepare(case)
    other_operation(service, case[2], scope=scope, status=status)
    before = git(root, 'rev-parse', 'HEAD')
    assert reconcile_task_publication(service) == 0
    assert git(root, 'rev-parse', 'HEAD') == before
    assert not any(e.type == 'runtime.action.attempt.started'
                   for e in service.writer.event_log.read_all())


def test_typed_blocked_build_completion_publishes_and_requests_retry_once(case):
    # The semantic submit API emits dev.build.done even for an admitted blocked
    # implementation-result.v1; the transport event is not a passed verdict.
    service, root = prepare(case, event_type='dev.build.done')
    before = git(root, 'rev-parse', 'HEAD')
    assert reconcile_task_publication(service) == 1
    assert git(root, 'rev-parse', 'HEAD') != before
    assert reconcile_task_publication(service) == 1
    assert reconcile_task_publication(service) == 0
    events = service.writer.event_log.read_all()
    assert sum(e.type == 'task.rework.requested' for e in events) == 1
    assert not any(e.type in {'human.escalate', 'task.done'} for e in events)


def test_passed_semantics_cannot_gain_retry_from_build_done_transport(case):
    service, root = prepare(case, event_type='dev.build.done')
    service.writer.append(ZfEvent(type='workflow.call.result.admitted', payload={
        **case[2], 'semantic_verdict': 'passed'}))
    before = git(root, 'rev-parse', 'HEAD')
    assert reconcile_task_publication(service) == 0
    assert git(root, 'rev-parse', 'HEAD') == before
    assert not any(e.type in {'runtime.action.attempt.started', 'task.rework.requested'}
                   for e in service.writer.event_log.read_all())


def test_run_manager_tick_resumes_typed_publication_after_map_replacement(case):
    from zf.runtime.run_manager import run_manager_tick
    service, root = prepare(case, event_type='dev.build.done')
    other_operation(service, case[2], scope='old_map', status='settled')
    before = git(root, 'rev-parse', 'HEAD')
    for _ in range(2):
        run_manager_tick(state_dir=service.state_dir, writer=service.writer,
                         config=service.config, project_root=service.project_root, auto_execute=True)
    assert git(root, 'rev-parse', 'HEAD') != before
    events = service.writer.event_log.read_all()
    assert sum(e.type == 'task.rework.requested' for e in events) == 1
    assert not any(e.type == 'task.done' for e in events)
