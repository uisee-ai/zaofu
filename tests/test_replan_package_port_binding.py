"""Resumption must use admitted ports even when source events are ref-only."""
from types import SimpleNamespace

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.plan_artifact_package import write_plan_artifact_package
from zf.runtime.replan_resynth import build_replan_resynth_event
from zf.runtime.stage_failure_replan import plan_reader_stage_replan
from tests.test_plan_artifact_package import _fixture_port, _package
from tests.test_stage_failure_replan import _config


def _case(tmp_path, *, inherited=False):
    body = _package(tmp_path)
    port = _fixture_port(tmp_path, 'source_index', 'current')
    if inherited:
        prior = write_plan_artifact_package(tmp_path, _package(tmp_path, revision='old', generation='old'))
        body['inherited'].append({**port, 'source_package_ref': prior['ref'], 'source_package_digest': prior['sha256']})
    else:
        body['produced'].append(port)
    desc = write_plan_artifact_package(tmp_path, body)
    source = ZfEvent(type='fanout.synth.completed', payload={'workflow_run_id': 'run-1', 'control_result_ref': {'ref': 'artifacts/control.json'}})
    admitted = ZfEvent(type='plan.artifact_package.admitted', payload={
        'workflow_run_id': 'run-1', 'package_id': desc['package_id'],
        'package_ref': desc['ref'], 'package_digest': desc['sha256'],
        'task_map_generation': 'g1', 'source_event_id': source.id,
    })
    return body, source, admitted


def _retry(kind, state_dir, source, admitted):
    origin = ZfEvent(type='issue.requested' if kind == 'reader' else 'prd.scan.completed', payload={
        'workflow_run_id': 'run-1', 'trace_id': 'run-1', 'pdd_id': 'PRD',
        'task_map_ref': 'old.json', 'task_map_digest': 'old-sha',
        'source_index_ref': 'old-index.json', 'source_index_digest': 'old-sha',
    })
    events = [origin, source, admitted]
    if kind == 'reader':
        failure = ZfEvent(type='issue.triage.failed', payload={'workflow_run_id': 'run-1', 'trigger_event_id': origin.id})
        return plan_reader_stage_replan(_config(), [*events, failure], failure, state_dir=state_dir)[0]
    config = SimpleNamespace(workflow=SimpleNamespace(admission_replan=SimpleNamespace(enabled=True, resynth_trigger='prd.scan.completed'), stages=[]))
    plan = SimpleNamespace(workflow_run_id='run-1', trace_id='run-1', pdd_id='PRD', flow_kind='prd')
    return build_replan_resynth_event(plan=plan, events=events, config=config, state_dir=state_dir)


@pytest.mark.parametrize('kind', ['reader', 'candidate'])
@pytest.mark.parametrize('inherited', [False, True])
def test_replan_uses_current_package_ports_not_ref_only_source(tmp_path, kind, inherited):
    body, source, admitted = _case(tmp_path, inherited=inherited)
    event = _retry(kind, tmp_path, source, admitted)
    ports = {p['logical_name']: p for p in body['produced'] + body['inherited']}
    for name in ('task_map', 'source_index'):
        assert event.payload[name + '_ref'] == ports[name]['ref']
        assert event.payload[name + '_digest'] == ports[name]['sha256']
    assert event.payload['task_map_generation'] == 'g1'


@pytest.mark.parametrize('kind', ['reader', 'candidate'])
def test_corrupt_admitted_package_cannot_fall_back_to_old_map(tmp_path, kind):
    _, source, admitted = _case(tmp_path)
    (tmp_path / admitted.payload['package_ref']).write_text('{}')
    with pytest.raises(ValueError):
        _retry(kind, tmp_path, source, admitted)


def test_missing_optional_source_index_clears_inherited_reference(tmp_path):
    body, source, admitted = _case(tmp_path)
    body['produced'] = [p for p in body['produced'] if p['logical_name'] != 'source_index']
    desc = write_plan_artifact_package(tmp_path, body)
    admitted.payload.update(package_ref=desc['ref'], package_digest=desc['sha256'])
    event = _retry('candidate', tmp_path, source, admitted)
    assert 'source_index_ref' not in event.payload
    assert 'source_index_digest' not in event.payload


@pytest.mark.parametrize('key,value', [('workflow_run_id', 'another-run'), ('task_map_generation', 'another-generation')])
def test_package_body_must_match_admitted_run_and_generation(tmp_path, key, value):
    body, source, admitted = _case(tmp_path)
    body[key] = value
    desc = write_plan_artifact_package(tmp_path, body)
    admitted.payload.update(package_ref=desc['ref'], package_digest=desc['sha256'])
    with pytest.raises(ValueError, match='mismatch'):
        _retry('candidate', tmp_path, source, admitted)


def test_controlled_replan_entrypoint_binds_current_ports(tmp_path):
    from zf.core.events.log import EventLog
    from zf.runtime.control_actions_candidate_rework import _build_resynth_event

    body, source, admitted = _case(tmp_path)
    log = EventLog(tmp_path / 'events.jsonl')
    log.append(source)
    log.append(admitted)
    config = SimpleNamespace(workflow=SimpleNamespace(admission_replan=SimpleNamespace(enabled=True, resynth_trigger='prd.scan.completed'), stages=[]))
    event = _build_resynth_event({'workflow_run_id': 'run-1', 'trace_id': 'run-1', 'flow_kind': 'prd', 'pdd_id': 'PRD'}, state_dir=tmp_path, config=config)
    port = next(p for p in body['produced'] if p['logical_name'] == 'task_map')
    assert event is not None
    assert event.payload['task_map_ref'] == port['ref']
    assert event.payload['task_map_digest'] == port['sha256']


@pytest.mark.parametrize('fault', ['missing_package', 'missing_digest', 'tampered_port'])
def test_invalid_package_inputs_fail_closed(tmp_path, fault):
    body, source, admitted = _case(tmp_path)
    if fault == 'missing_package':
        (tmp_path / admitted.payload['package_ref']).unlink()
    elif fault == 'missing_digest':
        admitted.payload['package_digest'] = ''
    else:
        port = next(p for p in body['produced'] if p['logical_name'] == 'task_map')
        (tmp_path / port['ref']).write_text('{}')
    with pytest.raises(ValueError):
        _retry('candidate', tmp_path, source, admitted)
