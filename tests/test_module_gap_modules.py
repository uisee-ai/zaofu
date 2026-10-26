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
