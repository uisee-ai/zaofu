"""Resident diagnosis must see preauthorized host work before choosing a route."""
import json

import pytest

from tests.test_run_manager import _config, _state
from zf.core.events.model import ZfEvent
from zf.runtime.run_manager import _execute_resident_agent_reprompt
from zf.runtime.sidecar_refs import write_sidecar_json


def dispatch(state, writer, config):
    _execute_resident_agent_reprompt(state_dir=state, writer=writer, config=config,
        project_root=state.parent, action={'request_kind': 'recovery_diagnosis',
        'request_id': 'host-context', 'workflow_run_id': 'R1', 'task_id': 'T1'},
        causation_id='failed-browser')
    return json.loads((state/'diagnostics/run-manager/host-context/context.json').read_text())


def receipt(state, **updates):
    body = {'schema_version': 'task-host-receipt.v1', 'workflow_run_id': 'R1',
        'operation_id': 'O1', 'kind': 'verify', 'status': 'completed',
        'command': 'npm test', 'command_id': 'unit', 'exit_code': 0,
        'target_commit': 'a'*40, 'source_stable': True}
    body.update(updates)
    return write_sidecar_json(state, 'artifacts/host-context/receipt.json', body,
        kind='task_host_receipt', schema_version='task-host-receipt.v1', created_by='run-manager')


def effect(writer, ref, *, run='R1', origin='kernel'):
    writer.append(ZfEvent(type='runtime.action.attempt.completed', actor='run-manager', origin=origin,
        payload={'action':'task-host-assist', 'kind':'verify', 'workflow_run_id':run,
        'operation_id':'O1', 'receipt_ref':ref}))


def test_real_diagnosis_entry_exposes_partial_host_execution(tmp_path):
    state, log, writer = _state(tmp_path); config = _config()
    config.runtime.host_verification.enabled = True
    config.runtime.host_verification.approval_ref = 'owner:test'
    config.runtime.host_verification.commands = ['npm test', 'node tests/browser.cjs']
    config.runtime.host_verification.max_artifact_bytes = 64 * 1024 * 1024
    config.runtime.host_verification.max_total_evidence_bytes = 128 * 1024 * 1024
    ref = receipt(state); effect(writer, ref)
    writer.append(ZfEvent(type='runtime.action.attempt.started', actor='run-manager', origin='kernel',
        payload={'action':'task-host-assist', 'kind':'verify', 'workflow_run_id':'R1',
        'operation_id':'O1', 'manifest_digest':'browser-manifest'}))
    body = dispatch(state, writer, config)['preauthorized_host_recovery']
    assert body['host_verification']['enabled'] is True
    assert body['host_verification']['approval_ref'] == 'owner:test'
    assert body['host_verification']['commands'] == ['npm test', 'node tests/browser.cjs']
    assert body['host_verification']['max_artifact_bytes'] == 64 * 1024 * 1024
    assert body['host_verification']['max_total_evidence_bytes'] == 128 * 1024 * 1024
    assert len(body['effects']) == 2
    completed = body['effects'][0]
    assert completed['receipt_status'] == 'observed'
    assert completed['command'] == 'npm test'
    assert completed['receipt_ref'] == ref
    assert body['effects'][1]['event_type'] == 'runtime.action.attempt.started'
    assert 'not a product verdict' in body['limits']


@pytest.mark.parametrize('issue', ['foreign_run','untrusted','corrupt','wrong_operation'])
def test_diagnosis_does_not_promote_unbound_host_evidence(tmp_path, issue):
    state, log, writer = _state(tmp_path)
    ref = receipt(state, **({'operation_id':'other'} if issue=='wrong_operation' else {}))
    if issue=='corrupt': (state/ref['ref']).write_text('{}')
    effect(writer, ref, run='other' if issue=='foreign_run' else 'R1',
        origin='worker' if issue=='untrusted' else 'kernel')
    rows=dispatch(state,writer,_config())['preauthorized_host_recovery']['effects']
    if issue in {'foreign_run','untrusted'}:
        assert rows==[]
    else:
        assert rows[0]['receipt_status']=='unavailable'
        assert rows[0]['receipt_error']
        assert 'command' not in rows[0]


def test_absent_config_is_unknown_not_disabled(tmp_path):
    from zf.runtime.diagnosis_host_recovery import host_recovery_context
    body=host_recovery_context(tmp_path,[],None,'R1')
    assert body['host_verification']['enabled'] is None
    assert body['task_publication']['enabled'] is None
