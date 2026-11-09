"""Declared identity and scope checks never stand in for semantic review."""
from copy import deepcopy
import json

import pytest

from zf.runtime.plan_local_review import scoped_port_paths
from zf.runtime.plan_scope_coverage import declared_coverage_complete


def inputs(count=100):
    tasks = [{'task_id': f'T{i}', 'blocked_by': [], 'source_refs': [f'req.md#T{i}'],
              'acceptance_criteria': [{'id': f'AC-{i}'}]} for i in range(count)]
    task_map = {'schema_version': 'task-map.v1', 'tasks': tasks}
    source_index = {'schema_version': 'source-index.v1',
        'tasks': [{'task_id': t['task_id'], 'source_refs': t['source_refs'],
                   'acceptance_ids': [t['acceptance_criteria'][0]['id']]} for t in tasks],
        'traceability': [{'source_id': f'REQ-{i}', 'source_ref': f'req.md#T{i}'} for i in range(count)]}
    ledger = {'schema_version': 'coverage-ledger.v1', 'entries': [
        {'source_id': f'REQ-{i}', 'disposition': 'covered', 'task_ids': [f'T{i}'],
         'acceptance_ids': [f'AC-{i}']} for i in range(count)]}
    inventory = {'schema_version': 'source-inventory.v1', 'pages': [], 'forms': []}
    port = {'schema_version': 'task-scoped-plan-input.v1', 'shared': {'global_rule': 'retain'},
            'tasks': [{'task_id': f'T{i}', 'body': {'original': f'req.md#T{i}', 'detail': 'x' * 1000}}
                      for i in range(count)]}
    return task_map, source_index, ledger, inventory, port


def test_100_tasks_keep_declared_context_for_changed_task_and_consumer():
    old_map, index, ledger, inventory, old_port = inputs()
    old_map['tasks'][13]['blocked_by'] = ['T12']
    new_map, new_port = deepcopy(old_map), deepcopy(old_port)
    new_map['tasks'][12]['title'] = 'changed'
    new_port['tasks'][12]['body']['detail'] = 'new'
    paths = scoped_port_paths(old_map=old_map, new_map=new_map, old_port=old_port, new_port=new_port,
                              affected_task_ids=['T12'])
    assert paths == (['$.shared', '$.tasks[12]', '$.tasks[13]'], ['$.shared', '$.tasks[12]', '$.tasks[13]'])
    assert declared_coverage_complete(new_map, index, ledger, inventory, affected={'T12', 'T13'})
    local = [old_port['shared'], *(old_port['tasks'][i] for i in (12, 13)),
             new_port['shared'], *(new_port['tasks'][i] for i in (12, 13))]
    assert len(json.dumps(local)) < len(json.dumps(new_port)) // 10


@pytest.mark.parametrize('problem', ['missing_row', 'duplicate_source', 'wrong_owner', 'missing_acceptance',
                                     'unknown_ref', 'pending_affected', 'truncated_index', 'missing_inventory',
                                     'visual_affected', 'visual_unmapped', 'unknown_visual_asset'])
def test_uncertain_scope_never_counts_as_complete(problem):
    task_map, index, ledger, inventory, _ = inputs(3)
    if problem == 'missing_row': ledger['entries'].pop(0)
    if problem == 'duplicate_source': index['traceability'].append(deepcopy(index['traceability'][0]))
    if problem == 'wrong_owner': ledger['entries'][0]['task_ids'] = ['T1']
    if problem == 'missing_acceptance': ledger['entries'][0]['acceptance_ids'] = []
    if problem == 'unknown_ref': index['traceability'][0]['source_ref'] = 'other.md#missing'
    if problem == 'pending_affected': ledger['entries'][0].update(disposition='pending_decision', reason='TBD')
    if problem == 'truncated_index': index['complete'] = False
    if problem == 'missing_inventory': inventory.pop('pages'); inventory.pop('forms')
    if problem == 'visual_affected': inventory['pages'].append({'task_ids': ['T0'], 'reference_images': ['screen.png']})
    if problem == 'visual_unmapped': inventory['pages'].append({'task_ids': [], 'reference_images': ['screen.png']})
    if problem == 'unknown_visual_asset': inventory['visual_assets'] = ['diagram.svg']
    assert not declared_coverage_complete(task_map, index, ledger, inventory, affected={'T0'})


def test_unrelated_pending_decision_is_preserved_without_expanding_affected_scope():
    task_map, index, ledger, inventory, _ = inputs(3)
    ledger['entries'][2].update(disposition='pending_decision', reason='awaiting decision')
    assert declared_coverage_complete(task_map, index, ledger, inventory, affected={'T0'})
