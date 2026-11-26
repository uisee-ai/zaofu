"""Reconcile a timed-out Goal rebuild only against its original Goal and target lineage."""
from collections.abc import Sequence

from zf.core.events import ZfEvent


def late_goal_rebuild_closure(events: Sequence[ZfEvent], index: int, timeout: ZfEvent) -> ZfEvent | None:
    pending = events[index]
    action = pending.payload
    if (action.get('action') != 'fanout-aggregate-rebuild'
            or action.get('failure_class') != 'goal_closure_identity_invalid'
            or 'flow.goal.closed' not in (action.get('expected_event_types') or ())
            or timeout.payload.get('effect_pending_event_id') != pending.id
            or timeout.causation_id != pending.id
            or timeout.payload.get('source_event_id') != action.get('source_event_id')):
        return None
    failure = next((row for row in events[:index] if row.id == action.get('source_event_id')
                    and row.type == 'goal.closure.identity.invalid' and row.origin == 'kernel'), None)
    if failure is None:
        return None
    original = failure.payload
    run, generation = action.get('workflow_run_id'), action.get('task_map_generation')
    if not run or not generation or (run, generation) != (
            original.get('workflow_run_id'), original.get('task_map_generation')):
        return None
    source_commit = original.get('candidate_head_commit')
    if not source_commit or not original.get('goal_id') or not original.get('plan_artifact_package_digest'):
        return None
    # A cherry-picked Task SHA differs from the frozen candidate head. Match the
    # original integration receipt in the native freeze before accepting that head.
    integrations = {}
    targets = {source_commit}
    for position, event in enumerate(events):
        body = event.payload
        if event.origin != 'kernel' or (body.get('workflow_run_id'), body.get('task_map_generation')) != (run, generation):
            continue
        if (event.type == 'integration.queue.integrated' and body.get('status') == 'integrated'
                and body.get('task_commit') == source_commit and body.get('task_id')):
            ref = body.get('receipt_ref')
            if (isinstance(ref, dict) and ref.get('kind') == 'task_integration_receipt' and ref.get('ref')
                    and ref.get('sha256') and ref['sha256'] == body.get('receipt_digest')):
                integrations[ref['sha256']] = (ref, body['task_id'])
        elif (event.type == 'candidate.ready' and body.get('status') == 'frozen'
                and not body.get('repaired_failure_event_id') and body.get('candidate_head_commit')
                and body.get('plan_artifact_package_digest') == original['plan_artifact_package_digest']):
            for ref in body.get('task_integration_receipt_refs') or ():
                member = integrations.get(ref.get('sha256')) if isinstance(ref, dict) else None
                if member and member[0] == ref and member[1] in (body.get('completed_task_ids') or ()):
                    targets.add(body['candidate_head_commit'])
                    break
        elif (position > index and event.type == 'flow.goal.closed' and body.get('goal_id') == original['goal_id']
                and body.get('plan_artifact_package_digest') == original['plan_artifact_package_digest']
                and body.get('candidate_head_commit') in targets):
            return event
    return None
