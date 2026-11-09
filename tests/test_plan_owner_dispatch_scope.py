"""Owner replan scope is distinct from read priority and cannot mutate live tasks."""
from types import SimpleNamespace

import pytest

from tests.test_control_actions_plan import _service, _exec
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.replan_resynth import build_replan_resynth_event


def _pending(service, log):
    service.config.workflow.admission_replan.enabled = True
    service.config.workflow.admission_replan.resynth_trigger = 'prd.scan.completed'
    log.append(ZfEvent(type='prd.scan.completed', payload={
        'trace_id': 'run', 'workflow_run_id': 'run', 'pdd_id': 'P', 'task_ids': ['OLD-GAP'],
        'resume_scope': 'gap_tasks_only'}))
    pending = ZfEvent(type='plan.approval.requested', payload={
        'plan_id': 'plan', 'workflow_run_id': 'run', 'trace_id': 'run', 'pdd_id': 'P',
        'stage_id': 'prd-lanes-impl', 'task_map_ref': 'plan.json'})
    log.append(pending)
    return pending


def _resynth(service, log, rejection):
    plan = SimpleNamespace(pdd_id='P', trace_id='run', workflow_run_id='run',
        source_event_id=rejection.id, source_event_type='plan.rejected', attempt=1, flow_kind='prd',
        task_ids=['OLD-GAP'], resume_scope='gap_tasks_only')
    return build_replan_resynth_event(plan=plan, events=log.read_all(), config=service.config,
                                     state_dir=service.state_dir)


def test_controlled_owner_scope_reaches_planner_without_seeding_tasks(tmp_path):
    service, log = _service(tmp_path)
    pending = _pending(service, log)
    store = TaskStore(service.state_dir / 'kanban.json')
    store.add(Task(id='OLD', title='old', status='done'))
    store.add(Task(id='INTEGRATE', title='integration', status='backlog', blocked_by=['OLD']))
    before = (service.state_dir / 'kanban.json').read_bytes()
    result = _exec(service, log, 'plan-reject', {'plan_id': 'plan', 'request_event_id': pending.id,
        'reason': 'Review independent slices and update the integration dependency.',
        'dispatch_task_ids': ['OLD-GAP', 'NEW-CORE', 'INTEGRATE'], 'input_task_ids': ['INTEGRATE']})
    assert result['ok'], result
    rejected = next(e for e in log.read_all() if e.type == 'plan.rejected')
    assert rejected.payload['dispatch_task_ids'] == ['OLD-GAP', 'NEW-CORE', 'INTEGRATE']
    trigger = _resynth(service, log, rejected)
    assert trigger.payload['task_ids'] == rejected.payload['dispatch_task_ids']
    assert trigger.payload['input_task_ids'] == ['INTEGRATE']
    assert trigger.payload['resume_scope'] == 'gap_tasks_only'
    assert (service.state_dir / 'kanban.json').read_bytes() == before
    assert store.get('NEW-CORE') is None
    assert not any(e.type in {'task_map.ready', 'plan.approved', 'task.created'} for e in log.read_all())


@pytest.mark.parametrize('scope', [[], 'T', [''], [3], ['x\ny'], ['T'] * 129])
def test_invalid_scope_keeps_approval_pending(tmp_path, scope):
    service, log = _service(tmp_path)
    _pending(service, log)
    result = _exec(service, log, 'plan-reject',
        {'plan_id': 'plan', 'reason': 'refine', 'dispatch_task_ids': scope})
    assert not result['ok']
    assert not any(e.type == 'plan.rejected' for e in log.read_all())


@pytest.mark.parametrize('status', ['in_progress', 'done', 'cancelled'])
def test_owner_scope_does_not_rebind_running_or_terminal_tasks(tmp_path, status):
    service, log = _service(tmp_path)
    _pending(service, log)
    store = TaskStore(service.state_dir / 'kanban.json')
    store.add(Task(id='PROTECTED', title='protected', status=status))
    result = _exec(service, log, 'plan-reject',
        {'plan_id': 'plan', 'reason': 'refine', 'dispatch_task_ids': ['PROTECTED', 'NEW']})
    assert not result['ok']
    assert store.get('PROTECTED').status == status
    assert not any(e.type == 'plan.rejected' for e in log.read_all())


def test_approve_cannot_expand_execution_scope(tmp_path):
    service, log = _service(tmp_path)
    _pending(service, log)
    result = _exec(service, log, 'plan-approve', {'plan_id': 'plan', 'dispatch_task_ids': ['NEW']})
    assert not result['ok']
    assert not any(e.type == 'plan.approved' for e in log.read_all())


def test_read_priority_alone_does_not_change_dispatch(tmp_path):
    service, log = _service(tmp_path)
    _pending(service, log)
    result = _exec(service, log, 'plan-reject', {'plan_id': 'plan', 'reason': 'read this first',
                                             'input_task_ids': ['INTEGRATE']})
    assert result['ok']
    rejected = next(e for e in log.read_all() if e.type == 'plan.rejected')
    assert _resynth(service, log, rejected).payload['task_ids'] == ['OLD-GAP']


