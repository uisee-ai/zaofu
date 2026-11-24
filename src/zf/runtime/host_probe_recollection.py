"""One explicitly authorized independent observation after an unknown effect or a bounded capture failure."""
import json
import stat
import subprocess

from zf.runtime.host_probe_diagnosis import bound_container_probe_receipt
from zf.runtime.task_host_manifest import digest


def capture_limit_now_covered(receipt: dict, policy) -> bool:
    """The original authenticated receipt must identify a newly affordable limit."""
    error = receipt.get('collection_error')
    old = receipt.get('policy_snapshot')
    if (receipt.get('status') != 'failed' or receipt.get('evidence_status') != 'collection_failed'
            or receipt.get('source_stable') is not True or type(receipt.get('exit_code')) is not int
            or receipt['exit_code'] != 0 or receipt.get('evidence_ref') is not None
            or not isinstance(error, dict) or not isinstance(old, dict)
            or error.get('stage') != 'collect_container_evidence'
            or not isinstance(error.get('path'), str) or not error['path']
            or error.get('file_mode') != stat.S_IFREG
            or error.get('per_file_limit_bytes') != old.get('max_artifact_bytes')
            or error.get('total_limit_bytes') != old.get('max_total_evidence_bytes')):
        return False
    size, total = error.get('size_bytes'), error.get('total_bytes')
    if type(size) is not int or not 0 <= size <= policy.max_artifact_bytes:
        return False
    if total is not None and (type(total) is not int or not size <= total <= policy.max_total_evidence_bytes):
        return False
    if error.get('kind') == 'per_file_limit':
        return type(old.get('max_artifact_bytes')) is int and size > old['max_artifact_bytes']
    return (error.get('kind') == 'total_limit' and total is not None
            and type(old.get('max_total_evidence_bytes')) is int and total > old['max_total_evidence_bytes'])


def replaced_capture_refs(state_dir, operation_id, events, policy) -> set[str]:
    """Only discharge the precise evidence failure after its independent success."""
    successors = [e for e in events if e.origin == 'kernel' and e.type == 'runtime.action.attempt.completed'
                  and e.payload.get('operation_id') == operation_id and e.payload.get('recollection_of_receipt_id')]
    if not successors:
        return set()
    indexed = {e.id: e for e in events}
    replaced = set()
    for event in successors:
        original = indexed.get(event.payload['recollection_of_receipt_id'])
        if (not original or original.causation_id != event.payload.get('recollection_of_start_id')
                or original.payload.get('operation_id') != operation_id):
            continue
        previous = bound_container_probe_receipt(state_dir, original, indexed, policy)
        current = bound_container_probe_receipt(state_dir, event, indexed, policy)
        if not previous or not current or not capture_limit_now_covered(previous, policy):
            continue
        manifest = previous['manifest']
        expected = {**manifest, 'original_manifest_digest': manifest['manifest_digest'],
                    'recollection_of_start_id': original.causation_id, 'recollection_of_receipt_id': original.id}
        del expected['manifest_digest']
        if (current['manifest'] == {**expected, 'manifest_digest': digest(expected)}
                and current['status'] == 'completed' and current['exit_code'] == 0
                and current.get('evidence_status') == 'collected' and current.get('evidence_ref')
                and current['command'] in (current.get('policy_snapshot') or {}).get('recollect_commands', [])):
            replaced.add(original.payload['receipt_ref']['sha256'])
    return replaced


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
            or start.payload.get('kind') != 'verify'):
        return None
    completed = [e for e in events if e.origin == 'kernel' and e.type == 'runtime.action.attempt.completed'
                 and e.causation_id == start.id]
    if completed:
        if len(completed) != 1 or manifest['command'].get('owner') != 'candidate_verify':
            return None
        receipt = bound_container_probe_receipt(service.state_dir, completed[0], {start.id: start}, policy)
        if not receipt or receipt['manifest'] != manifest or not capture_limit_now_covered(receipt, policy):
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
    if completed:
        body['recollection_of_receipt_id'] = completed[0].id
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
