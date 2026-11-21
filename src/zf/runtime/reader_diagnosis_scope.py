"""Bind a diagnosis reply to its Reader failure, independently of siblings."""
from zf.runtime.orchestrator_agent_execution_policy import run_goal_objective_snapshot
from zf.runtime.workflow_operation import reduce_workflow_operations

_FIELDS = ('operation_id', 'workflow_run_id', 'task_id', 'operation_type',
           'parent_stage_id', 'role_instance', 'request_hash', 'active_attempt_id',
           'status', 'semantic_verdict', 'admitted_control_result_ref')


def bind_reader_diagnosis_scope(events: list, request, payload: dict) -> dict:
    """Only a kernel request's exact failure can narrow its progress fence."""
    target = payload.get('target_operation_id')
    prior = events[:events.index(request) + 1]
    failures = set(request.payload.get('source_event_ids') or [])
    failure = next((e for e in prior if e.id in failures and e.origin == 'kernel'
                    and e.type in {'task.attempt.failed', 'workflow.operation.failed',
                                   'workflow.operation.blocked'}
                    and e.payload.get('operation_id') == target), None)
    op = reduce_workflow_operations(prior).get(target, {})
    if (request.origin != 'kernel' or failure is None
            or op.get('operation_type') != 'fanout_reader_child'
            or op.get('status') not in {'failed', 'blocked', 'settled'}
            or (op.get('status') == 'settled'
                and op.get('semantic_verdict') not in {'abstained', 'blocked', 'failed'})
            or not op.get('request_hash') or not op.get('active_attempt_id')
            or any(op.get(k) != payload.get(k) for k in ('workflow_run_id', 'task_id'))
            or op['active_attempt_id'] != payload.get('active_attempt_id')
            or not _failure_binding_matches(prior, failure, op)):
        return payload
    precondition = payload.get('operation_precondition') or {}
    if precondition.get('scope'):
        return payload
    return {**payload, 'operation_precondition': {
        **precondition, 'scope': 'reader_operation',
        'operation': {k: op.get(k) for k in _FIELDS},
        'goal_objective_event_id': run_goal_objective_snapshot(
            prior, op['workflow_run_id'])['objective_event_id'],
    }}


def _failure_binding_matches(events: list, failure, operation: dict) -> bool:
    body = failure.payload
    if (body.get('workflow_run_id') != operation.get('workflow_run_id')
            or (failure.task_id or body.get('task_id')) != operation.get('task_id')):
        return False
    if body.get('request_hash'):
        return body['request_hash'] == operation['request_hash']
    # role-v1 TaskAttempt IDs differ from immutable call attempts. Bind the
    # actual settlement receipt and dispatch, not the TaskAttempt ID as a call.
    if (failure.type != 'task.attempt.failed' or body.get('identity_version') != 'role-v1'
            or not body.get('dispatch_id') or body['dispatch_id'] != operation.get('dispatch_id')
            or operation.get('status') != 'settled'
            or operation.get('semantic_verdict') not in {'abstained', 'blocked', 'failed'}):
        return False
    admitted = next((e for e in events if e.id == body.get('admission_event_id')), None)
    ref = operation.get('admitted_call_result_ref') or {}
    return bool(admitted and admitted.origin == 'kernel' and admitted.type == 'workflow.operation.settled'
        and ref and ref.get('source_event_id') == body.get('source_event_id')
        and admitted.payload.get('admitted_call_result_ref') == ref
        and all(admitted.payload.get(k) == operation.get(k)
                for k in ('operation_id', 'workflow_run_id', 'task_id', 'request_hash')))


def reader_diagnosis_current(events: list, action: dict, precondition: dict) -> bool:
    from zf.runtime.recovery_step import recommendation_target_current

    expected = precondition.get('operation') or {}
    target = action.get('target_operation_id')
    current = reduce_workflow_operations(events).get(target, {})
    return bool(target and expected.get('operation_id') == target
        and expected.get('operation_type') == 'fanout_reader_child'
        and expected.get('active_attempt_id') == action.get('active_attempt_id')
        and expected.get('workflow_run_id') == action.get('workflow_run_id')
        and expected.get('task_id') == action.get('task_id')
        and {k: current.get(k) for k in _FIELDS} == expected
        and recommendation_target_current(events, action)
        and run_goal_objective_snapshot(events, expected['workflow_run_id'])['objective_event_id']
        == precondition.get('goal_objective_event_id'))


def has_retired_reader_diagnosis(events: list, action, state) -> bool:
    """A consumed stale reply cannot reserve the next failure's diagnostic slot.

    This reopens only read-only diagnosis for a strictly newer kernel failure.
    Active requests, human waits and all mutating recovery keep their gates.
    """
    if (state.get('status') not in {'diagnosing', 'verifying'}
            or not action.get('workflow_run_id')
            or not (action.get('action') == 'diagnose-attention'
                    or (action.get('action') == 'resident-agent-reprompt'
                        and action.get('request_kind') == 'recovery_diagnosis'))):
        return False
    indexed = {e.id: (i, e) for i, e in enumerate(events)}
    pending = indexed.get(state.get('pending_event_id'))
    sources = action.get('source_event_ids') or []
    current = indexed.get(sources[0]) if len(sources) == 1 else None
    if not pending or not current:
        return False
    request = pending[1]
    old_ids = request.payload.get('source_event_ids') or []
    old = indexed.get(old_ids[0]) if len(old_ids) == 1 else None
    if (request.origin != 'kernel' or request.payload.get('request_kind') != 'recovery_diagnosis'
            or not old or old[0] >= current[0]
            or old[1].origin != 'kernel' or current[1].origin != 'kernel'
            or old[1].type != current[1].type
            or any(e.payload.get('failure_kind') != 'infra' or not e.payload.get('fanout_id')
                   or e.payload.get('workflow_run_id') != action.get('workflow_run_id')
                   for e in (old[1], current[1]))):
        return False
    request_id = request.payload.get('request_id')
    delivery = next((e for e in events[pending[0]:] if e.origin == 'kernel'
                     and e.type == 'run.manager.resident.prompted'
                     and e.payload.get('request_id') == request_id), None)
    if not request_id or not delivery:
        return False
    from zf.runtime.recovery_step import diagnosis_reply_matches_request

    replies = {e.id for e in events[pending[0]:] if diagnosis_reply_matches_request(
        e, {**request.payload, 'instance_id': delivery.payload.get('instance_id')})}
    return any(e.origin == 'kernel' and e.type == 'run.manager.agent.recommendation.consumed'
               and e.payload.get('source_event_id') in replies
               and e.payload.get('status') == 'superseded'
               and e.payload.get('reason') == 'operation_precondition_changed'
               for e in events[pending[0]:])
