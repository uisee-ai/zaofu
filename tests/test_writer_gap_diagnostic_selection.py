"""Old recovery diagnosis must not suppress an approved gap prerequisite."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from zf.core.events.model import ZfEvent
from zf.core.workflow.lane_pipeline import parse_lane_pipeline
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


def load(case, *, pipeline_spec=None):
    root, data, payload = case
    path = root / 'task_map.json'
    path.write_text(json.dumps(data))
    return load_writer_task_map(stage=SimpleNamespace(task_map=''), event=ZfEvent(type='task_map.ready', payload={**payload, 'task_map_ref': str(path)}), pdd_id='PRD', state_dir=root/'runtime', project_root=root, pipeline_spec=pipeline_spec)


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


def test_gap_can_replace_a_failed_task_and_its_downstream(case):
    data = case[1]
    data['amend']['superseded_task_ids'].append('DOWNSTREAM')
    data['amend']['gap_task_ids'].append('DOWNSTREAM-NEXT')
    data['tasks'][2]['task_id'] = 'DOWNSTREAM-NEXT'
    data['tasks'][2]['payload'] = {'supersedes_task_ids': ['DOWNSTREAM']}
    data['workspace_root_owner_required'] = True
    data['tasks'][3]['allowed_paths'] = []  # completed scaffold is read-only
    spec = parse_lane_pipeline({
        'id': 'prd-lanes', 'kind': 'lane_pipeline', 'trigger': 'task_map.ready',
        'affinity_key': 'affinity_tag', 'lane_count': 8, 'assembly': 'none',
        'stages': [{'id': 'impl'}, {'id': 'verify'}],
        'final': {'when': 'all_tasks_verified', 'role': 'judge'},
    })

    result = load(case, pipeline_spec=spec)

    assert result.requested_task_ids == ['PRE', 'NEXT', 'DOWNSTREAM-NEXT']
    assert [item['task_id'] for item in result.task_items] == result.requested_task_ids


def test_gap_cannot_replace_a_task_outside_the_diagnostic_scope(case):
    case[1]['amend']['superseded_task_ids'].append('DONE')

    assert load(case).requested_task_ids != ['PRE', 'NEXT']


def test_gap_cannot_omit_a_failed_task_from_replacement(case):
    case[2]['failed_task_ids'].append('DOWNSTREAM')

    assert load(case).requested_task_ids != ['PRE', 'NEXT']


def test_broad_recovery_includes_approved_prerequisites_and_downstream(case):
    case[1]['amend']['resume_scope'] = 'failed_children_and_downstream'

    result = load(case)

    assert result.requested_task_ids == ['NEXT', 'DOWNSTREAM', 'PRE']
    assert [item['task_id'] for item in result.task_items] == ['PRE', 'NEXT', 'DOWNSTREAM']
    assert 'DONE' not in result.requested_task_ids


def test_broad_recovery_can_replace_a_diagnosed_downstream(case):
    data = case[1]
    data['amend']['resume_scope'] = 'failed_children_and_downstream'
    data['amend']['superseded_task_ids'].append('DOWNSTREAM')
    data['amend']['gap_task_ids'].append('DOWNSTREAM-NEXT')
    data['tasks'][2]['task_id'] = 'DOWNSTREAM-NEXT'
    data['tasks'][2]['payload'] = {'supersedes_task_ids': ['DOWNSTREAM']}

    assert load(case).requested_task_ids == ['NEXT', 'DOWNSTREAM-NEXT', 'PRE']


@pytest.mark.parametrize('selection', [['NEXT'], ['DONE'], ['OLD'], ['OLD', 'DOWNSTREAM', 'DONE']])
def test_broad_recovery_does_not_expand_explicit_or_unrelated_selection(case, selection):
    case[1]['amend']['resume_scope'] = 'failed_children_and_downstream'
    case[2]['task_ids'] = selection

    result = load(case)

    assert 'PRE' not in result.requested_task_ids
    assert set(result.requested_task_ids) == {('NEXT' if tid == 'OLD' else tid) for tid in selection}


@pytest.mark.parametrize('scope', ['gap_tasks_only', 'failed_children_only', '', 'all'])
def test_broad_amendment_does_not_override_a_different_event_scope(case, scope):
    case[1]['amend']['resume_scope'] = 'failed_children_and_downstream'
    case[2]['resume_scope'] = scope

    if scope in {'gap_tasks_only', 'failed_children_only'}:
        assert 'PRE' not in load(case).requested_task_ids
    else:
        with pytest.raises(RuntimeError, match='missing requested'):
            load(case)


@pytest.mark.parametrize('fault', ['no_diagnosis', 'omitted_failure', 'unrelated_supersede'])
def test_broad_recovery_requires_exact_diagnosis_and_bounded_supersession(case, fault):
    case[1]['amend']['resume_scope'] = 'failed_children_and_downstream'
    if fault == 'no_diagnosis':
        case[2].pop('failed_task_ids')
    elif fault == 'omitted_failure':
        case[2]['failed_task_ids'].append('DOWNSTREAM')
    else:
        case[1]['amend']['superseded_task_ids'].append('DONE')

    assert 'PRE' not in load(case).requested_task_ids


@pytest.mark.parametrize('gap_ids', [[], ['PRE', 'PRE'], ['ABSENT']])
def test_broad_recovery_rejects_invalid_approved_gap_ids(case, gap_ids):
    case[1]['amend']['resume_scope'] = 'failed_children_and_downstream'
    case[1]['amend']['gap_task_ids'] = gap_ids

    with pytest.raises(RuntimeError, match='gap_task_ids|missing requested'):
        load(case)


def test_broad_recovery_keeps_full_pipeline_root_owner_gate(case):
    data = case[1]
    data['amend']['resume_scope'] = 'failed_children_and_downstream'
    data['workspace_root_owner_required'] = True
    data['tasks'][3]['allowed_paths'] = []
    spec = parse_lane_pipeline({
        'id': 'prd-lanes', 'kind': 'lane_pipeline', 'trigger': 'task_map.ready',
        'affinity_key': 'affinity_tag', 'lane_count': 8, 'assembly': 'none',
        'stages': [{'id': 'impl'}, {'id': 'verify'}],
        'final': {'when': 'all_tasks_verified', 'role': 'judge'},
    })

    with pytest.raises(ValueError, match='root|owner'):
        load(case, pipeline_spec=spec)
