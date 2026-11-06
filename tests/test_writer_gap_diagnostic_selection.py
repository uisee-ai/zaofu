"""Old recovery diagnosis must not suppress an approved gap prerequisite."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.writer_fanout_admission import load_writer_task_map


@pytest.fixture
def case(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-qm', 'base'], cwd=tmp_path, check=True)
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=tmp_path, text=True).strip()
    def task(tid, **extra):
        return {'task_id': tid, 'title': tid, 'allowed_paths': [f'src/{tid}/**'], 'allowed_paths_reason': 'bounded scope', 'verification': 'git diff --check', 'base_commit': head, 'source_refs': [f'git:{head}'], **extra}
    data = {'schema_version': 'task-map.v1', 'tasks': [
        task('PRE'), task('NEXT', blocked_by=['PRE'], payload={'supersedes_task_ids': ['OLD']}),
        task('DOWNSTREAM', blocked_by=['NEXT']), task('DONE'),
    ], 'amend': {'schema_version': 'task-map-amend.v1', 'resume_scope': 'gap_tasks_only', 'gap_task_ids': ['PRE', 'NEXT'], 'superseded_task_ids': ['OLD']}}
    payload = {'task_ids': ['OLD', 'DOWNSTREAM'], 'failed_task_ids': ['OLD'], 'downstream_task_ids': ['DOWNSTREAM'], 'resume_scope': 'failed_children_and_downstream', 'rework_of': 'failure'}
    return tmp_path, data, payload


def load(case):
    root, data, payload = case
    path = root / 'task_map.json'
    path.write_text(json.dumps(data))
    return load_writer_task_map(stage=SimpleNamespace(task_map=''), event=ZfEvent(type='task_map.ready', payload={**payload, 'task_map_ref': str(path)}), pdd_id='PRD', state_dir=root/'runtime', project_root=root)


def test_approved_gap_replaces_old_diagnostic_selector_and_includes_prerequisite(case):
    result = load(case)
    assert result.requested_task_ids == ['PRE', 'NEXT']
    assert [x['task_id'] for x in result.task_items] == ['PRE', 'NEXT']


def test_explicit_new_task_subset_is_preserved(case):
    case[2]['task_ids'] = ['NEXT']
    assert load(case).requested_task_ids == ['NEXT']


@pytest.mark.parametrize('fault', ['unrelated_requested', 'wrong_failed_set', 'no_diagnosis'])
def test_unproven_old_selection_is_not_silently_overridden(case, fault):
    payload = case[2]
    if fault == 'unrelated_requested':
        payload['task_ids'] = ['DONE']
    elif fault == 'wrong_failed_set':
        payload['failed_task_ids'] = ['ANOTHER']
    else:
        payload.pop('failed_task_ids')
        payload.pop('downstream_task_ids')
    assert load(case).requested_task_ids != ['PRE', 'NEXT']


def test_missing_declared_gap_task_is_rejected(case):
    case[1]['amend']['gap_task_ids'].append('ABSENT')
    with pytest.raises(RuntimeError, match='missing requested'):
        load(case)
