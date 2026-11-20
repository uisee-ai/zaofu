"""Historical execution facts must reach the actual downstream reader input."""
import json

import pytest

from tests.test_candidate_continuation import source, _action
from tests.test_run_admission import _Transport
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def successor(source):
    seeded, service, old_run, candidate = source
    preview = _action(service, old_run, candidate, dry_run=True)
    accepted = _action(service, old_run, candidate,
        reviewed_digest=preview['manifest']['manifest_digest'], approval_ref='owner:continue')
    runtime = Orchestrator(seeded.state_dir, seeded.config, _Transport(), project_root=seeded.project_root)
    invoke = next(e for e in seeded.event_log.read_all() if e.id == accepted['event_id'])
    assert runtime._on_workflow_invoke_requested(invoke).action == 'workflow_invoke'
    frozen = next(e for e in seeded.event_log.read_all() if e.type == 'candidate.ready'
                  and e.payload.get('workflow_run_id') == accepted['workflow_run_id'])
    return runtime, old_run, frozen


def test_discovery_dispatch_receives_authorized_historical_sources(source):
    source[0].config.workflow.flow_metadata = {'result_protocol': {'mode': 'blocking'}}
    runtime, old_run, frozen = successor(source)
    # Enter a real Reader consumer with the successor's frozen authority.
    runtime.config.workflow.stages[0].id = 'post-verify-discovery'
    assert runtime._maybe_start_reader_fanout(frozen)
    dispatch = next(e for e in runtime.event_log.read_all() if e.type == 'fanout.child.dispatched')
    payload = dispatch.payload['payload']
    manifest = hydrate_sidecar_ref(runtime.state_dir, payload['attempt_source_manifest']).payload
    sources = manifest['sources']
    context = next(s for s in sources if s['source_id'] == 'critical-continuation-context')
    body = hydrate_sidecar_ref(runtime.state_dir, context).payload
    assert body['source_run_id'] == old_run
    assert body['workflow_run_id'] == frozen.payload['workflow_run_id']
    assert body['qualification_policy'] == 'historical_facts_are_not_successor_execution_or_verdicts'
    assert body['task_proofs'][0]['task_id'] == 'TASK-1'
    assert any(s['source_id'].startswith('continuation-verify-control-') for s in sources)
    assert any(r['source_id'] == context['source_id'] for r in payload['required_reads'])
    assert not any(e.type == 'task.pipeline.stage.dispatched' for e in runtime.event_log.read_all())


def test_history_is_not_available_without_controlled_authorization(source):
    from zf.runtime.continuation_evidence import continuation_input_refs
    runtime, old_run, frozen = successor(source)
    events = [e for e in runtime.event_log.read_all() if not (
        e.type == 'runtime.action.attempt.completed' and e.payload.get('action') == 'candidate-continue')]
    with pytest.raises(ValueError, match='authorization'):
        continuation_input_refs(runtime.state_dir, events, frozen.payload['workflow_run_id'])


def test_changed_historical_result_never_becomes_fresh_evidence(source):
    from zf.runtime.continuation_evidence import continuation_input_refs
    runtime, old_run, frozen = successor(source)
    refs = continuation_input_refs(runtime.state_dir, runtime.event_log.read_all(), frozen.payload['workflow_run_id'])
    control = next(r for r in refs if r['source_id'].startswith('continuation-verify-control-'))
    (runtime.state_dir / control['ref']).write_text('{}')
    with pytest.raises(ValueError):
        continuation_input_refs(runtime.state_dir, runtime.event_log.read_all(), frozen.payload['workflow_run_id'])


def test_goal_consumer_gets_the_same_history_as_discovery(source):
    from zf.runtime.continuation_evidence import continuation_input_refs
    runtime, old_run, frozen = successor(source)
    events = runtime.event_log.read_all()
    run = frozen.payload['workflow_run_id']
    contract = runtime._goal_closure_read_contract(events, workflow_run_id=run, goal_id=run,
        identity={'candidate_head_commit': frozen.payload['candidate_head_commit']},
        claim_set={}, payload={})
    expected = {r['ref']: r['sha256'] for r in continuation_input_refs(runtime.state_dir, events, run)}
    actual = {r['ref']: r['sha256'] for r in contract['input_refs']}
    assert expected.items() <= actual.items()
    assert any(r['source_id'] == 'critical-continuation-context' for r in contract['required_reads'])


