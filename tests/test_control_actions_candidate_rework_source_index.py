"""A reviewed producer must reach the actual writer without a second rewrite."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from zf.core.task.store import TaskStore
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.writer_fanout_admission import load_writer_task_map
from tests.test_module_gap_plan_runtime import _state


def _setup(tmp_path: Path, *, gap: bool = True):
    state, log, _, orch = _state(tmp_path)
    base_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=tmp_path, text=True).strip()
    baseline = {'schema_version': 'task-map.v1', 'tasks': [{
        'task_id': 'OLD', 'title': 'Original task', 'owner_role': 'dev',
        'allowed_paths': ['src/**'], 'allowed_paths_reason': 'owned fixture',
        'acceptance': ['preserve the requirement'], 'verify_commands': ['true'],
        'source_refs': ['docs/requirement.md'], 'wave': 0,
    }]}
    (state / 'base-map.json').write_text(json.dumps(baseline))
    approved = {'schema_version': 'source-index.v1', 'tasks': [{
        'task_id': 'NEW' if gap else 'OLD',
        'source_refs': ['docs/requirement.md', 'docs/requirement.md'],
    }], 'post_core_engineering_mapping': [{'parent_task_id': 'OLD'}]}
    legacy = deepcopy(approved)
    legacy['tasks'][0]['task_id'] = 'OLD'
    (state / 'legacy-source-index.json').write_text(json.dumps(legacy))
    svc = ControlledActionService(state, orch.event_writer, config=orch.config, project_root=tmp_path)
    source = svc.writer.emit('flow.discovery.failed', actor='test', payload={})
    request = svc.writer.emit('control.action.requested', actor='operator', payload={})
    payload = {
        'pdd_id': 'P1', 'source_event_id': source.id, 'candidate_rework_action': 'retrigger',
        'checkpoint_id': 'approved-index', 'task_map_ref': 'base-map.json',
        'source_index_ref': 'legacy-source-index.json',
        'source_commit': base_commit, 'candidate_base_commit': base_commit,
    }
    if gap:
        payload['rework_summary'] = {'supersedes_task_ids': ['OLD'], 'gap_tasks': [{
            'task_id': 'NEW', 'title': 'Successor', 'parent_task_id': 'OLD',
            'supersedes_task_ids': ['OLD'], 'claim_paths': ['src/**'],
            'acceptance': ['preserve the requirement'], 'verify_commands': ['true'],
            'base_commit': base_commit, 'source_refs': ['docs/requirement.md', 'git:' + base_commit],
        }]}
    return state, log, orch, svc, request, payload, approved


def _apply(svc, request, payload):
    return svc.execute(action='candidate-rework-apply', requested_action='candidate-rework-apply',
                       requested=request, payload=payload)


def _descriptor(state, body):
    return {'logical_name': 'source_index', **write_immutable_json_sidecar(
        state, body, root='fixtures', kind='source_index',
        schema_version='source-index.v1', created_by='test',
    )}


@pytest.mark.parametrize('gap', [False, True])
@pytest.mark.parametrize('producer', ['body', 'descriptor'])
def test_explicit_source_index_reaches_actual_writer_contract_unchanged(tmp_path, gap, producer):
    state, log, orch, svc, request, payload, approved = _setup(tmp_path, gap=gap)
    port = ({'logical_name': 'source_index', 'body': approved} if producer == 'body'
            else _descriptor(state, approved))
    payload['plan_ports'] = [port]
    before = deepcopy(payload)
    result = _apply(svc, request, payload)
    assert result['ok'], result
    assert payload == before
    ready = next(event for event in log.read_all() if event.type == 'task_map.ready')
    pinned = next(port for port in ready.payload['plan_ports'] if port['logical_name'] == 'source_index')
    assert ready.payload['source_index_ref'] == pinned['ref']
    assert hydrate_sidecar_ref(state, pinned).payload == approved
    assert not list((state / 'artifacts/P1/gap-amends').glob('*/source_index.json'))
    if gap:
        amended = next(event for event in log.read_all() if event.type == 'task_map.amended')
        assert amended.payload['source_index_ref'] == pinned['ref']
    loaded = load_writer_task_map(
        stage=orch.config.workflow.stages[0], event=ready, pdd_id='P1',
        state_dir=state, project_root=tmp_path,
    )
    assert loaded.source_index_ref == pinned['ref']
    orch._ensure_writer_tasks_canonical(loaded)
    task = TaskStore(state / 'kanban.json').get('NEW' if gap else 'OLD')
    assert task is not None
    assert task.contract.source_index_ref == pinned['ref']
    assert task.contract.evidence_contract['source_refs']['source_index_ref'] == pinned['ref']
    actual = hydrate_sidecar_ref(state, {'ref': task.contract.source_index_ref, 'sha256': pinned['sha256']}).payload
    assert actual == approved
    assert actual['post_core_engineering_mapping'][0]['parent_task_id'] == 'OLD'
    assert actual['tasks'][0]['source_refs'] == ['docs/requirement.md'] * 2


@pytest.mark.parametrize('fault', ['missing_ref', 'missing_digest', 'missing_file', 'digest', 'duplicate', 'malformed'])
def test_invalid_explicit_index_rejects_without_falling_back_to_legacy(tmp_path, fault):
    state, log, _, svc, request, payload, approved = _setup(tmp_path)
    port = _descriptor(state, approved)
    payload['plan_ports'] = [port]
    if fault == 'missing_ref':
        port.pop('ref')
    elif fault == 'missing_digest':
        port.pop('sha256')
    elif fault == 'missing_file':
        (state / port['ref']).unlink()
    elif fault == 'digest':
        port['sha256'] = '0' * 64
    elif fault == 'duplicate':
        payload['plan_ports'].append(dict(port))
    else:
        payload['plan_ports'].append('malformed')
    result = _apply(svc, request, payload)
    assert not result['ok']
    assert 'invalid rework plan ports' in result['reason']
    assert not any(event.type in {'task_map.ready', 'task_map.amended'} for event in log.read_all())
    assert not list((state / 'artifacts/P1/gap-amends').glob('*/task_map.json'))


@pytest.mark.parametrize('legacy_ref', [False, True])
def test_absent_explicit_index_preserves_legacy_derivation(tmp_path, legacy_ref):
    state, log, _, svc, request, payload, _ = _setup(tmp_path)
    if not legacy_ref:
        payload.pop('source_index_ref')
    result = _apply(svc, request, payload)
    assert result['ok'], result
    ready = next(event for event in log.read_all() if event.type == 'task_map.ready')
    if legacy_ref:
        derived = json.loads((state / ready.payload['source_index_ref']).read_text())
        assert derived['tasks'][0]['task_id'] == 'NEW'
        assert derived['post_core_engineering_mapping'][0]['parent_task_id'] == 'NEW'
        assert derived['tasks'][0]['source_refs'] == ['docs/requirement.md']
    else:
        assert ready.payload['source_index_ref'] == ''
        assert not list((state / 'artifacts/P1/gap-amends').glob('*/source_index.json'))
