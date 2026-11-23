import pytest

from tests.test_candidate_host_consumption import evidence, prepare
from zf.core.events import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.candidate_host_consumption import admitted_host_commands
from zf.runtime.candidate_media_review import KEY, SCHEMA, reviewed_paths, validate_owner_review
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations


@pytest.mark.parametrize('evidence', ['directory-output'], indirect=True)
def test_directory_receipt_requires_bound_owner_review_for_new_readers(evidence):
    runtime, seeded, _, _, receipt_ref, host = evidence
    state = runtime.state_dir
    original = hydrate_sidecar_ref(state, receipt_ref).payload
    run = seeded['run_id']
    def emit(kind, data, *, cause=None, actor='zf-cli', origin='kernel'):
        return runtime.event_writer.append(ZfEvent(type=kind, actor=actor, origin=origin,
            correlation_id=run, causation_id=cause, payload=data))
    # A pre-existing Reader remains bound to its unavailable projection.
    _, old = prepare(runtime, seeded, 'old-reviewer')
    old_ref = old['candidate_host_evidence_ref']
    assert hydrate_sidecar_ref(state, old_ref).payload['receipts'][0]['status'] == 'unavailable'
    failure = emit('test.failed', {'workflow_run_id': run, 'pdd_id': 'FLOW-ANCHOR'})
    native = next(e for e in runtime.event_log.read_all() if e.type == 'candidate.ready')
    review = {'schema_version': SCHEMA, 'workflow_run_id': run, 'failure_event_id': failure.id,
        'candidate_event_id': native.id, 'target_commit': seeded['candidate_head'],
        'host_receipt_event_id': host.id, 'original_receipt': receipt_ref,
        'original_command': original['manifest']['command'], 'producer_paths': ['ZF_EVIDENCE_DIR/artifacts/probe.json']}
    ref = write_immutable_json_sidecar(state, review, root='operator/candidate-media-reviews',
        kind='candidate_media_review', schema_version=SCHEMA, created_by='operator')
    body = {KEY: ref, 'repaired_failure_event_id': failure.id, 'approval_ref': 'owner:review'}
    intent = emit('operator.intent.created', {'action':'stage-replan-new-generation', 'action_request':body},
                  actor='operator', origin='external')
    assert validate_owner_review(state, runtime.event_log.read_all(), intent, body, failure, native) == ref
    with pytest.raises(ValueError):
        validate_owner_review(state, runtime.event_log.read_all(), intent, {**body, 'approval_ref':'changed'}, failure, native)
    effect = emit('runtime.action.attempt.started', {'action':'stage-replan-new-generation','payload':body}, cause=intent.id)
    trigger = emit('candidate.ready', {**native.payload, KEY:ref, 'workflow_run_id':run, 'repaired_failure_event_id':failure.id,
                                      'candidate_snapshot_event_id':native.id, 'schema_version':'reader-repair'},
                   cause=intent.id)
    emit('fanout.started', {'fanout_id':'fanout-candidate','trace_id':run,'trigger_event_id':trigger.id})
    with pytest.raises(ValueError, match='incomplete'):
        prepare(runtime, seeded, 'incomplete-action')
    emit('runtime.action.attempt.completed', {'action':'stage-replan-new-generation','status':'applied','result_event_id':trigger.id},
         cause=effect.id)
    op, new = prepare(runtime, seeded, 'independent-reviewer')
    binding = hydrate_sidecar_ref(state, new['candidate_host_evidence_ref']).payload
    assert binding['receipts'][0]['status'] == 'available' and binding['authorizes_execution'] is False
    assert any(row['source_id'] == 'candidate-host-review' for row in binding['input_refs'])
    assert any(row['source_id'] == 'candidate-host-review' for row in new['required_reads'])
    events = runtime.event_log.read_all()
    operation = reduce_workflow_operations(events)[op.operation_id]
    assert admitted_host_commands(state, {**new, 'host_execution_receipt_ids':[host.id],
        'request_hash': op.request_hash, 'verdict':'passed'}, operation=operation, events=events) == [review['original_command']]
    assert hydrate_sidecar_ref(state, old_ref).payload['receipts'][0]['status'] == 'unavailable'
    assert not any(e.type == 'test.passed' for e in events)


@pytest.mark.parametrize('old,new', [
    (['valid.png'], ['another.png']), (['old/', 'retain.png'], ['new.png']),
    (['old/', 'keep.png'], ['keep.png']), (['../bad/'], ['new.png']), (['old/'], []), (['old/'], ['../new.png']),
])
def test_mapping_cannot_drop_a_named_obligation_or_escape(old, new):
    command = {'owner':'candidate_verify', 'producer_paths':old}
    with pytest.raises(ValueError):
        reviewed_paths(command, {'original_command':command, 'producer_paths':new})