@pytest.mark.parametrize("fault", ["", "predecessor", "unadmitted_predecessor", "foreign_run", "foreign_actor", "wrong_cwd", "wrong_cause", "wrong_attempt", "late", "corrupt_raw"])
def test_impl_tool_facts_use_native_causation_and_complete_output(source, fault):
    from dataclasses import replace
    from zf.runtime.continuation_evidence import continuation_input_refs
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    from zf.runtime.sidecar_refs import write_sidecar_text
    runtime, old_run, frozen = successor(source)
    events = runtime.event_log.read_all()
    stop = next(i for i, e in enumerate(events) if e.type == 'run.cancelled')
    identity = {'workflow_run_id': old_run, 'task_id': 'TASK-1', 'operation_id': 'impl-history',
                'attempt_id': 'attempt-history', 'dispatch_id': 'dispatch-history', 'role_instance': 'dev-1'}
    dispatch = ZfEvent(type='task.pipeline.stage.dispatched', task_id='TASK-1', origin='kernel',
        correlation_id=old_run, payload={**identity, 'workdir': str(runtime.project_root),
            'task_map_generation': frozen.payload['task_map_generation'], 'task_pipeline_stage': 'impl'})
    output = 'tests fail 2\nthen tests pass 2\n' * 40
    raw = write_sidecar_text(runtime.state_dir, 'artifacts/history-output.txt', output,
        kind='agent_session_output', schema_version='agent-output-raw.v1', created_by='test')
    # Match hook-recv: PostToolUse does not set origin=kernel.
    fact = ZfEvent(type='codex.hook.post_tool_use', actor='dev-1', correlation_id=old_run,
        causation_id=dispatch.id, payload={'cwd': str(runtime.project_root),
        'tool_input': {'command': 'node --test'}, 'tool_response': '[truncated preview]',
        'refs': {'raw_output': raw}})
    if fault == 'foreign_run': fact = replace(fact, correlation_id='other-run')
    if fault == 'foreign_actor': fact = replace(fact, actor='other-role')
    if fault == 'wrong_cwd': fact = replace(fact, payload={**fact.payload, 'cwd': '/elsewhere'})
    if fault == 'wrong_cause': fact = replace(fact, causation_id='unrelated')
    if fault == 'wrong_attempt': dispatch = replace(dispatch, payload={**dispatch.payload, 'attempt_id': 'other'})
    if fault == 'corrupt_raw': (runtime.state_dir / raw['ref']).write_text('modified')
    control = write_immutable_json_sidecar(runtime.state_dir, {
        'schema_version': 'implementation-result.v1', **identity, 'target_commit': 'b' * 40,
        'self_check': {'evidence_refs': ['event:' + fact.id, 'event:missing']}},
        root='test', kind='control', schema_version='implementation-result.v1', created_by='test')
    admitted = ZfEvent(type='workflow.call.result.admitted', task_id='TASK-1', origin='kernel',
        payload={'workflow_run_id': old_run, 'control_result_schema': 'implementation-result.v1',
                 'semantic_verdict': 'passed', 'control_result_ref': control})
    stopped = ZfEvent(type='codex.hook.stop', actor='dev-1', correlation_id=old_run,
        causation_id=dispatch.id, payload={'cwd': str(runtime.project_root)})
    added = [dispatch, *([admitted, fact] if fault == 'late' else [fact, admitted]), stopped]
    if fault in {'predecessor', 'unadmitted_predecessor'}:
        old_identity = {**identity, 'operation_id': 'earlier-impl', 'attempt_id': 'earlier-attempt'}
        dispatch = replace(dispatch, payload={**dispatch.payload, **old_identity})
        old_control = write_immutable_json_sidecar(runtime.state_dir, {
            'schema_version': 'implementation-result.v1', **old_identity, 'target_commit': 'a' * 40},
            root='test', kind='control', schema_version='implementation-result.v1', created_by='test')
        old_admitted = ZfEvent(type='workflow.call.result.admitted', task_id='TASK-1', origin='kernel',
            payload={**admitted.payload, 'semantic_verdict': 'blocked', 'control_result_ref': old_control})
        added = [dispatch, fact, *([old_admitted] if fault == 'predecessor' else []), admitted, stopped]
    events[stop:stop] = added
    refs = continuation_input_refs(runtime.state_dir, events, frozen.payload['workflow_run_id'])
    tools = next(r for r in refs if r['source_id'] == 'continuation-tool-facts-TASK-1')
    body = hydrate_sidecar_ref(runtime.state_dir, tools).payload
    observed = body['referenced_tool_facts'][0]
    assert body['source_run_id'] == old_run
    assert body['referenced_tool_facts'][1]['status'] == 'unavailable'
    if fault and fault not in {'corrupt_raw', 'predecessor'}:
        assert observed['status'] == 'unavailable'
    else:
        assert observed['origin'] == ''
        observation = observed['observation']
        assert observation['claims_status'] == 'provider_observation_unverified'
        assert observation['authorizes_execution'] is False
        assert observation['output'] == (None if fault == 'corrupt_raw' else output)
        assert observation['status'] == ('unavailable' if fault == 'corrupt_raw' else 'available')
        if fault == 'predecessor':
            assert observation['binding']['operation_id'] == 'earlier-impl'
            assert observation['attempt_id'] == 'earlier-attempt'
            assert observed['source_target_commit'] == 'a' * 40
            assert observed['source_semantic_verdict'] == 'blocked'
            assert observed['scope'] == 'historical_task_observation_not_current_candidate_verdict'
    activity = hydrate_sidecar_ref(runtime.state_dir, next(r for r in refs
        if r['source_id'] == 'continuation-source-activity')).payload
    assert next(e for e in activity['observations'] if e['event_id'] == stopped.id)['origin'] == ''


