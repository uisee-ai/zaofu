"""One authorized reassessment of the same rejected candidate, without planning."""
from copy import deepcopy

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import canonical_json_sha256, write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref

KIND = 'plan_critic_reassessment'


def apply_reassessment(service, requested, action, requested_action, payload) -> dict:
    from zf.core.state.locks import locked_path
    from zf.core.task.store import TaskStore
    from zf.runtime.workflow_task_lifecycle import reopen_workflow_managed_task_from_run_recovery
    with locked_path(service.state_dir / 'locks' / 'reader-stage-recovery'):
        try:
            review, replayed = prepare_reassessment(service, requested, payload)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            return service._recovery_failed(requested, action, requested_action, str(exc), 409)
        if not replayed:
            goal_update = service.writer.append(ZfEvent(type='run.goal.updated', actor=service.actor,
                origin='kernel', causation_id=requested.id, correlation_id=review.correlation_id,
                task_id=review.task_id, payload={'run_id': review.payload['workflow_run_id'],
                    'workflow_run_id': review.payload['workflow_run_id'], 'status': 'active',
                    'reason': 'authorized_critic_reassessment', 'approval_ref': payload['approval_ref']}))
            reopen_workflow_managed_task_from_run_recovery(
                task_store=TaskStore(service.state_dir / 'kanban.json'), event_writer=service.writer,
                recovery_event=goal_update)
            service.writer.append(review)
        return service._recovery_ok(requested, review, action, requested_action,
            {'review_event_id': review.id, 'review_key': review.payload['review_key'], 'replayed': replayed})


def prepare_reassessment(service, requested: ZfEvent, payload: dict) -> tuple[ZfEvent, bool]:
    """Called only under the controlled recovery action's lock/authorization."""
    from zf.runtime.plan_synth_recovery_refs import resume_failed_critic_candidate
    from zf.runtime.plan_review_claims import _check_citation
    from zf.runtime.workflow_operation import reduce_workflow_operations

    if any(key in payload for key in ('input_task_ids', 'repair_target', 'repaired_failure_event_id', 'operator_feedback')):
        raise ValueError('review-only cannot change the candidate or planning scope')
    if not all(str(payload.get(k) or '').strip() for k in ('approval_ref', 'reason', 'escalation_event_id')):
        raise ValueError('review-only requires approval_ref, reason and escalation_event_id')
    citations = payload.get('counter_evidence')
    if not isinstance(citations, list) or not citations:
        raise ValueError('review-only requires exact current counter_evidence')
    events = service.writer.event_log.read_all()
    by_id = {event.id: event for event in events}
    escalation = by_id.get(payload['escalation_event_id'])
    if (escalation is None or escalation.type != 'human.escalate'
            or escalation.payload.get('failure_class') != 'stage_replan_cap_exhausted'
            or 'start_new_generation' not in escalation.payload.get('allowed_actions', [])):
        raise ValueError('review-only requires the authorized stage cap escalation')
    failure = by_id.get(escalation.payload.get('source_event_id'))
    if failure is None:
        raise ValueError('stage failure is missing')
    synth = by_id.get(failure.causation_id)
    if synth is None:
        raise ValueError('stage failure has no bound Critic result')
    refs = resume_failed_critic_candidate(service.state_dir, events, failure, synth.id)
    run, stage = failure.payload.get('workflow_run_id'), failure.payload.get('stage_id')
    fanout = synth.payload.get('fanout_id')
    starts = [e for e in events if e.type == 'fanout.started'
              and e.payload.get('workflow_run_id') == run and e.payload.get('stage_id') == stage]
    if not starts or starts[-1].payload.get('fanout_id') != fanout:
        raise ValueError('candidate was superseded or its fanout identity is unavailable')
    if any(e.type in {'run.goal.completed', 'run.failed', 'run.cancelled'}
           and e.payload.get('workflow_run_id', e.payload.get('run_id')) == run for e in events):
        raise ValueError('terminal Run cannot be reopened by review-only')
    dispatch = next(e for e in events if e.type == 'fanout.synth.dispatched'
                    and e.payload.get('operation_id') == synth.payload.get('operation_id'))
    sources = hydrate_sidecar_ref(service.state_dir, dispatch.payload['attempt_source_manifest']).payload
    current = {r['source_id'] for r in refs}
    for citation in citations:
        _check_citation(service.state_dir, sources, citation, current=current,
                        role=str(dispatch.payload.get('role_instance') or 'plan-critic'))
    key = canonical_json_sha256({'run': run, 'stage': stage, 'candidate_inputs': refs})
    body = {'schema_version': 'plan-review-checkpoint.v1', 'candidate_inputs': refs,
            'rejected_synth_event_id': synth.id, 'rejected_review_ref': synth.payload['control_result_ref'],
            'counter_evidence': citations, 'approval_ref': payload['approval_ref'], 'reason': payload['reason']}
    prior = next((e for e in events if e.type == 'fanout.aggregate.started'
                  and e.origin == 'kernel' and e.payload.get('review_key') == key), None)
    if prior:
        if hydrate_sidecar_ref(service.state_dir, prior.payload['review_checkpoint_ref']).payload != body:
            raise ValueError('unchanged candidate already used its one reassessment')
        return prior, True
    operations = reduce_workflow_operations(events)
    if any(op.get('workflow_run_id') == run and op.get('status') in {'requested', 'reserved', 'running', 'suspended'}
           for op in operations.values()):
        raise ValueError('review-only cannot overlap an active Run operation')
    from zf.runtime.fanout import FanoutManifestProjector
    manifest = FanoutManifestProjector(service.state_dir).rebuild(fanout, events)
    if ((manifest.get('aggregate') or {}).get('status') != 'failed'
            or (manifest.get('synth') or {}).get('last_event_id') != synth.id):
        raise ValueError('rejected review is no longer the current fanout outcome')
    if not manifest.get('children') or any(c.get('status') != 'completed' for c in manifest['children']):
        raise ValueError('review-only requires all original producer results completed')
    for ref in refs:
        _check_source(service.state_dir, ref)
    checkpoint = write_immutable_json_sidecar(service.state_dir, body, root='plan-synth/reassessments',
        kind='plan_review_checkpoint', schema_version=body['schema_version'], created_by='controlled-action',
        source_event_id=requested.id)
    return ZfEvent(type='fanout.aggregate.started', actor='zf-cli', origin='kernel',
        task_id=failure.task_id, correlation_id=run, causation_id=requested.id,
        payload={'workflow_run_id': run, 'stage_id': stage, 'fanout_id': fanout,
                 'trace_id': run, 'mode': 'wait_for_all', 'recovery_kind': KIND,
                 'review_key': key, 'review_checkpoint_ref': checkpoint}), False


