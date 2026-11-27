"""Identify still-unproduced, mandatory Candidate outputs after an admitted abstention."""
from dataclasses import asdict, dataclass
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.runtime.artifact_read_ledger_validation import validate_required_reads
from zf.runtime.host_evidence_media import required_media_paths
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, verify_sidecar_ref


@dataclass(frozen=True)
class CandidateOutputRecovery:
    commands: set[str]
    sibling_effects: set[str]
    shared_receipts: dict[str, dict]


def missing_host_output_commands(state: Path, operation: dict, request: dict, contract: dict,
                                 result: dict, events: list[ZfEvent]) -> set[str]:
    """The sealed inputs and approved output contract decide eligibility, never the Reader's prose."""
    if (result.get('execution_status') != 'failed' or result.get('verdict') != 'abstained'
            or request.get('output_profile_id') != 'candidate-verify'
            or contract.get('authority_scope') != 'candidate' or request.get('candidate_host_evidence_ref')):
        return set()
    scope = contract.get('candidate_reader_scope')
    if (not isinstance(scope, dict) or not scope or scope != request.get('candidate_reader_scope')
            or scope.get('role_instance') != operation.get('role_instance')
            or scope.get('target_commit') != request.get('target_commit')
            or scope.get('candidate_event_id') != contract.get('candidate_event_id')):
        return set()
    assigned = set(scope['acceptance_ids'])
    mandatory = {row['acceptance_id']: row['verification_command_ids']
                 for row in contract['acceptance_criteria']
                 if row.get('mandatory') is True and row['acceptance_id'] in assigned
                 and row.get('verification_owner') == 'candidate_verify' and row.get('verification_command_ids')}
    commands = {cmd['command_id'] for cmd in contract['verification_commands']
                if cmd.get('owner') == 'candidate_verify' and cmd.get('producer_paths')
                and all(isinstance(path, str) and path.startswith('ZF_EVIDENCE_DIR/') for path in cmd['producer_paths'])
                and required_media_paths(cmd)
                and any(cmd['command_id'] in mandatory.get(ac, ()) for ac in cmd['acceptance_ids'])}
    if not commands:
        return set()

    admission = next((e for e in events if e.id == operation.get('call_result_admitted_event_id')), None)
    if (not admission or admission.type != 'workflow.call.result.admitted' or admission.origin != 'kernel'
            or admission.payload.get('semantic_verdict') != 'abstained'
            or admission.payload.get('control_result_ref') != operation.get('admitted_control_result_ref')
            or any(not operation.get(k) or admission.payload.get(k) != operation[k]
                   for k in ('workflow_run_id', 'operation_id', 'request_hash'))
            or admission.payload.get('attempt_id') != operation['active_attempt_id']):
        return set()
    dispatch = next((e for e in reversed(events) if e.type == 'fanout.child.dispatched' and e.origin == 'kernel'
                     and e.payload.get('operation_id') == operation['operation_id']), None)
    if not dispatch or not request.get('source_manifest_digest') or (
            dispatch.payload.get('attempt_source_manifest_digest') != request['source_manifest_digest']):
        return set()
    manifest = hydrate_sidecar_ref(state, {'ref': dispatch.payload['attempt_source_manifest_ref'],
        'sha256': request['source_manifest_digest']}).payload
    policy = hydrate_sidecar_ref(state, request['input_consumption_policy_ref']).payload
    if (any(doc.get('attempt_id') != operation['active_attempt_id']
            or doc.get('workflow_run_id') != operation['workflow_run_id'] for doc in (manifest, policy))
            or manifest.get('task_id') != operation['task_id'] or not manifest.get('sources') or not policy.get('required_reads')
            or policy['required_reads'] != request.get('required_reads')
            or any(not s.get('sha256') or s['source_id'].startswith('candidate-host-') for s in manifest['sources'])
            or validate_required_reads(state, policy=policy, ledger_descriptor=admission.payload['read_ledger_ref'])):
        return set()
    for source in manifest['sources']:
        verify_sidecar_ref(state, source)
    return commands


def sibling_host_outputs(service, operation: dict, request: dict, contract: dict, root: Path,
                         command_ids: set[str], operations: dict, events: list[ZfEvent]) -> CandidateOutputRecovery:
    """Serialize shared output effects and carry only receipts accessible to this exact Reader."""
    from zf.runtime.candidate_execution_equivalence import same_execution_contract
    from zf.runtime.candidate_host_consumption import _request, _source_fact
    from zf.runtime.task_host_evidence import host_execution_facts
    from zf.runtime.task_host_manifest import dependency_digest, digest

    state = service.state_dir
    policy = service.config.runtime.host_verification
    consumer = {**request, 'host_policy_snapshot': asdict(policy)}
    specs = {cmd['command_id']: cmd for cmd in contract['verification_commands'] if cmd['command_id'] in command_ids}
    owned, receipts = set(), {}
    for event in events:
        p = event.payload
        if (event.type != 'runtime.action.attempt.started' or event.origin != 'kernel' or event.actor != 'run-manager'
                or p.get('action') != 'task-host-assist' or p.get('kind') != 'verify'
                or p.get('approval_ref') != policy.approval_ref or p.get('workflow_run_id') != operation['workflow_run_id']
                or p.get('operation_id') == operation['operation_id']):
            continue
        prior = operations.get(p.get('operation_id'), {})
        if prior.get('operation_type') != 'fanout_reader_child' or prior.get('task_id') != operation['task_id']:
            continue
        source = _request(state, prior)
        if (source.get('output_profile_id') != 'candidate-verify' or source.get('fanout_id') != request.get('fanout_id')
                or source.get('target_commit') != request['target_commit']):
            continue
        manifest = hydrate_sidecar_ref(state, p['manifest_ref']).payload
        cmd_id = manifest['command']['command_id']
        if (cmd_id not in specs or manifest.get('manifest_digest') != p.get('manifest_digest')
                or manifest['manifest_digest'] != digest({k: v for k, v in manifest.items() if k != 'manifest_digest'})
                or manifest['operation_id'] != prior['operation_id'] or manifest['head'] != request['target_commit']
                or not same_execution_contract(state, source, request, command_id=cmd_id)):
            continue
        owned.add(cmd_id)  # Unknown, inaccessible and incomplete effects stay with their original owner.
        if 'dependency_digest' not in consumer:
            consumer['dependency_digest'] = dependency_digest(service, root)
        for fact in host_execution_facts(state, prior, events):
            if fact['command_id'] != cmd_id or fact['status'] != 'available':
                continue
            resolved = _source_fact(state, prior, source, fact, consumer, specs)
            if resolved:
                receipt, _ = resolved
                if (receipt.get('execution_origin') == 'preauthorized_container'
                        and receipt.get('approval_ref') == policy.approval_ref and receipt.get('exit_code') == 0
                        and receipt.get('manifest_digest') == manifest['manifest_digest']):
                    receipts[cmd_id] = fact['receipt_ref']
    return CandidateOutputRecovery(command_ids, owned, receipts)
