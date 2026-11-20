"""Retire a task-scoped OA failure notice from its admitted causal chain."""
from zf.core.events import ZfEvent


def orchestrator_failure_retired(events: list[ZfEvent], notice: ZfEvent) -> bool:
    """A completed target retires old diagnostics, never grants owner approval."""
    if notice.origin != 'kernel' or notice.actor != 'zf-cli':
        return False
    position = next((i for i, e in enumerate(events) if e.id == notice.id), -1)
    if position < 0:
        return False
    before = events[:position]
    by_id = {e.id: e for e in before}
    op_id = notice.payload.get('orchestrator_operation_id')
    ref = notice.payload.get('orchestrator_decision_ref')
    submit = by_id.get(str(notice.causation_id or ''))
    if (not op_id or not isinstance(ref, dict) or not ref.get('ref') or not ref.get('sha256')
            or submit is None
            or submit.type != 'orchestrator.semantic.decision.submitted'
            or submit.payload.get('operation_id') != op_id
            or submit.payload.get('control_result_ref') != ref):
        return False
    admitted = [e for e in before if e.type == 'orchestrator.semantic.decision.admitted'
                and e.origin == 'kernel' and e.actor == 'zf-cli'
                and e.payload.get('operation_id') == op_id
                and e.payload.get('decision_event_id') == submit.id
                and e.payload.get('decision_ref') == ref
                and e.payload.get('checkpoint') == 'semantic_failure'
                and e.payload.get('decision') == 'escalate' and e.payload.get('status') == 'admitted']
    checkpoints = [e for e in before if e.type == 'orchestrator.semantic.checkpoint.requested'
                   and e.origin == 'kernel' and e.payload.get('operation_id') == op_id
                   and e.payload.get('checkpoint') == 'semantic_failure']
    if len(admitted) != 1 or len(checkpoints) != 1:
        return False
    admission, checkpoint = admitted[0], checkpoints[0]
    request = by_id.get(str(checkpoint.payload.get('source_event_id') or ''))
    if (request is None or request.origin != 'kernel'
            or request.type != 'orchestrator.semantic.failure.requested'
            or admission.payload.get('source_event_id') != request.id
            or request.payload.get('recovery_scope') != 'task'
            or request.payload.get('target_stage_id') not in {'impl', 'verify'}
            or not request.payload.get('target_operation_id')):
        return False
    run = str(request.payload.get('workflow_run_id') or '')
    task = str(request.task_id or '')
    generation = str(request.payload.get('task_map_generation') or '')
    if (not run or not task or not generation or request.payload.get('task_id') != task
            # Diagnosis/checkpoint correlation IDs may name the diagnosis;
            # their explicit workflow_run_id is the canonical Run binding.
            or any(e.payload.get('workflow_run_id') != run
                   for e in (request, checkpoint, admission))
            or any(e.correlation_id not in (None, '', run)
                   or e.payload.get('workflow_run_id') not in (None, '', run)
                   for e in (submit, notice))
            or not (before.index(request) < before.index(checkpoint)
                    < before.index(submit) < before.index(admission))):
        return False
    return any(e.type == 'task.done' and e.origin == 'kernel' and e.task_id == task
               and e.payload.get('task_id') in (None, '', task)
               and e.payload.get('workflow_run_id') == run
               and e.payload.get('run_id') in (None, '', run)
               and e.correlation_id in (None, '', run)
               and e.payload.get('task_map_generation') == generation
               and e.payload.get('authority') == 'task_integration_receipt'
               and e.payload.get('integration_event_id')
               and e.payload.get('integration_receipt_digest')
               for e in events[position + 1:])
