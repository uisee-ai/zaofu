"""Declared source bodies survive the real Plan and recovery handoff."""
import hashlib
import json

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.artifact_read_ledger import source_manifest_from_payload
from zf.runtime.artifact_read_requirements import build_canonical_required_reads
from zf.runtime.plan_synth_handoff import build_plan_synth_call_payload
from zf.runtime.sidecar_refs import write_sidecar_text
from tests.test_control_actions_recovery import _req, _service


def _plan(tmp_path, trigger):
    state = tmp_path / 'state'
    state.mkdir(exist_ok=True)
    payload = build_plan_synth_call_payload(
        state_dir=state, project_root=tmp_path,
        manifest={'fanout_id': 'f-plan', 'trace_id': 'RUN', 'stage_id': 'plan',
                  'trigger_event_id': 'trigger', 'trigger_payload': trigger},
        reports=[], run_id='synth', role_instance='critic')
    manifest, _ = source_manifest_from_payload(
        state_dir=state, project_root=tmp_path, payload=payload,
        workflow_run_id='RUN', task_id='T1', attempt_id='attempt', dispatch_id='dispatch')
    return state, payload, manifest


def test_declared_files_reach_synth_required_reads_and_remain_frozen(tmp_path):
    first = tmp_path / 'scope.md'
    second = tmp_path / 'owner.md'
    first.write_text('Full scope, including exclusions.')
    second.write_text('Exact owner acceptance, not an inventory excerpt.')
    state, payload, manifest = _plan(tmp_path, {
        'source_ref': '', 'source_refs': {'task_spec_ref': 'scope.md'},
        'artifact_refs': ['scope.md', {'ref': 'owner.md', 'kind': 'requirement_authority'}],
    })
    bodies = {s['artifact_id']: s for s in payload['input_refs']
              if s['artifact_id'] in {'scope.md', 'owner.md'}}
    assert bodies.keys() == {'scope.md', 'owner.md'}
    for source in bodies.values():
        assert any(row['source_id'] == source['source_id'] and row['required']
                   for row in manifest['context_sections'])
    first.write_text('Later live change')
    assert (state / bodies['scope.md']['ref']).read_text() == 'Full scope, including exclusions.'
    assert sum(s['sha256'] == bodies['scope.md']['sha256'] for s in payload['input_refs']) == 1


def test_explicit_input_digest_mismatch_is_not_silently_rebound(tmp_path):
    (tmp_path / 'owner.md').write_text('Changed body')
    with pytest.raises(ValueError, match='digest mismatch'):
        _plan(tmp_path, {'artifact_refs': [{'ref': 'owner.md', 'sha256': 'a' * 64,
                                          'kind': 'requirement_authority'}]})


def test_frozen_requirement_declarations_survive_scan_without_promoting_outputs(tmp_path):
    (tmp_path / 'owner.md').write_text('Full original body')
    (tmp_path / 'inventory.json').write_text('{}')
    requirement = tmp_path / 'requirement.json'
    requirement.write_text(json.dumps({'schema_version': 'requirement-spec.v1',
        'source_refs': {'task_spec_ref': 'owner.md'},
        'artifact_refs': ['inventory.json']}))
    _, payload, _ = _plan(tmp_path, {'requirement_spec_ref': str(requirement),
                                   'artifact_refs': ['inventory.json']})
    assert any(s['artifact_id'] == 'owner.md' for s in payload['input_refs'])
    assert not any(s['artifact_id'] == 'inventory.json' for s in payload['input_refs'])


def test_missing_explicit_source_body_cannot_disappear_from_required_inputs(tmp_path):
    with pytest.raises(ValueError, match='source body is unavailable'):
        _plan(tmp_path, {'source_refs': {'task_spec_ref': 'missing.md'}})


def test_declared_input_preserves_access_scope_and_allowed_paths(tmp_path):
    (tmp_path / 'scoped.json').write_text(json.dumps({'public': 'visible', 'private': 'hidden'}))
    _, _, manifest = _plan(tmp_path, {'artifact_refs': [{
        'ref': 'scoped.json', 'kind': 'requirement_authority',
        'allowed_paths': ['$.public'], 'access_scope': {'roles': ['critic']},
    }]})
    source = next(s for s in manifest['sources'] if s['artifact_id'] == 'scoped.json')
    assert source['allowed_paths'] == ['$.public']
    assert source['access_scope'] == {'roles': ['critic']}
    reads = build_canonical_required_reads(manifest, output_profile_id='plan-synth')
    assert [r['json_path'] for r in reads if r['source_id'] == source['source_id']] == ['$.public']


@pytest.mark.parametrize('order', ['plain-first', 'restricted-first', 'task-spec', 'existing'])
def test_duplicate_source_cannot_discard_a_restriction(tmp_path, order):
    (tmp_path / 'scoped.json').write_text(json.dumps({'public': 'visible', 'private': 'hidden'}))
    plain = {'ref': 'scoped.json', 'kind': 'requirement_authority'}
    restricted = {**plain, 'allowed_paths': ['$.public'], 'access_scope': {'roles': ['critic']}}
    trigger = {'artifact_refs': [plain, restricted] if order == 'plain-first' else [restricted, plain]}
    if order == 'task-spec':
        trigger = {'source_refs': {'task_spec_ref': 'scoped.json'}, 'artifact_refs': [restricted]}
    if order == 'existing':
        trigger = {'requirement_spec_ref': 'scoped.json', 'artifact_refs': [restricted]}
    with pytest.raises(ValueError, match='conflicting read limits'):
        _plan(tmp_path, trigger)


