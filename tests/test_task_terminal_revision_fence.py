"""An older integration must not silently finish a changed Task contract."""
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from tests.test_candidate_incremental import _setup, _integrate
from zf.core.task.store import TaskStore
from zf.runtime.task_contract_authority import TaskContractAuthorityService
from zf.runtime.task_pipeline_terminal import reconcile_task_pipeline_terminals


def test_integration_rejects_contract_changed_before_authority_capture(tmp_path, monkeypatch):
    from zf.runtime import candidate_incremental
    state, config, log, writer, base, _ = _setup(tmp_path)
    store = TaskStore(state / 'kanban.json')
    original = candidate_incremental._integrate_locked
    def race(*args, **kwargs):
        task = store.get('TASK-A')
        contract = asdict(task.contract)
        contract['contract_revision'] = 'replacement-contract'
        contract['behavior'] = 'Additional acceptance obligation'
        TaskContractAuthorityService(task_store=store, event_writer=writer, state_dir=state).replace(
            task, contract=contract, source='test_authorized_revision')
        return original(*args, **kwargs)
    monkeypatch.setattr(candidate_incremental, '_integrate_locked', race)
    result = _integrate(tmp_path, state, config, log, writer, base)
    assert result.status != 'integrated'
    assert not any(e.type == 'integration.queue.integrated' for e in log.read_all())


@pytest.mark.parametrize('change', ['before-reconcile', 'during-hydrate', 'during-cas', 'none'])
def test_terminal_requires_unchanged_task_authority(tmp_path, monkeypatch, change):
    state, config, log, writer, base, _ = _setup(tmp_path)
    assert _integrate(tmp_path, state, config, log, writer, base).status == 'integrated'
    store = TaskStore(state / 'kanban.json')
    runtime = SimpleNamespace(state_dir=state, config=config, event_log=log,
        event_writer=writer, task_store=store, project_root=tmp_path)
    context = {'workflow_run_id': 'run-1', 'task_map_generation': 'map-g1'}

    def revise():
        task = store.get('TASK-A')
        contract = asdict(task.contract)
        contract['behavior'] = 'New acceptance obligation requiring fresh verification'
        TaskContractAuthorityService(task_store=store, event_writer=writer, state_dir=state).replace(
            task, contract=contract, source='test_authorized_revision')

    if change == 'before-reconcile':
        revise()
    elif change == 'during-hydrate':
        from zf.runtime import candidate_incremental
        hydrate = candidate_incremental.hydrate_task_integration_receipt
        def hydrate_then_revise(*args, **kwargs):
            result = hydrate(*args, **kwargs)
            revise()
            return result
        monkeypatch.setattr(candidate_incremental, 'hydrate_task_integration_receipt', hydrate_then_revise)
    elif change == 'during-cas':
        original = store.compare_and_update_fields
        def race(*args, **kwargs):
            revise()
            return original(*args, **kwargs)
        monkeypatch.setattr(store, 'compare_and_update_fields', race)

    reconcile_task_pipeline_terminals(runtime, generation_contexts={'TASK-A': context})

    task = store.get('TASK-A')
    done = [e for e in log.read_all() if e.type == 'task.done']
    if change == 'none':
        assert task.status == 'done'
        assert len(done) == 1
    else:
        assert task.status != 'done'
        assert task.completed_at is None
        assert done == []


def test_terminal_cas_does_not_clear_a_concurrent_dispatch(tmp_path, monkeypatch):
    state, config, log, writer, base, _ = _setup(tmp_path)
    store = TaskStore(state / 'kanban.json')
    store.update('TASK-A', status='in_progress', active_dispatch_id='old-verify')
    assert _integrate(tmp_path, state, config, log, writer, base).status == 'integrated'
    runtime = SimpleNamespace(state_dir=state, config=config, event_log=log,
        event_writer=writer, task_store=store, project_root=tmp_path)
    original = store.compare_and_update_fields
    def race(*args, **kwargs):
        store.update('TASK-A', active_dispatch_id='new-verify')
        return original(*args, **kwargs)
    monkeypatch.setattr(store, 'compare_and_update_fields', race)
    reconcile_task_pipeline_terminals(runtime, generation_contexts={
        'TASK-A': {'workflow_run_id': 'run-1', 'task_map_generation': 'map-g1'}})
    assert store.get('TASK-A').status == 'in_progress'
    assert store.get('TASK-A').active_dispatch_id == 'new-verify'
    assert not any(e.type == 'task.done' for e in log.read_all())
