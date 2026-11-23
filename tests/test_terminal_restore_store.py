"""Terminal compensation cannot publish backlog or partially append a batch."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from zf.core.task.authority import authority_revision_for
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


def case(tmp_path):
    store = TaskStore(tmp_path / 'kanban.json')
    old, new = [], []
    for task_id in ['T1', 'T2']:
        store.add(Task(id=task_id, title=task_id))
        store.update(task_id, status='done', completed_at='2026-01-01T00:00:00+00:00')
        task = store.get(task_id)
        old.append(task)
        restored = deepcopy(task)
        restored.contract.behavior = 'Restored reviewed contract'
        restored.contract_authority_sequence += 1
        restored.contract_authority_revision = authority_revision_for(task_id=task_id,
            contract=restored.contract, execution_binding=restored.execution_binding,
            sequence=restored.contract_authority_sequence)
        new.append(restored)
    return store, old, new


def test_terminal_restore_atomic_batch_and_idempotent_replay(tmp_path):
    store, old, new = case(tmp_path)
    index = store._index_path.read_bytes()
    assert store.compare_and_restore_terminal_batch(expected=old, replacements=new)
    archive = store._archive_file(store._today()).read_bytes()
    assert [asdict(store.get(t.id)) for t in old] == [asdict(t) for t in new]
    assert store.list_all() == []
    assert store._index_path.read_bytes() == index
    assert store.compare_and_restore_terminal_batch(expected=old, replacements=new)
    assert store._archive_file(store._today()).read_bytes() == archive


def test_terminal_restore_rejects_partial_or_stale_expected_batch(tmp_path):
    store, old, new = case(tmp_path)
    assert store.compare_and_restore_terminal_batch(expected=old[:1], replacements=new[:1])
    archive = store._archive_file(store._today()).read_bytes()
    assert not store.compare_and_restore_terminal_batch(expected=old, replacements=new)
    assert store._archive_file(store._today()).read_bytes() == archive
    new[1].status = 'backlog'
    with pytest.raises(ValueError, match='inactive done'):
        store.compare_and_restore_terminal_batch(expected=old, replacements=new)