def test_task_spec_declared_digest_is_enforced(tmp_path):
    (tmp_path / 'scope.md').write_text('Changed source')
    with pytest.raises(ValueError, match='digest mismatch'):
        _plan(tmp_path, {'source_refs': {'task_spec_ref': 'scope.md',
                                       'task_spec_digest': 'sha256:' + 'a' * 64}})


@pytest.mark.parametrize('limits', [{'allowed_paths': [' ']}, {'allowed_paths': []},
    {'access_scope': {'roles': 'critic'}}, {'access_scope': {'actors': {'operator': True}}},
    {'access_scope': {'purposes': [' ']}}, {'access_scope': {'role': 0}}])
def test_malformed_read_limits_do_not_become_unrestricted(tmp_path, limits):
    (tmp_path / 'scope.md').write_text('Restricted body')
    with pytest.raises(ValueError, match='read limits|scope restrictions'):
        _plan(tmp_path, {'artifact_refs': [{'ref': 'scope.md', 'kind': 'requirement_authority',
                                          **limits}]})


def _supplement(service, text='Owner source body'):
    digest = hashlib.sha256(text.encode()).hexdigest()
    return write_sidecar_text(service.state_dir,
        f'artifacts/recovery-inputs/{digest}.md', text,
        kind='recovery_source', schema_version='recovery-source.v1', created_by='operator')


def _recover(service, source, refs):
    body = {'source_event_id': source.id, 'supplemental_input_refs': refs}
    return service.execute(action='stage-retrigger', requested_action='stage-retrigger',
                           payload=body, requested=_req(body))


def test_controlled_retrigger_binds_supplement_without_mutating_original(tmp_path):
    service = _service(tmp_path)
    source = service.writer.append(ZfEvent(type='task_map.ready', actor='kernel',
        payload={'workflow_run_id': 'RUN', 'artifact_refs': ['prior-input']}))
    descriptor = _supplement(service)
    result = _recover(service, source, [descriptor])
    assert result['ok'], result
    events = service.writer.event_log.read_all()
    original = next(e for e in events if e.id == source.id)
    trigger = next(e for e in events if e.id == result['retriggered_event_id'])
    assert original.payload == source.payload
    refs = trigger.payload['artifact_refs']
    assert refs[0] == 'prior-input'
    assert refs[1]['sha256'] == descriptor['sha256']
    assert refs[1]['source_event_id'] == trigger.causation_id
    assert refs[1]['kind'] == 'plan_supplemental_input'
    payload = build_plan_synth_call_payload(
        state_dir=service.state_dir, project_root=tmp_path,
        manifest={'fanout_id': 'f-recovery', 'trace_id': 'RUN', 'stage_id': 'plan',
                  'trigger_event_id': trigger.id, 'trigger_payload': trigger.payload},
        reports=[], run_id='synth-recovery', role_instance='critic')
    manifest, _ = source_manifest_from_payload(
        state_dir=service.state_dir, project_root=tmp_path, payload=payload,
        workflow_run_id='RUN', task_id='T1', attempt_id='recovered', dispatch_id='recovered')
    source_body = next(s for s in manifest['sources'] if s['sha256'] == descriptor['sha256'])
    assert (service.state_dir / source_body['ref']).read_text() == 'Owner source body'
    assert any(s['source_id'] == source_body['source_id'] and s['required']
               for s in manifest['context_sections'])
    assert not _recover(service, source, [descriptor])['ok']


def test_native_rm_payload_retains_supplement_through_controlled_service(tmp_path):
    from zf.runtime.run_manager import _action_from_agent_recommendation, _operator_controlled_action_payload
    from zf.runtime.run_manager_recovery_actions import normalize_recovery_recommendation

    service = _service(tmp_path)
    source = service.writer.append(ZfEvent(type='task_map.ready', actor='kernel'))
    descriptor = _supplement(service)
    normalized = normalize_recovery_recommendation({'action': 'stage-retrigger',
        'recommended_route': 'controlled_action', 'checkpoint_id': 'checkpoint',
        'action_payload': {'source_event_id': source.id, 'supplemental_input_refs': [descriptor]}})
    action = _action_from_agent_recommendation(_req(normalized), normalized)
    payload = _operator_controlled_action_payload(action)
    result = service.execute(action='stage-retrigger', requested_action='stage-retrigger',
                             payload=payload, requested=_req(payload))
    assert result['ok']
    trigger = next(e for e in service.writer.event_log.read_all()
                   if e.id == result['retriggered_event_id'])
    assert trigger.payload['artifact_refs'][0]['sha256'] == descriptor['sha256']
    with pytest.raises(ValueError, match='identity conflict'):
        normalize_recovery_recommendation({**normalized, 'supplemental_input_refs': []})


@pytest.mark.parametrize('bad', ['text', [{}], [{'ref': '../outside', 'sha256': 'a' * 64}],
                               [{'ref': 'artifacts/missing.md', 'sha256': 'a' * 64}]])
def test_retrigger_refuses_invalid_supplement_without_redrive(tmp_path, bad):
    service = _service(tmp_path)
    source = service.writer.append(ZfEvent(type='task_map.ready', actor='kernel'))
    result = _recover(service, source, bad)
    assert not result['ok']
    assert not any(e.payload.get('redrive_of') == source.id
                   for e in service.writer.event_log.read_all())


def test_retrigger_refuses_changed_supplement(tmp_path):
    service = _service(tmp_path)
    source = service.writer.append(ZfEvent(type='task_map.ready', actor='kernel'))
    descriptor = _supplement(service)
    (service.state_dir / descriptor['ref']).write_text('tampered')
    assert not _recover(service, source, [descriptor])['ok']
