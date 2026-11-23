"""One explicitly authorized independent observation after an unknown probe."""
import json
import subprocess

from zf.runtime.task_host_manifest import digest


def recollection_manifest(service, manifest: dict, events: list, policy) -> dict | None:
    """Never infer repeat safety from command text; require owner's exact list."""
    if (not policy.enabled or manifest.get('kind') != 'verify' or manifest.get('recollection_of_start_id')
            or manifest['command']['command'] not in policy.recollect_commands):
        return None
    starts = [e for e in events if e.origin == 'kernel' and e.type == 'runtime.action.attempt.started'
              and e.payload.get('action') == 'task-host-assist'
              and e.payload.get('manifest_digest') == manifest['manifest_digest']]
    if len(starts) != 1:
        return None
    start = starts[0]
    if (any(start.payload.get(k) != v for k, v in manifest['identity'].items())
            or start.payload.get('operation_id') != manifest['operation_id']
            or start.payload.get('approval_ref') != policy.approval_ref
            or start.payload.get('kind') != 'verify'
            or any(e.origin == 'kernel' and e.type == 'runtime.action.attempt.completed'
                   and e.causation_id == start.id for e in events)):
        return None
    # Each original effect has at most one recollection, including after restart.
    if any(e.origin == 'kernel' and e.type == 'runtime.action.attempt.started'
           and e.payload.get('recollection_of_start_id') == start.id for e in events):
        return None
    run = manifest['identity']['workflow_run_id']
    used = sum(e.origin == 'kernel' and e.type == 'runtime.action.attempt.started'
               and e.payload.get('workflow_run_id') == run
               and e.payload.get('approval_ref') == policy.approval_ref for e in events)
    if used >= policy.max_executions_per_run:
        return None
    name = 'zf-probe-' + manifest['manifest_digest'][:24]
    if not _container_absent(name):
        return None
    body = {k: v for k, v in manifest.items() if k != 'manifest_digest'}
    body.update(recollection_of_start_id=start.id, original_manifest_digest=manifest['manifest_digest'])
    return {**body, 'manifest_digest': digest(body)}


def _container_absent(name: str) -> bool:
    # Read-only Docker inventory; errors and surviving containers preserve unknown.
    try:
        query = subprocess.run(['docker', 'container', 'ls', '--all', '--filter', 'name=^/' + name + '$',
                                '--format', '{{json .Names}}'], capture_output=True, text=True,
                               check=False, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if query.returncode or query.stderr.strip():
        return False
    try:
        names = [json.loads(line) for line in query.stdout.splitlines() if line.strip()]
    except ValueError:
        return False
    return not names
