"""Turn incomplete diagnosis into one bound resident decision, not blind retry."""
from hashlib import sha256

from zf.core.events.model import ZfEvent


def pending_autoresearch_followups(events: list[ZfEvent]) -> list[dict]:
    from zf.runtime.run_manager import _autoresearch_request_id, _autoresearch_result_request_id
    requests = {_autoresearch_request_id(e, e.payload): e
                for e in events if e.type == 'run.manager.autoresearch.requested'}
    terminal = {_autoresearch_result_request_id(e, e.payload): e for e in events
                if e.type in {'autoresearch.loop.failed', 'autoresearch.loop.completed'}}
    out = []
    for request_id, event in terminal.items():
        if event.type != 'autoresearch.loop.failed':
            continue
        request = requests.get(request_id)
        if not request_id or request is None:
            continue
        diagnosis = event.payload.get('diagnosis') or {}
        if diagnosis.get('status') == 'waiting' and diagnosis.get('diagnosis_owner') == 'owner':
            continue  # An explicit owner decision retains its authorized path.
        checkpoint = 'ar-outcome-' + sha256(request_id.encode()).hexdigest()[:16]
        original = request.payload
        out.append({
            'schema_version': 'run-manager.pending-action.v1',
            'action': 'diagnose-attention', 'safe_resume_action': 'diagnose_attention',
            'checkpoint_id': checkpoint, 'fingerprint': checkpoint,
            'recovery_case_id': 'rcase-' + checkpoint,
            'parent_recovery_case_id': original.get('recovery_case_id', ''),
            'workflow_run_id': original.get('workflow_run_id', ''),
            'task_id': original.get('task_id', ''),
            'source_event_ids': [request.id, event.id],
            'failure_class': 'autoresearch_diagnosis_incomplete',
            'recovery_phase': 'diagnosis_result', 'diagnosis': diagnosis,
            'checkpoint_ref': event.payload.get('checkpoint_ref', {}),
            'diagnosis_ref': event.payload.get('diagnosis_ref', {}),
            'cleanup_pending': ((event.payload.get('worktree_lifecycle') or {}).get('reason') == 'runtime_cleanup_failed'
                or ((event.payload.get('worktree_lifecycle') or {}).get('runtime_cleanup') or {}).get('status') == 'retained'),
            'worktree_lifecycle': event.payload.get('worktree_lifecycle') or {},
            'owner_route': 'run_manager', 'suggested_route': 'run_manager_recovery',
            'action_policy': 'needs_diagnosis', 'intervention_class': 'diagnose',
            'expected_output': ['run.manager.agent.recommendation'],
            'expected_downstream_events': ['run.manager.resident.prompted'],
            'reason': 'Diagnosis did not complete. Inspect the bound phase, partial reports and '
                      'cleanup separately. Choose an authorized bounded continuation or explicit '
                      'owner request; do not repeat the whole experiment, infer a product defect, '
                      'expand caps, or claim the original task recovered from this receipt.',
        })
    return out
