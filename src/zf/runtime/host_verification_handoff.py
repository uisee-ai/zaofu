"""Hand container evidence back to the existing independent Reader recovery gate."""
from dataclasses import asdict
from zf.runtime.task_host_manifest import digest
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.stage_failure_replan import reader_stage_for_failure


def reader_probe_handoff_complete(operation: dict, events) -> bool:
    """A kernel successor owns remaining probes; do not drain its predecessor."""
    if operation.get('task_pipeline_stage'):
        return False
    run = operation.get('workflow_run_id')
    fanout = ''
    failures = set()
    for event in events:
        p = event.payload
        if event.origin != 'kernel' or not run or p.get('workflow_run_id') != run:
            continue
        if event.type == 'fanout.child.dispatched' and p.get('operation_id') == operation['operation_id']:
            fanout = p.get('fanout_id') or ''
        elif fanout and event.type.endswith('.failed') and p.get('fanout_id') == fanout:
            failures.add(event.id)
        elif event.type == 'candidate.ready' and p.get('repaired_failure_event_id') in failures:
            return True
    return False


def completed_reader_probes(service, operation: dict, contract: dict, policy, events) -> tuple[set, dict, bool]:
    """Collect only current-policy, stable-target Reader execution receipts."""
    if operation.get('task_pipeline_stage'):
        return set(), {}, False  # Task Pipeline retains its own rework state machine.
    required = {c['command_id'] for c in contract['verification_commands']
                if c.get('command') in policy.commands}
    if not required:
        return required, {}, False
    receipts = {}
    failed_probe = False
    for event in events:
        p = event.payload
        if (event.type != 'runtime.action.attempt.completed' or p.get('operation_id') != operation['operation_id']
                or p.get('approval_ref') != policy.approval_ref or p.get('kind') != 'verify'):
            continue
        receipt = hydrate_sidecar_ref(service.state_dir, p['receipt_ref']).payload
        if (receipt.get('policy_digest') == digest(asdict(policy))
                and receipt.get('execution_origin') == 'preauthorized_container'
                and receipt.get('command_id') in required
                and receipt.get('status') in {'completed', 'failed'}
                and type(receipt.get('exit_code')) is int and receipt.get('source_stable') is True):
            receipts[receipt['command_id']] = p['receipt_ref']
            failed_probe = failed_probe or receipt['exit_code'] != 0
    return required, receipts, failed_probe


def resume_reader_after_probes(service, operation: dict, contract: dict, policy, events) -> bool:
    required, receipts, failed_probe = completed_reader_probes(service, operation, contract, policy, events)
    if not required:
        return False
    if not failed_probe and not required <= receipts.keys():
        return False
    dispatch = next((e for e in events if e.type == 'fanout.child.dispatched'
                     and e.payload.get('operation_id') == operation['operation_id']), None)
    if dispatch is None:
        return False
    for failure in reversed(events):
        if (not failure.type.endswith('.failed') or failure.payload.get('fanout_id') != dispatch.payload.get('fanout_id')
                or (failure.payload.get('workflow_run_id') or failure.payload.get('run_id') or failure.correlation_id)
                != operation['workflow_run_id']):
            continue
        if any(e.payload.get('repaired_failure_event_id') == failure.id for e in events):
            return False
        stage, _ = reader_stage_for_failure(service.config, events, failure)
        if stage is None:
            continue
        payload = {'repaired_failure_event_id': failure.id, 'approval_ref': policy.approval_ref,
            'reason': 'Preauthorized container execution evidence is available for the original frozen target; independent verification required.',
            'operator_feedback': [
                'Read the following bound container receipts as execution evidence, independently evaluate the original acceptance criteria. '
                'Inspect exit codes and output, including failures. Do not repeat an unavailable sandbox command or treat the receipt as a product verdict. '
                + 'Commands without receipts remain unverified: ' + ', '.join(sorted(required - receipts.keys())) + '. '
                + ', '.join(str(service.state_dir / r['ref']) for r in receipts.values())]}
        requested = service.writer.emit('control.action.requested', actor='run-manager', payload={
            'action': 'stage-replan-new-generation', **payload})
        result = service.execute(action='stage-replan-new-generation', requested_action='stage-replan-new-generation',
                                 requested=requested, payload=payload)
        return bool(result.get('ok'))
    return False
