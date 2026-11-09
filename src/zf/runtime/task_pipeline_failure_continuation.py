"""Bind semantic recovery to an admitted Impl's committed partial delivery."""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref
from zf.runtime.worker_execution_envelope import normalize_worker_execution_envelope
from zf.runtime.workflow_operation import reduce_workflow_operations


def task_pipeline_failure_continuation(
    *, state_dir: Path, project_root: Path, events: list[ZfEvent], task_id: str,
    workflow_run_id: str, identity: dict[str, Any], failure_event_ids: set[str],
) -> dict[str, Any]:
    """Recover only a settled result, current dispatch and exact task-branch commit.

    A commit is the recovery target, never a dirty worktree or a completion claim.
    No workspace paths supplied by the worker are opened here.
    """
    operations = reduce_workflow_operations(events)
    for failure in reversed(events):
        p = failure.payload
        if failure.type != 'dev.blocked' or failure.id not in failure_event_ids:
            continue
        if failure.task_id != task_id or p.get('workflow_run_id') != workflow_run_id:
            continue
        if not _matches(p, identity, ('contract_revision', 'task_map_generation')):
            continue
        operation = operations.get(str(p.get('operation_id') or ''), {})
        ref = operation.get('admitted_control_result_ref') or {}
        admission = next((e for e in events if e.id == operation.get('call_result_admitted_event_id')), None)
        if (operation.get('status') != 'settled'
                or operation.get('task_pipeline_stage') != 'impl'
                or operation.get('semantic_verdict') not in {'blocked', 'failed'}
                or ref.get('source_event_id') != failure.id
                or admission is None or admission.origin not in {'', 'kernel'}
                or admission.payload.get('admission_status') != 'admitted'
                or admission.payload.get('source_event_id') != failure.id
                or operation.get('active_attempt_id') != p.get('attempt_id')):
            continue
        keys = ('workflow_run_id', 'task_id', 'operation_id', 'request_hash', 'dispatch_id',
                'lease_id', 'role_instance', 'task_map_generation', 'operation_generation')
        if not _matches(p, operation, keys):
            continue
        if any(other.get('task_id') == task_id and other.get('workflow_run_id') == workflow_run_id
               and other.get('task_pipeline_stage') == 'impl' and other.get('operation_id') != p['operation_id']
               and int(other.get('operation_generation') or 0) >= int(p['operation_generation'])
               for other in operations.values()):
            continue
        try:
            hydrated = hydrate_sidecar_ref(state_dir, ref)
        except (OSError, SidecarRefError):
            continue
        result = hydrated.payload if hydrated.ok else None
        if (not isinstance(result, dict) or result.get('target_commit') != p.get('source_commit')
                or not _matches(result, p, (
                    *keys, 'attempt_id', 'contract_revision', 'source_branch', 'base_commit',
                ))):
            continue
        dispatches = [e for e in events if e.type == 'task.pipeline.stage.dispatched'
                      and e.payload.get('operation_id') == p['operation_id']]
        if not dispatches:
            continue
        dispatch = dispatches[-1]
        d = dispatch.payload
        if (dispatch.origin != 'kernel' or dispatch.task_id != task_id
                or not _matches(d, p, tuple(k for k in keys if k != 'request_hash') + (
                    'attempt_id', 'source_branch', 'base_commit', 'contract_revision',
                    'workspace_generation', 'placement_epoch', 'task_stage_session_binding'))):
            continue
        try:
            envelope = normalize_worker_execution_envelope(d.get('worker_execution_envelope'))
        except (ValueError, TypeError):
            continue
        if (not envelope.get('envelope_digest')
                or not _matches(envelope['identity'], d, tuple(envelope['identity']))
                or envelope['workspace']['branch'] != d['source_branch']
                or envelope['workspace']['base_commit'] != d['base_commit']):
            continue
        head, base, branch = str(p['source_commit']), str(p['base_commit']), str(d['source_branch'])
        if head == base or not all(re.fullmatch(r'[0-9a-f]{40,64}', c) for c in (head, base)):
            continue
        branch_ref = branch if branch.startswith('refs/heads/') else 'refs/heads/' + branch
        if (_git(project_root, 'rev-parse', '--verify', '--end-of-options', branch_ref + '^{commit}') != head
                or _git(project_root, 'merge-base', '--is-ancestor', base, head) is None):
            continue
        return {'handoff_kind': 'partial_task_continuation', 'continuation_commit': head,
                'continuation_ref': branch, 'continuation_source_event_id': failure.id,
                'partial_head_commit': head, 'partial_source_branch': branch}
    return {}


def _matches(left: dict, right: dict, keys: tuple[str, ...]) -> bool:
    return all(left.get(key) not in (None, '') and str(left[key]) == str(right.get(key)) for key in keys)


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                                text=True, check=False, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None
