"""Return completed, preauthorized Task probes to the existing Verify retry gate."""
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

from zf.core.config.host_verification import HostVerificationConfig
from zf.core.events import ZfEvent

from zf.runtime.host_probe_diagnosis import bound_container_probe_receipt
from zf.runtime.provider_turn_liveness import active_codex_turn_from_events
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_host_evidence import IDENTITY
from zf.runtime.task_host_manifest import preview
from zf.runtime.task_host_quiescence import unrelated_task_turn_isolated
from zf.runtime.task_pipeline_verify_retry import request_verify_retry
from zf.runtime.verification_commands import verification_command_required_for_stage

if TYPE_CHECKING:
    from zf.runtime.control_actions import ControlledActionService


def resume_task_after_probes(
    service: "ControlledActionService", operation: dict, contract: dict,
    policy: HostVerificationConfig, events: list[ZfEvent],
) -> bool:
    """An execution fact authorizes independent evaluation, never a product pass.

    Task generation/contract/target/lease currentness and idempotency remain in
    request_verify_retry. The successor's existing execution evidence index
    captures these post-settlement receipts, including nonzero exits.
    """
    if (operation.get('task_pipeline_stage') != 'verify'
            or operation.get('status') != 'settled'
            or operation.get('semantic_verdict') != 'abstained'
            or not all(operation.get(k) for k in IDENTITY)):
        return False
    owner = str(contract.get('verification_owner') or 'task_verify')
    required = {c['command_id']: c for c in contract['verification_commands']
                if verification_command_required_for_stage(
                    c, verification_owner=owner, task_id=operation['task_id'])}
    if not required or any(c['command'] not in policy.commands for c in required.values()):
        return False
    try:
        request = hydrate_sidecar_ref(service.state_dir, operation['request_ref']).payload['request']
        target = request['target_commit']
        contract_digest = request['result_identity']['contract_snapshot_digest']
    except (OSError, ValueError, KeyError, TypeError):
        return False
    completions = Counter(e.causation_id for e in events
                          if e.type == 'runtime.action.attempt.completed')
    prior, receipts, settled = {}, {}, False
    for event in events:
        p = event.payload
        if (event.type == 'workflow.operation.settled' and event.origin == 'kernel'
                and p.get('operation_id') == operation['operation_id']):
            settled = True
        receipt = bound_container_probe_receipt(service.state_dir, event, prior, policy) if settled else None
        prior[event.id] = event
        if receipt is None or any(receipt.get(k) != operation.get(k) for k in IDENTITY):
            continue
        spec = required.get(receipt.get('command_id'))
        if (not spec or receipt.get('target_commit') != target
                or completions[event.causation_id] != 1
                or receipt['manifest'].get('contract_digest') != contract_digest
                or receipt.get('product_verdict') != 'not_evaluated'
                or p['receipt_ref'].get('source_event_id') != event.causation_id
                or any(receipt.get(k) != spec.get(k)
                       for k in ('command_id', 'command', 'command_digest'))):
            continue
        try:
            current = preview(service, operation_id=operation['operation_id'],
                              kind='verify', command_id=receipt['command_id'])
            if current['manifest_digest'] != receipt['manifest_digest']:
                continue
            output = receipt['output_ref']
            if (output.get('kind') != 'host_probe_output'
                    or output.get('source_event_id') != event.causation_id
                    or not isinstance(hydrate_sidecar_ref(service.state_dir, output).payload, str)):
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        receipts[receipt['command_id']] = p['receipt_ref']['ref']
    if not required.keys() <= receipts.keys():
        return False
    turn = active_codex_turn_from_events(events, str(operation.get('role_instance') or ''))
    if turn:
        dispatch = next((e for e in events if e.type == 'task.pipeline.stage.dispatched'
                         and e.payload.get('operation_id') == operation['operation_id']), None)
        if (dispatch is None or not unrelated_task_turn_isolated(service, operation,
                Path(dispatch.payload['workdir']), turn, events)):
            return False
    try:
        result = request_verify_retry(state_dir=service.state_dir,
            event_log=service.writer.event_log, event_writer=service.writer,
            operation_id=operation['operation_id'], apply=True,
            reason='preauthorized container execution facts are available; independent Verify required: '
                   + ', '.join(receipts[key] for key in sorted(receipts)))
    except (OSError, ValueError, KeyError):
        return False  # Currentness can change between the probe and this tick.
    return result.get('status') == 'requested'
