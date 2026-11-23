"""Bind one Candidate aggregate to all admitted Reader outcomes for OA review."""
from pathlib import Path

from zf.core.events import ZfEvent
from zf.runtime.candidate_plan_recovery import candidate_checkpoint_binding, candidate_plan_scope
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations
from zf.runtime.candidate_timeout_members import candidate_failed_members, candidate_unresolved_timeout


def aggregate_candidate_binding(
    state_dir: Path, events: list[ZfEvent], *, failure_id: str, run_id: str, task_id: str,
    require_completed_dependencies: bool = False,
) -> dict:
    failure = next((e for e in events if e.id == failure_id), None)
    if (not failure or failure.origin != 'kernel' or failure.type not in {'test.failed', 'verify.failed'}
            or failure.payload.get('workflow_run_id') != run_id):
        raise ValueError('candidate aggregate failure missing')
    history = events[:events.index(failure)]
    aggregate = next((e for e in history if e.id == failure.causation_id), None)
    fanout = failure.payload.get('fanout_id')
    if (not aggregate or aggregate.origin != 'kernel' or aggregate.type != 'fanout.aggregate.completed'
            or aggregate.payload.get('status') != 'failed'
            or not fanout or aggregate.payload.get('fanout_id') != fanout):
        raise ValueError('candidate aggregate lineage mismatch')
    children = candidate_failed_members(events, aggregate)
    operations = reduce_workflow_operations(events)
    members, semantic = [], []
    unresolved, unresolved_scopes = [], []
    for child_id in sorted(children):
        child = children[child_id]
        if not child.payload.get('control_result_ref'):
            if require_completed_dependencies:
                raise ValueError('candidate dependency lacks admitted result')
            member, scope = candidate_unresolved_timeout(state_dir, events, child, aggregate,
                operations, run_id=run_id, task_id=task_id)
            unresolved.append(member)
            unresolved_scopes.append(scope)
            continue
        op_id = child.payload.get('operation_id')
        op = operations.get(op_id, {})
        ref = op.get('admitted_control_result_ref')
        admitted = next((e for e in history if e.id == op.get('call_result_admitted_event_id')), None)
        if (op.get('operation_type') != 'fanout_reader_child' or op.get('status') != 'settled'
                or op.get('workflow_run_id') != run_id or op.get('task_id') != task_id
                or not ref or child.payload.get('control_result_ref') != ref
                or child.payload.get('request_hash') != op.get('request_hash')
                or not admitted or admitted.origin != 'kernel'
                or admitted.type != 'workflow.call.result.admitted'
                or admitted.payload.get('control_result_ref') != ref
                or admitted.payload.get('operation_id') != op_id
                or admitted.payload.get('request_hash') != op.get('request_hash')):
            raise ValueError('candidate member lacks admitted result')
        result = hydrate_sidecar_ref(state_dir, ref).payload
        identity = {'operation_id': op_id, 'workflow_run_id': run_id, 'task_id': task_id,
                    'role_instance': op.get('role_instance'), 'request_hash': op.get('request_hash'),
                    'attempt_id': op.get('active_attempt_id'), 'verdict': op.get('semantic_verdict'),
                    'stage_id': child.payload.get('stage_id'), 'output_profile_id': 'candidate-verify'}
        if (not isinstance(result, dict) or result.get('schema_version') != 'verification-result.v1'
                or any(not value or result.get(key) != value for key, value in identity.items())):
            raise ValueError('candidate member result identity mismatch')
        if require_completed_dependencies and (
            result.get('failure_class') != 'dependency_blocked' or result['verdict'] != 'blocked'
            or result.get('execution_status') != 'completed'
        ):
            raise ValueError('candidate reader did not complete a dependency verdict')
        scope = candidate_plan_scope(state_dir, events, result)
        if scope['anchor_task_id'] != task_id:
            raise ValueError('candidate member anchor mismatch')
        member = {**identity, 'child_id': child_id, 'control_result_ref': ref,
                  'execution_status': result.get('execution_status')}
        members.append(member)
        if result['verdict'] in {'rejected', 'blocked'} and result.get('execution_status') == 'completed':
            bound = candidate_checkpoint_binding(state_dir, events, failure_id=failure_id,
                operation_id=op_id, run_id=run_id, task_id=task_id)
            semantic.append((op_id, bound))
        elif result['verdict'] not in {'abstain', 'abstained', 'blocked'}:
            raise ValueError('candidate member outcome unavailable')
    if not semantic:
        raise ValueError('candidate aggregate has no completed semantic result')
    # The primary Reader is an identity anchor, not a selected semantic winner.
    op_id, primary = min(semantic, key=lambda row: row[0])
    if any(any(scope[key] != primary[key] for key in (
            'candidate_event_id', 'task_map_generation', 'source_commit', 'plan_artifact_package_digest'))
            for scope in unresolved_scopes):
        raise ValueError('candidate timeout member scope differs')
    scopes = [bound['failed_task_ids'] for _, bound in semantic]
    affected = sorted({task for scope in scopes for task in scope}) if all(scopes) else []
    completed = {e.payload.get('child_id'): e for e in history[:history.index(aggregate)]
                 if e.origin == 'kernel' and e.payload.get('fanout_id') == fanout
                 and e.type in {'fanout.child.completed', 'fanout.child.failed',
                               'fanout.child.cancelled', 'fanout.child.dispatched'}}
    completed_facts = [{'child_id': key, 'role_instance': event.payload.get('role_instance'),
                        'child_event_id': event.id, 'outcome': 'reported_completed'}
                       for key, event in completed.items() if event.type == 'fanout.child.completed']
    from zf.runtime.candidate_finding_partition import candidate_findings

    return {**primary, 'operation_id': op_id, 'failed_task_ids': affected,
            'findings': candidate_findings(state_dir, members, unresolved, events),
            'members': members, 'unresolved_members': unresolved, 'completed_members': completed_facts}
