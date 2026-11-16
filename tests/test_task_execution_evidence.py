"""Native observations remain discoverable without becoming acceptance verdicts."""
from dataclasses import replace
import json

import pytest

from tests.test_task_pipeline_capsule import dispatch_fixture

from zf.core.events import ZfEvent
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_text


def observations(tmp_path):
    source = {'operation_id': 'old', 'workflow_run_id': 'r', 'task_id': 'T7',
              'active_attempt_id': 'a0', 'role_instance': 'dev-0'}
    dispatch = ZfEvent(type='task.pipeline.stage.dispatched', origin='kernel', task_id='T7',
        correlation_id='r', payload={**source, 'attempt_id': 'a0', 'workdir': str(tmp_path)})
    hook = ZfEvent(type='codex.hook.post_tool_use', actor='dev-0', correlation_id='r',
        causation_id=dispatch.id, payload={'cwd': str(tmp_path), 'tool_name': 'Bash',
        'tool_input': {'command': 'npm test'}, 'tool_response': '1 failed\nAPI_KEY=private-value'})
    settled = ZfEvent(type='workflow.operation.settled', origin='kernel', payload=source)
    source['last_event_id'] = settled.id
    return source, dispatch, hook, settled


def test_snapshot_preserves_inline_and_raw_output_with_exact_causation(tmp_path):
    from zf.runtime.task_execution_evidence import capture_execution_evidence
    source, dispatch, hook, settled = observations(tmp_path)
    raw = write_sidecar_text(tmp_path, 'artifacts/output.txt', '2 passed\n' * 400,
        kind='agent_session_output', schema_version='agent-output-raw.v1', created_by='test')
    green = replace(hook, id='green', payload={**hook.payload, 'tool_response': 'truncated', 'refs': {'raw_output': raw}})
    wrong = [replace(hook, id='other-role', actor='other'),
             replace(hook, id='other-run', correlation_id='other'),
             replace(hook, id='other-dispatch', causation_id='other'),
             replace(hook, id='other-workdir', payload={**hook.payload, 'cwd': '/outside'})]
    ref = capture_execution_evidence(tmp_path, source, [dispatch, hook, green, *wrong, settled, replace(hook, id='late')])
    index = hydrate_sidecar_ref(tmp_path, ref).payload
    assert [row['event_id'] for row in index['entries']] == [hook.id, 'green']
    red = hydrate_sidecar_ref(tmp_path, index['entries'][0]['ref']).payload
    assert '1 failed' in red['output'] and 'private-value' not in red['output']
    assert red['claims_status'] == 'provider_observation_unverified'
    assert red['authorizes_execution'] is False and 'exit_code' not in red
    output = hydrate_sidecar_ref(tmp_path, index['entries'][1]['ref']).payload
    assert output['output'] == '2 passed\n' * 400 and output['source_ref'] == raw


def test_missing_raw_evidence_is_explicit_not_a_successful_preview(tmp_path):
    from zf.runtime.task_execution_evidence import capture_execution_evidence
    source, dispatch, hook, settled = observations(tmp_path)
    raw = write_sidecar_text(tmp_path, 'artifacts/output.txt', 'real', kind='agent_session_output',
        schema_version='agent-output-raw.v1', created_by='test')
    (tmp_path / raw['ref']).write_text('tampered')
    hook = replace(hook, payload={**hook.payload, 'refs': {'raw_output': raw}})
    ref = capture_execution_evidence(tmp_path, source, [dispatch, hook, settled])
    entry = hydrate_sidecar_ref(tmp_path, ref).payload['entries'][0]
    body = hydrate_sidecar_ref(tmp_path, entry['ref']).payload
    assert body['status'] == 'unavailable' and body['output'] is None


def test_real_dispatch_attaches_reachable_observation_index(dispatch_fixture):
    import subprocess
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    runtime, _, _, prepared, dispatch_task = dispatch_fixture
    root = runtime.project_root
    for args in (['init', '-q'], ['config', 'user.email', 'test@example.invalid'],
                 ['config', 'user.name', 'Test'], ['commit', '--allow-empty', '-qm', 'test: seed']):
        subprocess.run(['git', *args], cwd=root, check=True, capture_output=True)
    source, dispatched, hook, settled = observations(root)
    source.update(workflow_run_id='run-1', task_id='TASK-1', task_pipeline_stage='impl')
    source['request_ref'] = write_immutable_json_sidecar(runtime.state_dir, {'request': {}},
        root='requests', kind='request', schema_version='request.v1', created_by='kernel')
    requested = ZfEvent(type='workflow.operation.requested', origin='kernel', task_id='TASK-1', payload=source)
    dispatched = replace(dispatched, task_id='TASK-1', correlation_id='run-1',
        payload={**source, 'attempt_id': 'a0', 'workdir': str(root)})
    hook = replace(hook, correlation_id='run-1')
    settled = replace(settled, task_id='TASK-1', payload=source)
    for event in (requested, dispatched, hook, settled):
        runtime.event_writer.append(event)
    dispatch_task()
    body = hydrate_sidecar_ref(runtime.state_dir, prepared[-1]['payload']['task_handoff_ref']).payload
    index = hydrate_sidecar_ref(runtime.state_dir, body['execution_evidence_ref']).payload
    assert [e['event_id'] for e in index['entries']] == [hook.id]
    assert 'handoff-evidence' in body['execution_evidence_read']


