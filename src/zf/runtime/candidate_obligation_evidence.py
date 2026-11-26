"""Admit evidence for retained duties without allowing a Reader to grant authority."""
from pathlib import Path

from zf.runtime.artifact_access import require_artifact_access
from zf.runtime.artifact_input_index import load_attempt_source_manifest
from zf.runtime.call_result_envelope import canonical_json_sha256
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations
from zf.runtime.task_host_evidence import host_execution_facts


def validate_obligation_evidence(state_dir: Path, result: dict, contract: dict, events: list | None) -> None:
    obligations = contract.get('candidate_recovery_obligations', [])
    if not obligations or result.get('verdict') != 'passed':
        return
    if events is None:
        raise ValueError('recovery obligation evidence requires the authenticated event ledger')
    operation = reduce_workflow_operations(events).get(result.get('operation_id'), {})
    request = hydrate_sidecar_ref(state_dir, operation.get('request_ref') or {}).payload['request']
    manifest = load_attempt_source_manifest(state_dir, result['attempt_id'])
    if request.get('source_manifest_digest') != canonical_json_sha256(manifest):
        raise ValueError('recovery evidence source manifest changed')
    allowed = {(s['ref'], s['sha256']): s for s in manifest['sources']}
    positions = {e.id: i for i, e in enumerate(events)}
    indexed = {e.id: e for e in events}
    results = {row['finding_id']: row for row in result['recovery_obligation_results']}
    for finding in obligations:
        row = results[finding['finding_id']]
        # The admitted result binds the Agent's rationale to this finding. Its
        # supporting facts must really exist in this consumer's frozen inputs.
        for ref in row['evidence_refs']:
            source = allowed.get((ref['ref'], ref['sha256']))
            if source is None:
                raise ValueError('recovery evidence is outside this consumer source manifest')
            require_artifact_access(source.get('access_scope'), role=result['role_instance'],
                                    actor=result['role_instance'], purpose='candidate-verify')
            hydrate_sidecar_ref(state_dir, source)
        if (finding['disposition']['classification'] == 'contract_question'
                and row.get('resolution_event_id')):
            decision = indexed.get(row.get('resolution_event_id'))
            source = next((e for e in events if e.origin == 'kernel' and e.type == 'workflow.call.result.admitted'
                           and e.payload.get('control_result_ref') == finding.get('control_result_ref')), None)
            if (decision is None or source is None or decision.origin != 'kernel'
                    or decision.type != 'plan.approved' or decision.actor != 'operator'
                    or not _approved_generation(decision, result, contract, events, indexed)
                    or positions[decision.id] <= positions[source.id]):
                raise ValueError('contract question requires a subsequent approved Plan bound to the current generation')
        start_id = finding.get('original_start_event_id')
        if start_id:
            effect = indexed.get(row.get('resolution_event_id'))
            if effect is None:
                raise ValueError('unknown host obligation requires an authenticated observation')
            source = reduce_workflow_operations(events).get(effect.payload.get('operation_id'), {})
            facts = host_execution_facts(state_dir, source, events)
            fact = next((f for f in facts if f['event_id'] == effect.id and f['status'] == 'available'), None)
            direct = effect.causation_id == start_id or effect.payload.get('recollection_of_start_id') == start_id
            independent = False
            if fact and not direct and finding.get('original_manifest_ref'):
                original = hydrate_sidecar_ref(state_dir, finding['original_manifest_ref']).payload
                receipt = hydrate_sidecar_ref(state_dir, fact['receipt_ref']).payload
                independent = (effect.id in result.get('host_execution_receipt_ids', [])
                    and receipt['target_commit'] == result['target_commit']
                    and all(receipt['manifest']['command'].get(k) == original['command'].get(k)
                            for k in ('command_id', 'command', 'command_digest', 'tier', 'producer_paths')))
            if (not fact or effect.payload.get('workflow_run_id') != result['workflow_run_id']
                    or not (direct or independent)):
                raise ValueError('host observation does not resolve the exact original unknown effect')


def _approved_generation(decision, result, contract, events, indexed) -> bool:
    """Link the real approval request/trigger to the admitted consumer generation.

    A Reader may explain the existing contract using frozen inputs without an
    owner event. Claiming an owner change instead requires this authorization;
    neither explanation can mutate the canonical contract.
    """
    request = indexed.get(decision.payload.get('request_event_id'))
    trigger_id = decision.payload.get('plan_id')
    trigger = indexed.get(trigger_id)
    if (not trigger or not request or request.type != 'plan.approval.requested'
            or request.payload.get('plan_id') != trigger_id
            or decision.causation_id != trigger_id):
        return False
    generations = [e for e in events if e.origin == 'kernel'
                   and e.type == 'task.pipeline.generation.admitted'
                   and e.payload.get('workflow_run_id') == result['workflow_run_id']]
    if not generations:
        return False
    current = generations[-1].payload
    return (current.get('trigger_event_id') == trigger_id
        and current.get('task_map_generation') == result['task_map_generation']
        and all(str(current.get(k) or '') == str(contract.get(k) or '') for k in (
            'plan_artifact_package_id', 'plan_artifact_package_ref', 'plan_artifact_package_digest')))
