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


def test_impl_tool_facts_keep_original_identity_and_missing_references(source):
    from zf.runtime.continuation_evidence import continuation_input_refs
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    runtime, old_run, frozen = successor(source)
    events = runtime.event_log.read_all()
    stop = next(i for i, e in enumerate(events) if e.type == 'run.cancelled')
    fact = ZfEvent(type='codex.hook.post_tool_use', actor='dev-1', origin='kernel',
        payload={'tool_input': {'command': 'node --test'}, 'tool_response': 'tests fail 2'})
    control = write_immutable_json_sidecar(runtime.state_dir, {
        'schema_version': 'implementation-result.v1', 'workflow_run_id': old_run,
        'task_id': 'TASK-1', 'role_instance': 'dev-1', 'target_commit': 'b' * 40,
        'self_check': {'evidence_refs': ['event:' + fact.id, 'event:missing']}},
        root='test', kind='control', schema_version='implementation-result.v1', created_by='test')
    admitted = ZfEvent(type='workflow.call.result.admitted', task_id='TASK-1', origin='kernel',
        payload={'workflow_run_id': old_run, 'control_result_schema': 'implementation-result.v1',
                 'semantic_verdict': 'passed', 'control_result_ref': control})
    events[stop:stop] = [fact, admitted]
    refs = continuation_input_refs(runtime.state_dir, events, frozen.payload['workflow_run_id'])
    tools = next(r for r in refs if r['source_id'] == 'continuation-tool-facts-TASK-1')
    body = hydrate_sidecar_ref(runtime.state_dir, tools).payload
    assert body['source_run_id'] == old_run
    assert body['referenced_tool_facts'][0]['payload']['tool_response'] == 'tests fail 2'
    assert body['referenced_tool_facts'][1]['status'] == 'unavailable'
