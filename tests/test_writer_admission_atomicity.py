"""A rejected final Task cannot leave earlier canonical Tasks reopened."""
from dataclasses import asdict

from tests.test_writer_fanout_runtime import _state, _start
from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.task_contract_authority import TaskContractAuthorityService


def test_partial_terminal_replan_rejection_does_not_change_other_tasks(tmp_path):
    state, log, transport, runtime = _state(tmp_path, synthesize_canonical=True)
    _start(runtime)
    store = TaskStore(state / 'kanban.json')
    # Only the first Task is eligible for contract refresh. The second has
    # already completed the incoming Task Map, as in a partially retained Run.
    first = store.get('TASK-1')
    contract = asdict(first.contract)
    contract['evidence_contract']['source_refs']['task_map_ref'] = 'old-map.json'
    TaskContractAuthorityService(task_store=store, event_writer=runtime.event_writer,
        state_dir=state).replace(first, contract=contract, source='test_prior_plan')
    for task_id in ['TASK-1', 'TASK-2']:
        store.update(task_id, status='done', completed_at='2026-01-01T00:00:00+00:00')
    before = {task_id: asdict(store.get(task_id)) for task_id in ['TASK-1', 'TASK-2']}
    sent = len(transport.sent)
    trigger = runtime.event_writer.append(ZfEvent(type='task_map.ready', actor='zf-cli',
        correlation_id='trace-1', payload={'pdd_id': 'F-11111111',
            'rework_of': 'prior-plan', 'rework_attempt': 1}))

    runtime.run_once(events=[trigger])

    assert {task_id: asdict(store.get(task_id)) for task_id in before} == before
    assert len(transport.sent) == sent
    assert any(e.type == 'fanout.cancelled' and e.payload.get('reason') == 'terminal_tasks'
               and e.payload.get('trigger_event_id') == trigger.id for e in log.read_all())


def test_role_capacity_rejection_precedes_canonical_materialization(tmp_path):
    state, log, transport, runtime = _state(tmp_path, synthesize_canonical=True)
    writer = next(s for s in runtime.config.workflow.stages if s.topology == 'fanout_writer_scoped')
    writer.roles = writer.roles[:1]
    store = TaskStore(state / 'kanban.json')
    before = store.list_all_with_archive()

    _start(runtime)

    assert store.list_all_with_archive() == before
    assert transport.sent == []
    assert any(e.type == 'fanout.cancelled' and 'more tasks than writer role instances'
               in e.payload.get('reason', '') for e in log.read_all())