@pytest.fixture
def evidence_handoff(tmp_path, monkeypatch):
    from tests.test_work_record import setup
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    from zf.runtime.artifact_read_ledger import source_manifest_from_payload
    from zf.runtime.task_execution_evidence import capture_execution_evidence
    state = tmp_path / 'state'
    source, dispatched, hook, settled = observations(tmp_path)
    hook = replace(hook, payload={**hook.payload, 'tool_response': 'original RED\n' * 300})
    index = capture_execution_evidence(state, source, [dispatched, hook, settled])
    def inputs(state):
        old = {'schema_version': 'task-handoff.v1',
            'target': {'workflow_run_id': 'r', 'task_id': 'T7', 'operation_id': 'middle'},
            'source': {'operation_id': 'old'}, 'objective': 'finish', 'contract_revision': '',
            'remaining_acceptance': [], 'workspace': {}, 'admitted_results': [], 'work_record': None,
            'unknown_effects': [], 'next_action': 'check', 'references': [],
            'authorizes_execution': False, 'business_completed': False, 'execution_evidence_ref': index}
        oldref = write_immutable_json_sidecar(state, old, root='task-handoffs', kind='task_handoff',
            schema_version='task-handoff.v1', created_by='kernel')
        body = {**old, 'target': {**old['target'], 'operation_id': 'op'}, 'source': {'operation_id': 'middle'},
                'execution_evidence_ref': None, 'previous_handoff_ref': oldref}
        ref = write_immutable_json_sidecar(state, body, root='task-handoffs', kind='task_handoff',
            schema_version='task-handoff.v1', created_by='kernel')
        manifest, descriptor = source_manifest_from_payload(state_dir=state, project_root=tmp_path,
            payload={'input_refs': [{**ref, 'source_id': 'task-handoff', 'artifact_id': 'task-handoff', 'allowed_paths': ['$']}]},
            workflow_run_id='r', task_id='T7', attempt_id='a', dispatch_id='a')
        return manifest, [], descriptor
    monkeypatch.setattr('tests.test_work_record.published', inputs)
    _, _, log, _, token = setup(state)
    monkeypatch.setenv('ZF_ROLE_INSTANCE', 'dev-1')
    monkeypatch.setenv('ZF_RESULT_SUBMIT_TOKEN', token)
    return state, hook, log


def test_authenticated_cli_can_page_original_evidence_across_multiple_attempts(evidence_handoff, capsys, monkeypatch):
    from zf.cli.main import main
    state, hook, log = evidence_handoff
    command = ['artifact', 'handoff-evidence', '--operation', 'op', '--source-operation', 'old',
               '--event', hook.id, '--state-dir', str(state)]
    assert main(command) == 0
    page = json.loads(capsys.readouterr().out)
    content, digest = page['content'], page['sha256']
    while page['next_page_start'] is not None:
        assert main([*command, '--page-start', str(page['next_page_start']), '--digest', digest]) == 0
        page = json.loads(capsys.readouterr().out)
        content += page['content']
    assert json.loads(content)['output'] == 'original RED\n' * 300
    assert not page['authorizes_execution']
    assert main([*command, '--page-start', '1024']) == 1
    capsys.readouterr()
    assert main([*command, '--digest', 'wrong']) == 1
    capsys.readouterr()
    assert main([*command, '--source-operation', 'unrelated']) == 1
    capsys.readouterr()
    assert main([*command, '--event', 'unrelated']) == 1
    capsys.readouterr()
    monkeypatch.setenv('ZF_RESULT_SUBMIT_TOKEN', 'wrong')
    assert main(command) == 1
    assert not any(e.type in {'task.done', 'test.passed'} for e in log.read_all())


def test_previous_handoff_uses_canonical_required_digest_not_dropped_request_paths(tmp_path):
    from zf.runtime.task_execution_evidence import previous_handoff
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    target = {'workflow_run_id': 'r', 'task_id': 'T7', 'operation_id': 'middle'}
    body = {'schema_version': 'task-handoff.v1', 'target': target, 'source': {'operation_id': 'old'},
        'objective': '', 'contract_revision': '', 'remaining_acceptance': [], 'workspace': {},
        'admitted_results': [], 'work_record': None, 'unknown_effects': [], 'next_action': 'continue',
        'references': [], 'authorizes_execution': False, 'business_completed': False}
    ref = write_immutable_json_sidecar(tmp_path, body, root='task-handoffs', kind='task_handoff',
        schema_version='task-handoff.v1', created_by='kernel')
    request = write_immutable_json_sidecar(tmp_path, {'request': {'required_reads': [
        {'source_id': 'task-handoff', 'artifact_sha256': ref['sha256']}]}},
        root='requests', kind='request', schema_version='request.v1', created_by='kernel')
    event = ZfEvent(type='task.handoff.prepared', origin='kernel', task_id='T7', payload={'target': target, 'handoff_ref': ref})
    source = {**target, 'request_ref': request}
    assert previous_handoff(tmp_path, source, [event]) == ref
    with pytest.raises(ValueError):
        previous_handoff(tmp_path, source, [replace(event, origin='worker')])
