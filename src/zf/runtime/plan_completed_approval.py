"""Reuse proven Task completion when a late approval replays the same Plan."""
from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.candidate_continuation import _git_commit
from zf.runtime.candidate_verification_authority import (
    frozen_candidate_for_payload, prepare_candidate_verification_authority,
)
from zf.runtime.workflow_operation import reduce_workflow_operations
from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package
from zf.runtime.plan_output_lineage import (
    assert_package_task_map_matches, assert_package_explicit_ports_match,
)


def suppress_completed_plan_approval(runtime: Any, *, trigger_event: ZfEvent,
                                     loaded: Any, stage_id: str) -> bool:
    """Suppress dispatch only; this grants no Candidate/Judge/Goal verdict."""
    events = runtime.event_log.read_all()
    run = str(loaded.workflow_run_id or '')
    approval = next((e for e in reversed(events) if e.type in {'plan.approved', 'plan.rejected'}
                     and e.payload.get('plan_id') == trigger_event.id
                     and not e.payload.get('semantic_control')), None)
    if (not run or approval is None or approval.type != 'plan.approved'
            or approval.actor != 'operator' or approval.payload.get('auto')):
        return False
    candidate = frozen_candidate_for_payload(events, run, payload={})
    if candidate is None:
        return False
    body = candidate.payload
    # Task IDs alone are never equivalence. The exact admitted package binds
    # the Map and all acceptance ports; current child authority is checked below.
    for key in ('task_map_generation', 'plan_artifact_package_ref', 'plan_artifact_package_digest'):
        expected = str(getattr(loaded, key, '') or '')
        if not expected or body.get(key) != expected:
            return False
    admitted = next((e for e in reversed(events) if e.type == 'plan.artifact_package.admitted'
                     and e.payload.get('workflow_run_id') == run), None)
    if admitted is None or admitted.payload.get('package_digest') != loaded.plan_artifact_package_digest:
        return False
    try:
        package = hydrate_plan_artifact_package(runtime.state_dir, {
            'ref': loaded.plan_artifact_package_ref,
            'sha256': loaded.plan_artifact_package_digest})
        if package.get('workflow_run_id') != run or package.get('task_map_generation') != loaded.task_map_generation:
            return False
        incoming = {**trigger_event.payload, 'task_map_ref': loaded.task_map_ref}
        assert_package_task_map_matches(runtime.state_dir, runtime.project_root, package, incoming)
        assert_package_explicit_ports_match(runtime.state_dir, runtime.project_root, package, incoming,
            flow_kind=package['flow_kind'], producer_stage_id=package['producer_stage_id'], events=events)
    except (ValueError, OSError, KeyError):
        return False
    tasks = {str(item.get('task_id') or '') for item in loaded.task_items}
    completed = set(body.get('completed_task_ids') or [])
    if not tasks or '' in tasks or not tasks.issubset(completed):
        return False
    for task_id in completed:
        task = runtime.task_store.get(task_id)
        if task is None or task.status != 'done' or task.active_dispatch_id:
            return False
    if any(op.get('task_id') in completed and op.get('status') in {'requested', 'reserved', 'running', 'suspended'}
           for op in reduce_workflow_operations(events).values()):
        return False
    try:
        if _git_commit(runtime.project_root, body['candidate_ref']) != body['candidate_head_commit']:
            return False
        authority = prepare_candidate_verification_authority(runtime, payload={}, workflow_run_id=run,
            task_id=str(loaded.pdd_id), source_event_id=approval.id)
    except (ValueError, OSError, KeyError):
        return False
    if not authority or authority.get('target_commit') != body['candidate_head_commit']:
        return False
    reason = 'approved_plan_tasks_already_integrated'
    if not any(e.type == 'fanout.retrigger.suppressed' and e.payload.get('reason') == reason
               and e.payload.get('trigger_event_id') == trigger_event.id
               and e.payload.get('approval_event_id') == approval.id for e in events):
        runtime.event_writer.append(ZfEvent(type='fanout.retrigger.suppressed', origin='kernel', actor='zf-cli',
            causation_id=approval.id, correlation_id=run,
            payload={'reason': reason, 'stage_id': stage_id, 'trigger_event_id': trigger_event.id,
                'approval_event_id': approval.id, 'workflow_run_id': run,
                'task_map_generation': loaded.task_map_generation,
                'plan_artifact_package_digest': loaded.plan_artifact_package_digest,
                'task_ids': sorted(tasks), 'candidate_event_id': candidate.id,
                'candidate_head_commit': body['candidate_head_commit'],
                'contract_snapshot_ref': authority['contract_snapshot_ref'],
                'contract_snapshot_digest': authority['contract_snapshot_digest']}))
    return True
