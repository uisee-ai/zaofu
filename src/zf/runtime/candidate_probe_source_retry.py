"""Return newly collected host evidence to its source Reader before the barrier."""
from dataclasses import asdict
from typing import TYPE_CHECKING

from zf.core.config.host_verification import HostVerificationConfig
from zf.core.events.model import ZfEvent
from zf.runtime.host_verification_retained import reader_probe_inputs_improved
from zf.runtime.reader_rebuild_admission import reader_rebuild_error, reader_rebuild_key
from zf.runtime.run_manager_router import decide_action_policy
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_host_manifest import digest

if TYPE_CHECKING:
    from zf.runtime.control_actions import ControlledActionService


def resume_probe_source(service: 'ControlledActionService', operation: dict, contract: dict,
                        policy: HostVerificationConfig, events: list[ZfEvent], dispatch: ZfEvent,
                        receipts: dict[str, dict]) -> bool:
    if (contract.get('authority_scope') != 'candidate'
            or operation.get('semantic_verdict') != 'abstained'
            or {c['command_id'] for c in contract['verification_commands']} != receipts.keys()):
        return False
    fanout = dispatch.payload.get('fanout_id')
    child = dispatch.payload.get('child_id')
    members = {}
    for event in events:
        if event.origin != 'kernel' or event.payload.get('fanout_id') != fanout:
            continue
        if event.type in {'fanout.aggregate.completed', 'fanout.timed_out', 'fanout.cancelled'}:
            return False  # A closed aggregate already has its own recovery owner.
        if event.type in {'fanout.child.dispatched', 'fanout.child.failed',
                          'fanout.child.completed', 'fanout.child.cancelled'}:
            members[event.payload.get('child_id')] = event
    failed = members.get(child)
    if (not failed or failed.type != 'fanout.child.failed'
            or failed.payload.get('failure_class') != 'verifier_execution_failure'
            or failed.payload.get('operation_id') != operation['operation_id']
            or not any(e.type == 'fanout.child.dispatched' for e in members.values())):
        return False
    payload = {'fanout_id': fanout, 'fanout_child_id': child,
               'workflow_run_id': operation['workflow_run_id'], 'target_operation_id': operation['operation_id'],
               'active_attempt_id': operation['active_attempt_id'], 'source_event_id': failed.id,
               'checkpoint_id': failed.id}
    key = reader_rebuild_key(events, payload)
    if any(e.origin == 'kernel' and e.type == 'repair.action.requested'
           and e.payload.get('idempotency_key') == key for e in events):
        return False
    if (not decide_action_policy(action='child-rebuild', payload=payload)['executable']
            or reader_rebuild_error(service.state_dir, events, payload)):
        return False
    try:
        request = hydrate_sidecar_ref(service.state_dir, operation['request_ref']).payload['request']
        if request.get('output_profile_id') != 'candidate-verify' or request.get('candidate_host_evidence_ref'):
            return False
        for ref in receipts.values():
            fact = hydrate_sidecar_ref(service.state_dir, ref).payload
            if (any(not operation.get(k) or fact.get(k) != operation[k] for k in (
                    'workflow_run_id', 'operation_id', 'active_attempt_id', 'request_hash'))
                    or not request.get('target_commit') or fact.get('target_commit') != request['target_commit']
                    or not request.get('contract_snapshot_digest')
                    or (fact.get('manifest') or {}).get('contract_digest') != request['contract_snapshot_digest']):
                return False
        if not reader_probe_inputs_improved(service, operation, contract, policy, receipts):
            return False
    except (ValueError, TypeError, KeyError, OSError):
        return False
    handoff = {'target_commit': request['target_commit'], 'policy_digest': digest(asdict(policy)),
               'contract_snapshot_digest': request['contract_snapshot_digest'],
               'command_receipts': {key: ref['sha256'] for key, ref in receipts.items()}}
    # Continue an interrupted request, including a restart before its durable repair effect.
    requested = next((e for e in events if e.type == 'control.action.requested'
                      and e.origin == 'kernel' and e.actor == 'run-manager' and e.causation_id == failed.id
                      and e.payload.get('action') == 'child-rebuild'
                      and all(e.payload.get(k) == v for k, v in payload.items())), None)
    if requested and (requested.payload.get('host_verification') != handoff
                      or requested.payload.get('approval_ref') != policy.approval_ref):
        return False
    if requested and any(e.causation_id == requested.id and e.origin == 'kernel'
                         and e.type in {'runtime.action.failed', 'runtime.action.completed'} for e in events):
        return False
    if requested is None:
        requested = service.writer.append(ZfEvent(type='control.action.requested', actor='run-manager',
            origin='kernel', correlation_id=operation['workflow_run_id'], causation_id=failed.id, payload={
                **payload, 'action': 'child-rebuild', 'approval_ref': policy.approval_ref,
                'host_verification': handoff,
                'reason': 'The original candidate host probes are complete; retry the bound Reader.'}))
    return bool(service.execute(action='child-rebuild', requested_action='child-rebuild',
                                requested=requested, payload=requested.payload).get('ok'))


def handed_off_probe_commands(events: list[ZfEvent], policy: HostVerificationConfig) -> dict[tuple, set[str]]:
    """Keep completed host executions with their cohort after the producer is redispatched."""
    groups = {}
    policy_digest = digest(asdict(policy))
    for event in events:
        p = event.payload
        handoff = p.get('host_verification')
        if (event.type != 'control.action.requested' or event.origin != 'kernel' or event.actor != 'run-manager'
                or p.get('action') != 'child-rebuild' or not isinstance(handoff, dict)
                or not event.causation_id or p.get('source_event_id') != event.causation_id
                or p.get('checkpoint_id') != event.causation_id or handoff.get('policy_digest') != policy_digest):
            continue
        group = (p.get('workflow_run_id'), p.get('fanout_id'), handoff.get('target_commit'),
                 handoff.get('contract_snapshot_digest'))
        receipts = handoff.get('command_receipts')
        if all(group) and isinstance(receipts, dict):
            groups.setdefault(group, set()).update(receipts)
    return groups
