"""Recover missing failure metadata and retire explicitly reviewed misrouted readers."""
from pathlib import Path

from zf.core.events import ZfEvent
from zf.runtime.fanout import FanoutManifestProjector
from zf.runtime.failure_kind import aggregate_failure_kind
from zf.runtime.stage_failure_replan import _event_run_id
from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations


def repaired_failure_kind(state_dir, events, failure):
    """Read immutable admitted child evidence; never rewrite historical events."""
    from zf.runtime.plan_reader_preregistration import plan_failure_kind

    explicit = plan_failure_kind(events, failure)
    if (explicit == 'infra' and failure.origin == 'kernel'
            and failure.type in {'test.failed', 'verify.failed'}
            and failure.payload.get('reason') == 'timeout'):
        # Legacy timeout aggregates omitted settled semantic siblings. Their
        # host receipts cannot authorize the pure-infrastructure successor.
        operations = reduce_workflow_operations(events)
        semantic = [operations.get(e.payload.get('operation_id'), {}) for e in events
                    if e.type == 'fanout.child.failed' and e.origin == 'kernel'
                    and e.payload.get('fanout_id') == failure.payload.get('fanout_id')]
        if any(op.get('status') == 'settled' and op.get('call_result_admitted_event_id')
               and op.get('semantic_verdict') in {'rejected', 'blocked'} for op in semantic):
            from zf.runtime.candidate_failure_checkpoint import aggregate_candidate_binding
            try:
                aggregate_candidate_binding(state_dir, events, failure_id=failure.id,
                    run_id=_event_run_id(failure),
                    task_id=str(failure.task_id or failure.payload.get('pdd_id') or ''))
            except (ValueError, OSError, KeyError):
                return 'unknown'
            return 'mixed'
    if explicit:
        return explicit
    fanout = str(failure.payload.get('fanout_id') or '')
    if not fanout:
        return ''
    history = events[:events.index(failure) + 1]
    manifest = FanoutManifestProjector(state_dir).rebuild(fanout, history)
    children = [c for c in manifest['children'] if c.get('status') == 'failed']
    operations = reduce_workflow_operations(history)
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    for child in children:
        op = operations.get(child.get('operation_id'), {})
        descriptor = child.get('control_result_ref') or {}
        if (not op.get('call_result_admitted_event_id') or not descriptor
                or op.get('admitted_control_result_ref') != descriptor
                or op.get('workflow_run_id') != _event_run_id(failure)):
            return ''
        result = hydrate_sidecar_ref(state_dir, descriptor).payload
        if any(result.get(key) != op.get(key) for key in ('operation_id', 'request_hash', 'workflow_run_id')):
            return ''
        child.update(control_result_schema=result.get('schema_version'), semantic_verdict=result.get('verdict'),
                     failure_class=result.get('failure_class'))
    return aggregate_failure_kind(children)


def retire_misrouted_readers(service, events, payload, requested):
    """Only cancel stopped, explicitly named Plan descendants of the repaired failure."""
    selected = payload.get('misrouted_operation_ids') or []
    if not selected:
        return
    if not isinstance(selected, list) or any(not isinstance(item, str) for item in selected):
        raise ValueError('misrouted_operation_ids must be an explicit operation list')
    failure = next(e for e in events if e.id == payload['repaired_failure_event_id'])
    run_id = _event_run_id(failure)
    descendants = {failure.id}
    fanouts = set()
    dispatches = {}
    for event in events[events.index(failure) + 1:]:
        body = event.payload
        if _event_run_id(event) != run_id:
            continue
        linked = any(ref in descendants for ref in (event.causation_id, body.get('rework_of'),
                     body.get('source_event_id'), body.get('trigger_event_id')))
        if not linked and body.get('fanout_id') not in fanouts:
            continue
        descendants.add(event.id)
        if event.type == 'fanout.started':
            fanouts.add(body.get('fanout_id'))
        if event.type == 'fanout.child.dispatched':
            dispatches[body.get('operation_id')] = event
        if event.type in {'task_map.ready', 'plan.approved', 'prd.plan.approved'}:
            raise ValueError('misrouted Plan has published new authority; reconcile before repair')
    operations = reduce_workflow_operations(events)
    active = {key for key in dispatches if operations.get(key, {}).get('status') in
              {'requested', 'reserved', 'running', 'suspended'}}
    if active - set(selected):
        raise ValueError('repair must account for every active causal descendant')
    reviewed = []
    from zf.runtime.environment_usage import assert_workspace_idle, EnvironmentInUse
    from zf.runtime.workdirs import WorkdirManager
    manager = WorkdirManager(state_dir=service.state_dir, project_root=service.project_root, config=service.config)
    for key in selected:
        op = operations.get(key, {})
        dispatch = dispatches.get(key)
        if (dispatch is None or op.get('workflow_run_id') != run_id or op.get('attempt_domain') != 'plan'
                or op.get('call_result_admitted_event_id') or op.get('status') not in {'suspended', 'cancelled'}):
            raise ValueError('misrouted operation must be a stopped, unadmitted causal Plan reader')
        role = next((r for r in service.config.roles if r.instance_id == op['role_instance']), None)
        if role is None:
            raise ValueError('misrouted reader role is no longer configured')
        plan = manager.plan(role)
        if not plan.enabled or plan.role_kind != 'reader':
            raise ValueError('misrouted reader requires an owned reader workspace')
        from zf.core.safety import assert_owned_workdir
        assert_owned_workdir(Path(plan.workdir), state_dir=service.state_dir)
        try:
            assert_workspace_idle(Path(plan.project_path))
        except EnvironmentInUse as exc:
            raise ValueError(str(exc)) from exc
        reviewed.append((op, dispatch))
    # Validate the whole batch before any effect. Existing lifecycle service owns cancellation.
    owner = WorkflowOperationService(state_dir=service.state_dir, event_log=service.writer.event_log,
                                     event_writer=service.writer)
    for op, dispatch in reviewed:
        owner.cancel(operation_id=op['operation_id'], request_hash=op['request_hash'],
            workflow_run_id=run_id, task_id=op.get('task_id', ''),
            reason='operator reviewed infrastructure misrouting repair', causation_id=requested.id,
            correlation_id=run_id)
        fid = dispatch.payload['fanout_id']
        if not any(e.type == 'fanout.cancelled' and e.payload.get('fanout_id') == fid for e in events):
            service.writer.append(ZfEvent(type='fanout.cancelled', actor=service.actor,
                correlation_id=run_id, causation_id=requested.id, payload={
                    'fanout_id': fid, 'stage_id': dispatch.payload['stage_id'], 'trace_id': run_id,
                    'workflow_run_id': run_id, 'reason': 'operator reviewed infrastructure misrouting repair',
                    'repaired_failure_event_id': failure.id, 'approval_ref': payload['approval_ref']}))
