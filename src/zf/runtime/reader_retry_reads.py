"""Regenerate consumer-bound reads without discarding other obligations."""
from zf.runtime.artifact_input_index import load_attempt_source_manifest
from zf.runtime.artifact_read_ledger import canonical_required_reads
from zf.runtime.call_result_envelope import canonical_json_sha256
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations


def explicit_retry_reads(runtime, payload: dict, predecessor: str) -> list[dict]:
    """Only remove exact derived consumer-binding rows from authenticated predecessors.

    A new consumer binding has new bytes. Its required reads must be derived
    from the new manifest. Walk the same Reader lineage to handle already
    persisted pre-fix retries that retained an ancestor's obsolete digest.
    Custom selections and stricter constraints remain explicit and fail closed
    if their pinned source is unavailable in the new attempt.
    """
    operations = reduce_workflow_operations(runtime.event_log.read_all())
    remaining = list(payload['required_reads'])
    task_id = payload.get('task_id') or ''
    visited = set()
    while predecessor:
        if predecessor in visited:
            raise ValueError('Reader retry input lineage is cyclic')
        visited.add(predecessor)
        operation = operations.get(predecessor, {})
        if (operation.get('operation_type') != 'fanout_reader_child'
                or any(operation.get(k) != payload.get(k) for k in ('workflow_run_id', 'role_instance'))
                or (operation.get('task_id') or '') != task_id):
            raise ValueError('Reader retry input lineage scope changed')
        request = hydrate_sidecar_ref(runtime.state_dir, operation['request_ref']).payload['request']
        manifest = load_attempt_source_manifest(runtime.state_dir, operation['active_attempt_id'])
        if canonical_json_sha256(manifest) != request.get('source_manifest_digest'):
            raise ValueError('Reader retry predecessor manifest changed')
        derived = [row for row in canonical_required_reads(
            manifest, output_profile_id=request['output_profile_id'])
            if row['source_id'] == 'candidate-host-execution']
        remaining = [row for row in remaining if row not in derived]
        predecessor = str(operation.get('parent_operation_id') or '')
        parent = operations.get(predecessor, {})
        # The initial Reader is a child of its Workflow invocation. Only
        # earlier Reader attempts own consumer-specific read obligations.
        if (parent.get('operation_type') == 'workflow'
                and not request.get('retry_of_operation_id')
                and parent.get('workflow_run_id') == payload.get('workflow_run_id')
                and (parent.get('task_id') or '') == task_id):
            break
    return remaining
