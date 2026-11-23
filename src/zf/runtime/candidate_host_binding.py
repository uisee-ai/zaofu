"""Exact frozen-candidate authority for reviewed host probes, never source writes."""
from pathlib import Path

from zf.core.safety import assert_owned_workdir
from zf.runtime.candidate_plan_recovery import candidate_recovery_superseded
from zf.runtime.candidate_verification_authority import _latest_frozen_candidate
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_contract_snapshot import descriptor_from_payload, hydrate_task_contract_snapshot, hydrate_target_snapshot
from zf.runtime.workdirs import WorkdirManager


def _validate_dispatch_lineage(dispatches, op, identity, target):
    if not dispatches or dispatches[0].payload.get('role_instance') != op['role_instance']:
        raise ValueError('candidate reader lacks a bound dispatch workspace')
    first = dispatches[0]
    by_run = {first.payload.get('run_id'): first}
    for dispatched in dispatches[1:]:
        p = dispatched.payload
        previous = by_run.get(p.get('retry_of_run_id'))
        nested = p.get('payload') or {}
        scopes = ('fanout_id', 'child_id', 'stage_id', 'workflow_run_id', 'role_instance', 'target_ref')
        pins = ('contract_snapshot_ref', 'contract_snapshot_digest', 'target_snapshot_ref', 'target_snapshot_digest')
        if (dispatched.origin != 'kernel' or previous is None or dispatched.causation_id != previous.id
                or not p.get('run_id') or p['run_id'] in by_run
                or any(not first.payload.get(k) or p.get(k) != first.payload[k] for k in scopes)
                or nested.get('operation_id') != op['operation_id']
                or nested.get('request_hash') != op['request_hash']
                or nested.get('workflow_run_id') != op['workflow_run_id']
                or nested.get('task_map_generation') != op['task_map_generation']
                or nested.get('target_commit') != target['target_commit']
                or any(not identity.get(k) or nested.get(k) != identity[k] for k in pins)):
            raise ValueError('candidate reader dispatch retry is not bound to its original authority')
        by_run[p['run_id']] = dispatched


def binding(service, op, request, events, operations):
    from zf.runtime.task_host_manifest import git

    if (op.get('status') != 'settled' or not op.get('call_result_admitted_event_id')
            or op.get('semantic_verdict') not in {'blocked', 'failed', 'abstained'}):
        raise ValueError('candidate host probe requires a settled admitted unavailable verification')
    result = hydrate_sidecar_ref(service.state_dir, op.get('admitted_control_result_ref') or {}).payload
    if (result.get('schema_version') != 'verification-result.v1'
            or result.get('verdict') not in {'blocked', 'failed', 'abstained'}
            or any(result.get(k) != op.get(k) for k in ('operation_id', 'task_id', 'workflow_run_id', 'request_hash'))):
        raise ValueError('candidate verification result identity is invalid')
    run = build_run_admission_projection(events).runs.get(op['workflow_run_id'])
    if run is None or run.status != 'running':
        raise ValueError('candidate host probe requires a running admitted Run')
    requests = [e for e in events if e.type == 'workflow.operation.requested'
                and e.payload.get('role_instance') == op['role_instance']]
    if not requests or requests[-1].payload.get('operation_id') != op['operation_id']:
        raise ValueError('candidate reader has a newer operation')
    if any(other.get('operation_id') != op['operation_id']
           and other.get('role_instance') == op['role_instance']
           and other.get('status') in {'requested', 'reserved', 'running', 'suspended'}
           for other in operations.values()):
        raise ValueError('candidate reader is still owned by another operation')
    identity = request['result_identity']
    expected = {k: op[k] for k in ('workflow_run_id', 'task_id', 'task_map_generation')}
    contract = hydrate_task_contract_snapshot(service.state_dir, descriptor_from_payload(identity), expected=expected)
    target = hydrate_target_snapshot(service.state_dir,
        {'ref': identity['target_snapshot_ref'], 'sha256': identity['target_snapshot_digest']},
        expected={**expected, 'contract_snapshot_digest': identity['contract_snapshot_digest']})
    freeze = _latest_frozen_candidate(events, op['workflow_run_id'])
    if (contract.get('authority_scope') != 'candidate' or target.get('authority_scope') != 'candidate'
            or freeze is None or freeze.payload.get('task_map_generation') != op['task_map_generation']
            or freeze.payload.get('candidate_head_commit') != target.get('target_commit')
            or freeze.payload.get('candidate_ref') != target.get('candidate_ref')
            or request.get('target_commit') != target.get('target_commit')
            or result.get('target_commit') != target.get('target_commit')):
        raise ValueError('candidate freeze or target is no longer current')
    if candidate_recovery_superseded(events, freeze, op['workflow_run_id']):
        raise ValueError('candidate recovery target superseded')
    descriptor = freeze.payload.get('freeze_receipt_ref') or {}
    if (descriptor.get('ref') != target.get('freeze_receipt_ref')
            or descriptor.get('sha256') != target.get('freeze_receipt_digest')):
        raise ValueError('candidate freeze receipt differs from dispatched target')
    hydrate_sidecar_ref(service.state_dir, descriptor)
    dispatches = [e for e in events if e.type == 'fanout.child.dispatched'
                  and e.payload.get('operation_id') == op['operation_id']]
    _validate_dispatch_lineage(dispatches, op, identity, target)
    role = next((r for r in service.config.roles if r.instance_id == op['role_instance']), None)
    if role is None:
        raise ValueError('candidate reader role is no longer configured')
    manager = WorkdirManager(state_dir=service.state_dir, project_root=service.project_root, config=service.config)
    plan = manager.plan(role)
    if not plan.enabled or plan.mode != 'worktree' or plan.role_kind != 'reader':
        raise ValueError('candidate host probe requires the configured reader worktree')
    root = Path(plan.project_path)
    assert_owned_workdir(Path(plan.workdir), state_dir=service.state_dir)
    if root.resolve() != root or not root.is_relative_to(service.state_dir.resolve()):
        raise ValueError('candidate worktree path is not owned')
    if (Path(git(root, 'rev-parse', '--show-toplevel')).resolve() != root
            or git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir') != git(
                service.project_root, 'rev-parse', '--path-format=absolute', '--git-common-dir')
            or git(root, 'rev-parse', 'HEAD') != target['target_commit']
            or git(root, 'rev-parse', '--abbrev-ref', 'HEAD') != 'HEAD'):
        raise ValueError('candidate reader must be detached at the exact frozen repository target')
    return op, request, contract, root
