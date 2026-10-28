from copy import deepcopy

import pytest

from zf.runtime.module_gap_plan import build_gap_task_map_amend
from zf.runtime.task_map import validate_task_map_payload


@pytest.mark.parametrize('successors', [1, 2])
def test_replacement_rebinds_all_declared_modules_without_mutating_source(successors):
    base = {'schema_version':'task-map.v1', 'feature_id':'P1', 'tasks':[
        {'task_id':task, 'title':task, 'owner_role':'dev', 'wave':0,
         'allowed_paths':[f'src/{task}/**'], 'allowed_paths_reason':'owned source',
         'acceptance':['behavior works']} for task in ['KEEP','OLD']],
        'modules':[
            {'module_id':'root', 'title':'Product', 'task_ids':['KEEP','OLD'],
             'design_refs':['docs/design.md']},
            {'module_id':'child', 'title':'Feature', 'parent_module_id':'root',
             'task_ids':['OLD']},
        ]}
    assert validate_task_map_payload(base).passed
    snapshot = deepcopy(base)
    gaps = [{'task_id':f'NEW{i}', 'parent_task_id':'OLD', 'supersedes_task_ids':['OLD'],
        'claim_paths':[f'src/new{i}/**',f'tests/test_new{i}.py'],
        'acceptance':['replacement works'], 'verify_commands':[f'pytest tests/test_new{i}.py'],
        'source_refs':['docs/design.md'], 'base_commit':'a'*40} for i in range(successors)]
    amended = build_gap_task_map_amend(base, gap_tasks=gaps, supersedes_task_map_ref='artifacts/old.json')
    validation = validate_task_map_payload(amended)
    assert validation.passed, validation.errors
    new_ids = [f'NEW{i}' for i in range(successors)]
    assert amended['modules'][0]['task_ids'] == ['KEEP',*new_ids]
    assert amended['modules'][1]['task_ids'] == new_ids
    assert amended['modules'][0]['design_refs'] == ['docs/design.md']
    assert amended['modules'][1]['parent_module_id'] == 'root'
    assert base == snapshot
    replay = build_gap_task_map_amend(amended, gap_tasks=gaps, supersedes_task_map_ref='artifacts/new.json')
    assert replay['modules'] == amended['modules']
    assert validate_task_map_payload(replay).passed


def test_multi_replacement_does_not_inherit_stale_edge_between_successors():
    base = {'schema_version': 'task-map.v1', 'feature_id': 'P1', 'tasks': [
        {'task_id': 'A', 'title': 'A', 'owner_role': 'dev', 'wave': 0,
         'allowed_paths': ['src/a/**'], 'allowed_paths_reason': 'owned source',
         'acceptance': ['A works']},
        {'task_id': 'F', 'title': 'F', 'owner_role': 'dev', 'wave': 1,
         'blocked_by': ['A'], 'allowed_paths': ['src/f/**'],
         'allowed_paths_reason': 'owned source', 'acceptance': ['F works']},
        {'task_id': 'ASSEMBLY', 'title': 'Assembly', 'owner_role': 'dev', 'wave': 2,
         'blocked_by': ['A', 'F'], 'allowed_paths': ['src/assembly/**'],
         'allowed_paths_reason': 'owned source', 'acceptance': ['Assembly works']},
    ]}
    assert validate_task_map_payload(base).passed
    gaps = [
        {'task_id': 'F-R2', 'supersedes_task_ids': ['F'], 'base_commit': 'a' * 40,
             'claim_paths': ['src/f/**', 'tests/f.py'], 'acceptance': ['F works again'],
         'verify_commands': ['pytest tests/f.py'], 'source_refs': ['docs/f.md']},
        {'task_id': 'ASSEMBLY-R2', 'supersedes_task_ids': ['ASSEMBLY'],
         'base_commit': 'a' * 40,
             'blocked_by': ['A', 'F-R2'],
         'claim_paths': ['src/assembly/**', 'tests/assembly.py'],
         'acceptance': ['Assembly works again'],
         'verify_commands': ['pytest tests/assembly.py'], 'source_refs': ['docs/a.md']},
    ]
    amended = build_gap_task_map_amend(
        base, gap_tasks=gaps, supersedes_task_map_ref='artifacts/old.json'
    )
    by_id = {task['task_id']: task for task in amended['tasks']}
    assert by_id['F-R2']['blocked_by'] == ['A']
    assert by_id['ASSEMBLY-R2']['blocked_by'] == ['A', 'F-R2']
    assert validate_task_map_payload(amended).passed
