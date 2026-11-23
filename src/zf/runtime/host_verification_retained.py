"""Reuse authenticated effects granted to a Reader, without issuing new effects."""
from zf.runtime.artifact_access import require_artifact_access
from zf.runtime.candidate_host_consumption import BOUND_FIELDS, _request, _source_fact
from zf.runtime.host_evidence_media import materialize_container_media, required_media_paths
from zf.runtime.host_verification_policy import policy_receipt_matches
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_host_evidence import host_execution_facts
from zf.runtime.task_host_manifest import binding, dependency_digest, git, source_files
from zf.runtime.workflow_operation import reduce_workflow_operations


def retained_reader_probes(service, operation, contract, policy, events) -> dict:
    """Consumption authority is necessary; matching head/command alone is not."""
    if operation.get('task_pipeline_stage') or not operation.get('request_ref'):
        return {}
    try:
        request = _request(service.state_dir, operation)
        ref = request.get('candidate_host_evidence_ref')
        if not ref or request.get('output_profile_id') != 'candidate-verify':
            return {}
        body = hydrate_sidecar_ref(service.state_dir, ref).payload
        consumer = body['consumer']
        if (body.get('schema_version') != 'candidate-host-evidence.v1'
                or body.get('authorizes_execution') is not False
                or any(consumer.get(k) != request.get(k) for k in BOUND_FIELDS)
                or consumer.get('operation_id') != operation.get('operation_id')
                or consumer.get('attempt_id') != operation.get('active_attempt_id')
                or consumer.get('role_instance') != request.get('role_instance')):
            return {}
        require_artifact_access(ref.get('access_scope'), role=consumer['role_instance'],
                                actor=consumer['role_instance'], purpose='candidate-verify')
        _, _, current_contract, root = binding(service, operation['operation_id'])
        if current_contract != contract or git(root, 'rev-parse', 'HEAD') != consumer['target_commit']:
            return {}
        files, dependencies = source_files(root), dependency_digest(service, root)
        specs = {c['command_id']: c for c in contract['verification_commands']}
        operations = reduce_workflow_operations(events)
        facts_by_source, receipts = {}, {}
        for row in body['receipts']:
            source_id = row['source_operation_id']
            source = operations.get(source_id)
            if not source:
                continue
            if source_id not in facts_by_source:
                facts_by_source[source_id] = host_execution_facts(service.state_dir, source, events)
            matches = [f for f in facts_by_source[source_id]
                       if f['event_id'] == row['receipt_id'] and f['receipt_ref'] == row['receipt_ref']]
            if len(matches) != 1:
                continue
            fact = matches[0]
            resolved = _source_fact(service.state_dir, source, _request(service.state_dir, source),
                                    fact, consumer, specs)
            if not resolved or fact['attempt_id'] != row['source_attempt_id']:
                continue
            receipt, _ = resolved
            retained = hydrate_sidecar_ref(service.state_dir, row['fact_ref']).payload
            if (any(retained.get(k) != v for k, v in fact.items())
                    or fact['status'] != 'available'
                    or receipt.get('execution_origin') != 'preauthorized_container'
                    or receipt.get('approval_ref') != policy.approval_ref
                    or receipt['manifest'].get('files') != files
                    or receipt['manifest'].get('dependency_digest') != dependencies
                    or not policy_receipt_matches(service.state_dir, policy, receipt)):
                continue
            # A media projection failure cannot undo the completed command.
            receipts[fact['command_id']] = fact['receipt_ref']
        return receipts
    except (ValueError, OSError, KeyError, TypeError):
        return {}  # An incomplete binding grants no inherited coverage.


def reader_probe_inputs_improved(service, operation, contract, policy, receipts) -> bool:
    """Only new effects or a repaired previously unavailable input permit retry."""
    request = _request(service.state_dir, operation) if operation.get('request_ref') else {}
    ref = request.get('candidate_host_evidence_ref')
    old = {}
    if ref:
        body = hydrate_sidecar_ref(service.state_dir, ref).payload
        old = {row['command_id']: row for row in body['receipts']}
    # First-time evidence must meet the same media contract as a successor's.
    # A new receipt alone cannot repair a missing or invalid declared output.
    improved = False
    for command in contract['verification_commands']:
        receipt_ref = receipts.get(command['command_id'])
        if not receipt_ref:
            continue
        row = old.get(command['command_id'])
        receipt = hydrate_sidecar_ref(service.state_dir, receipt_ref).payload
        indices = materialize_container_media(service.state_dir, [receipt_ref],
                                              image=policy.image, policy=policy)
        produced = {file['path'] for ref in indices
                    for file in hydrate_sidecar_ref(service.state_dir, ref).payload['files']}
        if receipt.get('exit_code') == 0 and not required_media_paths(command) <= produced:
            return False
        improved |= (not row or row['receipt_ref'] != receipt_ref or row['status'] != 'available')
    return improved