def _check_source(state, source):
    from hashlib import sha256
    from zf.runtime.sidecar_refs import sidecar_path
    if sha256(sidecar_path(state, source['ref']).read_bytes()).hexdigest() != source['sha256']:
        raise ValueError('review candidate bytes changed after rejection')


def reassessment_manifest(state, manifest: dict) -> dict:
    """Only the review identity changes; producer inputs and old verdict persist."""
    body = hydrate_sidecar_ref(state, manifest['review_checkpoint_ref']).payload
    for source in body['candidate_inputs']:
        _check_source(state, source)
    result = deepcopy(manifest)
    result['trigger_event_id'] = manifest['review_request_event_id']
    result['review_input_refs'] = [
        {**manifest['review_checkpoint_ref'], 'source_id': 'plan-review-counter-evidence',
         'artifact_id': 'plan-review-counter-evidence.json', 'allowed_paths': ['$']},
        {**body['rejected_review_ref'], 'source_id': 'plan-review-prior-verdict',
         'artifact_id': 'plan-review-prior-verdict.json', 'allowed_paths': ['$']},
    ]
    return result


def validate_reassessment_inputs(state, manifest: dict, payload: dict) -> None:
    if not manifest.get('review_checkpoint_ref'):
        return
    body = hydrate_sidecar_ref(state, manifest['review_checkpoint_ref']).payload
    def bindings(rows):
        return sorted(tuple(row.get(key, '') for key in ('source_id', 'artifact_id', 'ref', 'sha256'))
                      for row in rows if str(row.get('source_id', '')).startswith('child-'))
    if bindings(payload['input_refs']) != bindings(body['candidate_inputs']):
        raise ValueError('current producer outputs differ from the authorized review candidate')


def reconcile_reassessments(runtime, events) -> None:
    """Durable aggregate requests use the existing fanout dispatch/restart path."""
    for event in events:
        if (event.type != 'fanout.aggregate.started' or event.origin != 'kernel'
                or event.payload.get('recovery_kind') != KIND):
            continue
        current = runtime._fanout_manifest(event.payload['fanout_id'])
        if current.get('review_request_event_id') == event.id and (
                runtime._fanout_synth_dispatched(current)
                or (current.get('aggregate') or {}).get('status') in {'completed', 'failed', 'timed_out', 'cancelled'}):
            continue
        from zf.runtime.fanout import FanoutManifestProjector
        manifest = FanoutManifestProjector(runtime.state_dir).write_manifest(
            event.payload['fanout_id'], runtime.event_log.read_all())
        if manifest and manifest.get('review_request_event_id') == event.id:
            runtime._evaluate_reader_fanout(event.payload['fanout_id'])


def reassessment_result_current(runtime, manifest, event) -> bool:
    if not manifest.get('review_request_event_id'):
        return True
    events = runtime.event_log.read_all()
    review_index = next((i for i, row in enumerate(events)
                         if row.id == manifest['review_request_event_id']), len(events))
    dispatches = [row for row in events[review_index + 1:] if row.type == 'fanout.synth.dispatched'
                  and row.payload.get('fanout_id') == manifest['fanout_id']]
    if (not dispatches and event.origin == 'kernel' and event.actor == 'zf-cli'
            and event.payload.get('status') == 'failed'
            and event.payload.get('review_request_event_id') == manifest['review_request_event_id']):
        return True  # A bound pre-dispatch failure must also close the recovery attempt.
    return bool(dispatches and all(event.payload.get(key)
        and event.payload.get(key) == dispatches[-1].payload.get(key)
        for key in ('operation_id', 'attempt_id', 'request_hash')))