def test_cli_can_request_an_explicit_new_dispatch_scope(tmp_path, monkeypatch):
    from zf.cli.main import main

    service, log = _service(tmp_path)
    _pending(service, log)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('ZF_STATE_DIR', str(service.state_dir))
    assert main(['plan', 'reject', 'plan', '--reason', 'review split',
                 '--dispatch-task', 'NEW', '--dispatch-task', 'INTEGRATE']) == 0
    rejected = next(e for e in log.read_all() if e.type == 'plan.rejected')
    assert rejected.payload['dispatch_task_ids'] == ['NEW', 'INTEGRATE']
    assert rejected.origin == 'kernel'
    assert _resynth(service, log, rejected).payload['task_ids'] == ['NEW', 'INTEGRATE']


@pytest.mark.parametrize('actor,request_id,origin', [
    ('planner', 'bound', 'kernel'), ('operator', 'missing', 'kernel'),
    ('operator', 'bound', 'worker'), ('operator', 'bound', '')])
def test_resynth_rejects_unbound_scope_claims(tmp_path, actor, request_id, origin):
    service, log = _service(tmp_path)
    pending = _pending(service, log)
    event = ZfEvent(type='plan.rejected', actor=actor, origin=origin, payload={
        'plan_id': 'plan', 'workflow_run_id': 'run',
        'request_event_id': pending.id if request_id == 'bound' else request_id,
        'dispatch_task_ids': ['NEW']})
    log.append(event)
    with pytest.raises(ValueError, match='bound operator'):
        _resynth(service, log, event)


def test_scope_stays_bound_in_planner_inputs_and_writer_selection(tmp_path):
    from zf.runtime.plan_synth_handoff import build_plan_handoff_input_refs
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    from zf.runtime.writer_fanout_admission import select_writer_task_items

    service, log = _service(tmp_path)
    _pending(service, log)
    assert _exec(service, log, 'plan-reject', {'plan_id': 'plan', 'reason': 'review slices',
        'dispatch_task_ids': ['OLD-GAP', 'NEW-CORE', 'INTEGRATE'], 'input_task_ids': ['INTEGRATE']})['ok']
    event = next(e for e in log.read_all() if e.type == 'plan.rejected')
    trigger = _resynth(service, log, event)
    refs = build_plan_handoff_input_refs(state_dir=service.state_dir, project_root=tmp_path,
        payload={'trigger_payload': trigger.payload}, source_event_id=trigger.id)
    context = hydrate_sidecar_ref(service.state_dir, next(r for r in refs if r['source_id']=='plan-rework-context')).payload
    assert context['dispatch_scope_source_event_id'] == event.id
    assert context['task_ids'] == ['OLD-GAP', 'NEW-CORE', 'INTEGRATE']
    items = [{'task_id': t} for t in ['OLD-GAP', 'NEW-CORE', 'INTEGRATE', 'RUNNING', 'DONE']]
    selected, ids, _, _ = select_writer_task_items({'tasks': items}, items, trigger.payload)
    assert ids == context['task_ids']
    assert [t['task_id'] for t in selected] == ids


def test_owner_cannot_select_another_workflows_task(tmp_path):
    from zf.core.task.schema import TaskContract

    service, log = _service(tmp_path)
    _pending(service, log)
    TaskStore(service.state_dir/'kanban.json').add(Task(id='FOREIGN', title='foreign',
        contract=TaskContract(feature_id='other-workflow')))
    result = _exec(service, log, 'plan-reject',
        {'plan_id': 'plan', 'reason': 'refine', 'dispatch_task_ids': ['FOREIGN']})
    assert not result['ok'] and 'another workflow' in result['reason']


def test_backlog_with_live_operation_cannot_be_rebound(tmp_path):
    from zf.core.events.writer import EventWriter
    from zf.runtime.workflow_operation import WorkflowOperationService

    service, log = _service(tmp_path)
    _pending(service, log)
    TaskStore(service.state_dir / 'kanban.json').add(Task(id='LIVE', title='live'))
    operations = WorkflowOperationService(state_dir=service.state_dir, event_log=log,
                                          event_writer=EventWriter(log))
    operation = operations.ensure_operation(workflow_run_id='run', operation_id='op-live',
        operation_type='task-stage', request={'task_id': 'LIVE'}, task_id='LIVE')
    operations.mark_started(operation_id='op-live', request_hash=operation.request_hash,
                            workflow_run_id='run', task_id='LIVE')
    result = _exec(service, log, 'plan-reject',
        {'plan_id': 'plan', 'reason': 'refine', 'dispatch_task_ids': ['LIVE']})
    assert not result['ok'] and 'active or non-backlog' in result['reason']
    assert not any(e.type == 'plan.rejected' for e in log.read_all())


def test_worker_emit_cannot_claim_owner_dispatch_scope(tmp_path, monkeypatch):
    import json
    from zf.cli.main import main

    service, log = _service(tmp_path)
    pending = _pending(service, log)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('ZF_STATE_DIR', str(service.state_dir))
    assert main(['emit', 'plan.rejected', '--actor', 'operator', '--payload', json.dumps({
        'plan_id': 'plan', 'workflow_run_id': 'run', 'request_event_id': pending.id,
        'reason': 'claimed scope', 'dispatch_task_ids': ['NEW']})]) == 2
    assert not any(e.type == 'plan.rejected' for e in log.read_all())
    from zf.runtime.plan_approval_replay import pending_plan_requests
    assert [e.id for e in pending_plan_requests(log.read_all())] == [pending.id]
