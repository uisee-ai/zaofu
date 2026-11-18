"""Publish preauthorized candidates via the existing host Git and retry gates.

No interpretation of a worker's error text and no product verdict: the signed
blocked result chooses paths, owner policy limits publication, and the next
worker/independent Reader must assess the resulting exact commit.
"""
import subprocess
import logging

from zf.core.state.locks import locked_path
from zf.runtime.pause_lifecycle import is_dispatch_paused
from zf.runtime.provider_turn_liveness import active_codex_turn_from_events
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.source_repair_apply import _hard_denied
from zf.runtime.task_host_actions import apply
from zf.runtime.task_host_manifest import binding, dependency_digest, digest, git, preview, source_files
from zf.runtime.task_pipeline_operator_retry import request_impl_retry
from zf.runtime.workflow_operation import reduce_workflow_operations


def _escalate(service, events, op, reason):
    if any(e.type == 'human.escalate' and e.payload.get('task_publish_operation') == op['operation_id'] for e in events):
        return
    service.writer.emit('human.escalate', actor='run-manager', task_id=op['task_id'],
        correlation_id=op['workflow_run_id'], payload={'task_publish_operation': op['operation_id'],
        'reason': reason, 'owner_route': 'run_manager', 'blocking_scope': 'task',
        'resume_condition': 'resolve retained candidate publication without replaying an ambiguous Git effect'})


def reconcile_task_publication(service) -> int:
    policy = getattr(service.config.runtime, 'task_publication', None)
    if not policy or not policy.enabled:
        return 0
    approval = policy.approval_ref + '#' + digest(policy.authorization_payload())
    applied = 0
    with locked_path(service.state_dir / 'locks' / 'task-candidate-publication'):
        events = service.writer.event_log.read_all()
        if is_dispatch_paused(service.state_dir, events=events):
            return 0
        for op_id, op in reduce_workflow_operations(events).items():
            if applied >= policy.max_actions_per_tick:
                break
            # Each effect may change the Run budget, pause state or ownership.
            # Never authorize a later candidate using the pre-batch snapshot.
            events = service.writer.event_log.read_all()
            if is_dispatch_paused(service.state_dir, events=events):
                break
            if (op.get('task_pipeline_stage') != 'impl' or op.get('status') != 'settled'
                    or op.get('semantic_verdict') != 'blocked'
                    or active_codex_turn_from_events(events, str(op.get('role_instance') or ''))):
                continue
            try:
                _, _, _, root = binding(service, op_id)
            except (ValueError, KeyError, OSError) as exc:
                logging.getLogger(__name__).warning(
                    'Candidate publication deferred for %s: %s', op_id, exc)
                continue  # A stale operation, changed contract or live successor has no authority.
            if any(e.type == 'human.escalate' and e.payload.get('task_publish_operation') == op_id for e in events):
                continue
            try:
                prior = [e for e in events if e.payload.get('action') == 'task-host-assist'
                         and e.payload.get('operation_id') == op_id and e.payload.get('kind') == 'commit']
                completed = [e for e in prior if e.type == 'runtime.action.attempt.completed']
                if prior:
                    if len(completed) != 1 or completed[0].payload.get('approval_ref') != approval:
                        raise ValueError('candidate publication has an unresolved or differently authorized effect')
                    receipt = hydrate_sidecar_ref(service.state_dir, completed[0].payload['receipt_ref']).payload
                    manifest = receipt['manifest']
                    if (receipt.get('status') != 'completed' or receipt.get('source_commit') != git(root, 'rev-parse', 'HEAD')
                            or source_files(root) != manifest['files'] or git(root, 'status', '--porcelain')
                            or dependency_digest(service, root) != manifest['dependency_digest']):
                        raise ValueError('published candidate changed; do not automatically continue')
                    retry = request_impl_retry(state_dir=service.state_dir,
                        event_log=service.writer.event_log, event_writer=service.writer, operation_id=op_id,
                        reason='Preauthorized candidate publication completed: source_commit=' + receipt['source_commit']
                            + '; receipt=' + completed[0].payload['receipt_ref']['ref']
                            + '. Reassess the exact commit and required self-checks; publication is not an Impl verdict.',
                        config=service.config, apply=True)
                    if retry['status'] == 'requested':
                        applied += 1
                    continue
                if not git(root, 'status', '--porcelain'):
                    result = hydrate_sidecar_ref(service.state_dir, op['admitted_control_result_ref']).payload
                    if result.get('failure_class') == 'host_publication_required':
                        raise ValueError('host publication requested without a current source delta or receipt')
                    # Result paths may describe already committed work. Only a
                    # current source delta needs a new publication effect.
                    continue
                result = hydrate_sidecar_ref(service.state_dir, op['admitted_control_result_ref']).payload
                paths = result.get('changed_files')
                if (result.get('operation_id') != op_id or result.get('verdict') != 'blocked'
                        or not isinstance(paths, list) or not paths or any(not isinstance(p, str) for p in paths)
                        or any(p not in policy.paths or _hard_denied(p) for p in paths)):
                    raise ValueError('signed blocked candidate paths are outside publication authorization')
                if result.get('target_commit') != git(root, 'rev-parse', 'HEAD'):
                    raise ValueError('blocked result does not bind the current pre-publication commit')
                used = sum(e.type == 'runtime.action.attempt.started' and e.payload.get('kind') == 'commit'
                    and e.payload.get('action') == 'task-host-assist'
                    and e.payload.get('workflow_run_id') == op['workflow_run_id'] for e in events)
                if used >= policy.max_publications_per_run:
                    raise ValueError('candidate publication budget exhausted')
                manifest = preview(service, operation_id=op_id, kind='commit', paths=paths)
                apply(service, manifest, approval_ref=approval, reviewed_digest=manifest['manifest_digest'])
                applied += 1
            except BlockingIOError:
                logging.getLogger(__name__).warning(
                    'Candidate publication deferred for %s: dependency environment is being mutated', op_id)
                continue
            except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
                _escalate(service, events, op, str(exc))
    return applied
