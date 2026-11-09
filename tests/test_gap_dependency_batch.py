"""One-to-one gap batches preserve the original dependency graph."""
from copy import deepcopy

from zf.runtime.module_gap_plan import build_gap_task_map_amend
from zf.runtime.task_map import validate_task_map_payload


def test_one_to_one_replacements_rebind_edges_without_moving_prerequisites_upstream():
    def task(tid, deps):
        return {'task_id': tid, 'title': tid, 'owner_role': 'dev', 'wave': 1,
                'allowed_paths': [f'src/{tid}/'], 'allowed_paths_reason': 'one owner',
                'blocked_by': deps, 'acceptance': ['works']}
    original = {'schema_version': 'task-map.v1', 'tasks': [task('BASE', []), task('EXTRA', []),
                task('OLD-A', ['BASE']), task('OLD-B', ['OLD-A', 'EXTRA']), task('CONSUMER', ['OLD-B'])]}
    gaps = []
    for old, new in [('OLD-A', 'NEW-A'), ('OLD-B', 'NEW-B')]:
        gaps.append({'task_id': new, 'parent_task_id': old, 'supersedes_task_ids': [old],
                     'claim_paths': [f'src/{old}/'], 'acceptance': ['works'],
                     'verify_commands': ['pytest -q'], 'source_refs': ['event:gap']})
    before = deepcopy(gaps)
    amended = build_gap_task_map_amend(original, gap_tasks=gaps, supersedes_task_map_ref='base.json',
                                       successor_base_commit='a' * 40)
    rows = {t['task_id']: t for t in amended['tasks']}
    assert set(rows['NEW-A']['blocked_by']) == {'BASE'}
    assert set(rows['NEW-B']['blocked_by']) == {'NEW-A', 'EXTRA'}
    assert rows['CONSUMER']['blocked_by'] == ['NEW-B']
    assert rows['NEW-B']['wave'] > rows['NEW-A']['wave']
    assert validate_task_map_payload(amended, require_task_verification=False).passed
    assert gaps == before


def test_authored_successor_edges_do_not_pull_later_external_dependencies_to_first_slice():
    from zf.runtime.module_gap_replacement import inherit_superseded_incoming_dependencies

    proposed = [{'task_id': 'A2', 'supersedes_task_ids': ['A'], 'blocked_by': ['BASE']},
                {'task_id': 'B2', 'supersedes_task_ids': ['B'], 'blocked_by': ['A2', 'LATER']}]
    result = inherit_superseded_incoming_dependencies(proposed, base_tasks_by_id={
        'A': {'blocked_by': ['BASE']}, 'B': {'blocked_by': ['A', 'LATER']}})
    assert result == proposed
