from dataclasses import replace

import pytest

from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.writer_fanout_admission import load_writer_task_map
from tests.test_writer_fanout_runtime import _state


def _setup(tmp_path):
    state, log, _, orch = _state(tmp_path, synthesize_canonical=True)
    loaded = load_writer_task_map(
        stage=orch.config.workflow.stages[0], pdd_id='F-11111111',
        state_dir=state, project_root=tmp_path,
        event=ZfEvent(type='task_map.ready', payload={'pdd_id':'F-11111111'}))
    loaded = replace(loaded, workflow_run_id='RUN-1', plan_artifact_package_id='pkg-old',
                     plan_artifact_package_ref='artifacts/old.json', plan_artifact_package_digest='old',
                     task_map_generation='generation-old')
    orch._ensure_writer_tasks_canonical(loaded)
    store = TaskStore(state/'kanban.json')
    store.update('TASK-2', status='done')
    return orch, store, loaded, log


@pytest.mark.parametrize('status', ['backlog', 'in_progress'])
def test_same_map_new_package_refreshes_selected_contract_without_reopening_done(tmp_path, status):
    orch, store, loaded, log = _setup(tmp_path)
    store.update('TASK-1', status=status)
    before = store.get('TASK-1')
    kept = store.get('TASK-2')
    successor = replace(loaded, is_replan=True, task_items=loaded.task_items[:1],
                        plan_artifact_package_id='pkg-new', plan_artifact_package_ref='artifacts/new.json',
                        plan_artifact_package_digest='new', task_map_generation='generation-new')
    orch._ensure_writer_tasks_canonical(successor)
    current = store.get('TASK-1')
    assert current.contract.evidence_contract['source_refs']['plan_artifact_package_id'] == 'pkg-new'
    assert current.contract.evidence_contract['source_refs']['task_map_generation'] == 'generation-new'
    assert current.contract_authority_revision != before.contract_authority_revision
    assert current.status == status
    assert store.get('TASK-2') == kept
    orch._ensure_writer_tasks_canonical(successor)
    assert store.get('TASK-1') == current
    applied = [e for e in log.read_all() if e.type == 'task.contract.revision.applied'
               and e.payload.get('previous_authority_revision') == before.contract_authority_revision]
    assert len(applied) == 1


@pytest.mark.parametrize('case', ['same_package', 'different_run', 'not_replan'])
def test_package_rebind_does_not_refresh_without_same_run_replan_identity_change(tmp_path, case):
    orch, store, loaded, _ = _setup(tmp_path)
    before = store.get('TASK-1')
    successor = replace(loaded, is_replan=True)
    if case == 'different_run':
        successor = replace(successor, workflow_run_id='RUN-2', plan_artifact_package_id='pkg-new')
    elif case == 'not_replan':
        successor = replace(successor, is_replan=False, plan_artifact_package_id='pkg-new')
    orch._ensure_writer_tasks_canonical(successor)
    assert store.get('TASK-1') == before


def _recovery_fixture(tmp_path):
    import hashlib
    from zf.runtime.control_actions import ControlledActionService
    from zf.runtime.plan_artifact_package import write_plan_artifact_package
    from tests.test_plan_artifact_package import _package

    orch, store, loaded, log = _setup(tmp_path)
    package = _package(orch.state_dir)
    package['workflow_run_id'] = 'RUN-1'
    package['task_map_generation'] = 'generation-new'
    for port in package['produced']:
        if port['logical_name'] == 'task_map':
            port.update(ref=str(loaded.task_map_path.relative_to(orch.state_dir)),
                        sha256=hashlib.sha256(loaded.task_map_path.read_bytes()).hexdigest())
    descriptor = write_plan_artifact_package(orch.state_dir, package)
    source = orch.event_writer.append(ZfEvent(type='task_map.ready', origin='kernel', payload={
        'task_map_ref':loaded.task_map_ref, 'workflow_run_id':'RUN-1', 'task_ids':['TASK-1']}))
    generation = orch.event_writer.append(ZfEvent(type='task.pipeline.generation.admitted', origin='kernel', payload={
        'generation_id':'pipeline-new', 'trigger_event_id':source.id, 'workflow_run_id':'RUN-1',
        'task_map_ref':loaded.task_map_ref, 'task_ids':['TASK-1'], 'task_map_generation':'generation-new',
        'plan_artifact_package_id':descriptor['package_id'],
        'plan_artifact_package_ref':descriptor['ref'], 'plan_artifact_package_digest':descriptor['sha256']}))
    svc = ControlledActionService(orch.state_dir, orch.event_writer, config=orch.config, project_root=tmp_path)
    return svc, store, source, generation


def _repair_bindings(svc, source, **extra):
    return svc.execute(action='stage-retrigger', requested_action='stage-retrigger',
        requested=ZfEvent(type='runtime.action.requested'),
        payload={'source_event_id':source.id, 'refresh_plan_bindings':True, 'approval_ref':'owner-review', **extra})


def test_controlled_binding_repair_preserves_task_contract_and_completed_work(tmp_path):
    from dataclasses import asdict
    svc, store, source, generation = _recovery_fixture(tmp_path)
    before = asdict(store.get('TASK-1').contract)
    kept = store.get('TASK-2')
    result = _repair_bindings(svc, source)
    assert result['ok'], result
    after = asdict(store.get('TASK-1').contract)
    for key in ('plan_artifact_package_id','plan_artifact_package_ref','plan_artifact_package_digest','task_map_generation'):
        before['evidence_contract']['source_refs'][key] = generation.payload[key]
    assert after == before
    assert store.get('TASK-2') == kept
    assert not _repair_bindings(svc, source)['ok']


@pytest.mark.parametrize('case', ['dispatched', 'superseded', 'approval'])
def test_controlled_binding_repair_rejects_live_or_unapproved_generation(tmp_path, case):
    svc, store, source, generation = _recovery_fixture(tmp_path)
    before = store.get('TASK-1')
    extra = {}
    if case == 'dispatched':
        svc.writer.append(ZfEvent(type='task.pipeline.stage.dispatched',origin='kernel',payload={
            'workflow_run_id':'RUN-1','task_map_generation':'generation-new'}))
    elif case == 'superseded':
        svc.writer.append(ZfEvent(type='task.pipeline.generation.admitted',origin='kernel',payload={
            **generation.payload,'generation_id':'later','trigger_event_id':'another-plan'}))
    else:
        extra['approval_ref'] = ''
    result = _repair_bindings(svc, source, **extra)
    assert not result['ok'], result
    assert store.get('TASK-1') == before
