"""Resume admitted aggregate rebuilds left pending by reversible Run blocking."""
from zf.runtime.pause_lifecycle import is_dispatch_paused
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.run_scope import resolve_run_for_event


def candidate_binding_current(runtime, events, source, run):
    payload = source.payload
    if source.type == 'discriminator.failed':
        payload = payload.get('blocked_event_payload') or {}
    if not payload.get('candidate_head_commit'):
        return True  # Generic aggregates without a Candidate retain their normal fences.
    from zf.runtime.candidate_verification_authority import frozen_candidate_for_payload
    from zf.runtime.candidate_continuation import _git_commit

    current = frozen_candidate_for_payload(events, run, payload={})
    native = current is not None
    if native:
        position = next((index for index, event in enumerate(events) if event.id == source.id), None)
        if position is None:
            return False
        starts = [(index, event) for index, event in enumerate(events[:position + 1])
            if event.type == 'fanout.started' and event.origin == 'kernel'
            and event.payload.get('fanout_id') == payload.get('fanout_id')]
        boundary, start = starts[-1] if starts else (position, source)
        trigger = start.payload.get('trigger_payload') or payload
        bound = frozen_candidate_for_payload(events[:boundary + 1], run,
            payload=trigger, authority_events=events)
        if bound is None or bound.id != current.id:
            return False
        if trigger.get('source_event_id'):
            evidence = next(((index, event) for index, event in enumerate(events[:boundary])
                if event.id == trigger['source_event_id']), None)
            if evidence is None:
                return False
            evidence_index, evidence_event = evidence
            if evidence_event.origin != 'kernel' or resolve_run_for_event(events, evidence_event) != run:
                return False
            evidence_bound = frozen_candidate_for_payload(events[:evidence_index + 1], run,
                payload=evidence_event.payload, authority_events=events)
            if evidence_bound is None or evidence_bound.id != current.id:
                return False
    if current is None:
        current = next((event for event in reversed(events) if event.type == 'candidate.ready'
            and event.origin in {'', 'kernel'} and event.actor in {'zf-cli', 'orchestrator'}
            and (event.payload.get('workflow_run_id') or event.correlation_id) == run), None)
    if current is None:
        return False
    manifest = runtime._fanout_manifest(payload['fanout_id']) if payload.get('fanout_id') else {}
    trigger = manifest.get('trigger_payload') or {}
    if trigger.get('handoff_kind') == 'partial_task_continuation':
        return bool(trigger.get('candidate_event_id') == current.id
            and payload['candidate_head_commit'] == trigger.get('continuation_commit')
            and payload.get('candidate_ref') == trigger.get('continuation_ref')
            and _git_commit(runtime.project_root, trigger.get('continuation_ref', ''))
                == trigger['continuation_commit'])
    keys = ('candidate_head_commit', 'candidate_ref')
    if native:
        keys += ('task_map_generation', 'plan_artifact_package_ref', 'plan_artifact_package_digest')
    for key in keys:
        if payload.get(key) and payload[key] != current.payload.get(key):
            return False
    return _git_commit(runtime.project_root, current.payload.get('candidate_ref', '')) == payload['candidate_head_commit']


def reconcile_pending_aggregate_rebuilds(runtime) -> None:
    events = runtime.event_log.read_all()
    if is_dispatch_paused(runtime.state_dir, events=events):
        return
    cancelled = {event.payload.get('fanout_id') for event in events
        if event.type == 'fanout.cancelled' and event.origin in {'', 'kernel'}}
    aggregates = {(event.payload.get('fanout_id'), event.payload.get('rebuild_of')): event
        for event in events if event.type == 'fanout.aggregate.completed' and event.origin == 'kernel'}
    by_id = {event.id: event for event in events}
    projection = build_run_admission_projection(events)
    for event in events:
        if event.type != 'fanout.aggregate.rebuild.requested' or event.origin != 'kernel':
            continue
        scope = (event.payload.get('fanout_id'), event.payload.get('source_event_id'))
        if not all(scope) or scope[0] in cancelled:
            continue
        run = resolve_run_for_event(events, event)
        entry = projection.runs.get(run)
        source = by_id.get(scope[1])
        if entry is None or entry.status != 'running' or source is None:
            continue
        if runtime._fanout_identity_stale_reason(scope[0])[0]:
            continue
        if not candidate_binding_current(runtime, events, source, run):
            continue
        aggregate = aggregates.get(scope)
        if aggregate is not None:
            if aggregate.payload.get('rebuild_publication_ref'):
                from zf.runtime.reader_aggregate_publication import publish_prepared

                publish_prepared(runtime, aggregate)
            # Legacy completed aggregates without an exact publication body
            # are not reconstructed by guessing semantic payload fields.
            continue
        # Reuse the original reducer's source/manifest and terminal-child gates.
        runtime._rebuild_reader_fanout_aggregate(event)