def test_continuation_keeps_post_settlement_host_fact_and_blocked_source_verdict(source):
    from tests.test_task_host_evidence import host_probe
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    from zf.runtime.continuation_evidence import continuation_input_refs
    runtime, old_run, frozen = successor(source)
    identity = {'workflow_run_id': old_run, 'task_id': 'TASK-1', 'operation_id': 'prior-impl',
                'active_attempt_id': 'prior-attempt', 'role_instance': 'dev-1'}
    start, completed, _ = host_probe(runtime.state_dir, identity,
        task_map_generation=frozen.payload['task_map_generation'])
    dispatch = ZfEvent(type='task.pipeline.stage.dispatched', origin='kernel', task_id='TASK-1',
        payload={**identity, 'attempt_id': identity['active_attempt_id'], 'workdir': str(runtime.project_root)})
    def admission(operation, attempt, commit, verdict, evidence):
        ref = write_immutable_json_sidecar(runtime.state_dir, {
            'schema_version': 'implementation-result.v1', **identity, 'operation_id': operation,
            'attempt_id': attempt, 'target_commit': commit, 'self_check': {'evidence_refs': evidence}},
            root='test', kind='control', schema_version='implementation-result.v1', created_by='test')
        return ZfEvent(type='workflow.call.result.admitted', origin='kernel', task_id='TASK-1',
            payload={'workflow_run_id': old_run, 'control_result_schema': 'implementation-result.v1',
                     'semantic_verdict': verdict, 'control_result_ref': ref})
    earlier = admission('prior-impl', 'prior-attempt', 'a' * 40, 'blocked', [])
    settled = ZfEvent(type='workflow.operation.settled', origin='kernel', payload=identity)
    control_event = ZfEvent(type='task.rework.requested', origin='kernel', task_id='TASK-1', payload=identity)
    current = admission('current-impl', 'current-attempt', 'b' * 40, 'passed',
        ['event:' + completed.id, 'event:' + control_event.id])
    events = runtime.event_log.read_all()
    stop = next(i for i, e in enumerate(events) if e.type == 'run.cancelled')
    events[stop:stop] = [dispatch, earlier, settled, start, completed, control_event, current]
    refs = continuation_input_refs(runtime.state_dir, events, frozen.payload['workflow_run_id'])
    body = hydrate_sidecar_ref(runtime.state_dir, next(r for r in refs
        if r['source_id'] == 'continuation-tool-facts-TASK-1')).payload
    fact, non_execution = body['referenced_tool_facts']
    assert fact['source_semantic_verdict'] == 'blocked'
    assert fact['observation']['claims_status'] == 'host_execution_fact_not_product_verdict'
    assert fact['observation']['exit_code'] == 1
    assert 'assertion RED' in fact['observation']['output']
    assert 'private-value' not in fact['observation']['output']
    assert non_execution['status'] == 'unavailable'
    assert non_execution['event_type'] == 'task.rework.requested'
    assert non_execution['reason'] == 'not_a_bound_execution_observation'
