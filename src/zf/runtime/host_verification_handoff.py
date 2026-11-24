"""Hand container evidence back to the existing independent Reader recovery gate."""
from dataclasses import asdict
from zf.core.events.model import ZfEvent
from zf.runtime.task_host_manifest import digest
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.stage_failure_replan import reader_stage_for_failure


def operator_candidate_receipts(service, operation: dict, contract: dict, events) -> dict:
    """Carry existing reviewed host facts; never extend automatic execution policy."""
    if contract.get('authority_scope') != 'candidate':
        return {}
    try:
        body = hydrate_sidecar_ref(service.state_dir, operation['request_ref']).payload
    except (ValueError, KeyError, OSError):
        return {}
    if any(body.get(k) != operation.get(k) for k in ('workflow_run_id', 'operation_id')):
        return {}
    target = body.get('request', {}).get('target_commit')
    contract_digest = body.get('request', {}).get('result_identity', {}).get('contract_snapshot_digest')
    if not target or not contract_digest:
        return {}
    specs = {c['command_id']: c for c in contract['verification_commands']}
    identity = {k: operation.get(k) for k in ('workflow_run_id', 'task_id', 'task_map_generation')}
    if not all(identity.values()):
        return {}
    receipts = {}
    for event in reversed(events):
        p = event.payload
        trusted_effect = event.origin == 'kernel' or (not event.origin and event.actor == 'web')
        if (event.type != 'runtime.action.attempt.completed' or not trusted_effect
                or p.get('action') != 'task-host-assist' or p.get('kind') != 'verify'
                or any(p.get(k) != v for k, v in identity.items())):
            continue
        try:
            receipt = hydrate_sidecar_ref(service.state_dir, p['receipt_ref']).payload
        except (ValueError, KeyError, OSError):
            continue
        manifest = receipt.get('manifest') or {}
        spec = specs.get(receipt.get('command_id'))
        if (not spec or receipt.get('schema_version') != 'task-host-receipt.v1'
                or receipt.get('execution_origin') != 'operator_authorized_host'
                or not receipt.get('approval_ref') or receipt['approval_ref'] != p.get('approval_ref')
                or receipt.get('kind') != 'verify' or receipt.get('status') not in {'completed', 'failed'}
                or type(receipt.get('exit_code')) is not int or receipt.get('source_stable') is not True
                or any(receipt.get(k) != v or manifest.get('identity', {}).get(k) != v
                       for k, v in identity.items())
                or receipt.get('target_commit') != target or manifest.get('head') != target
                or manifest.get('schema_version') != 'task-host-manifest.v1' or manifest.get('kind') != 'verify'
                or manifest.get('contract_digest') != contract_digest
                or not receipt.get('operation_id') or receipt['operation_id'] != p.get('operation_id')
                or receipt['operation_id'] != manifest.get('operation_id')
                or any(receipt.get(k) != spec.get(k) or manifest.get('command', {}).get(k) != spec.get(k)
                       for k in ('command_id', 'command', 'command_digest'))):
            continue
        reviewed = digest({k: v for k, v in manifest.items() if k != 'manifest_digest'})
        if any(value != reviewed for value in (
                receipt.get('manifest_digest'), manifest.get('manifest_digest'), p.get('manifest_digest'))):
            continue
        receipts.setdefault(receipt['command_id'], p['receipt_ref'])
    return receipts


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
    from zf.runtime.host_verification_retained import retained_reader_probes
    receipts = retained_reader_probes(service, operation, contract, policy, events)
    from zf.runtime.host_probe_recollection import replaced_capture_refs
    replaced = replaced_capture_refs(service.state_dir, operation['operation_id'], events, policy)
    retained = [hydrate_sidecar_ref(service.state_dir, ref).payload for ref in receipts.values()
                if ref.get('sha256') not in replaced]
    failed_probe = any(row.get('exit_code') != 0 or row.get('status') == 'failed' for row in retained)
    for event in events:
        p = event.payload
        if (event.type != 'runtime.action.attempt.completed' or p.get('operation_id') != operation['operation_id']
                or p.get('approval_ref') != policy.approval_ref or p.get('kind') != 'verify'
                or (p.get('receipt_ref') or {}).get('sha256') in replaced):
            continue
        receipt = hydrate_sidecar_ref(service.state_dir, p['receipt_ref']).payload
        from zf.runtime.host_verification_policy import policy_receipt_matches
        if (policy_receipt_matches(service.state_dir, policy, receipt)
                and receipt.get('execution_origin') == 'preauthorized_container'
                and receipt.get('command_id') in required
                and receipt.get('status') in {'completed', 'failed'}
                and type(receipt.get('exit_code')) is int and receipt.get('source_stable') is True):
            receipts[receipt['command_id']] = p['receipt_ref']
            failed_probe = failed_probe or receipt['exit_code'] != 0 or receipt['status'] == 'failed'
    return required, receipts, failed_probe


def _handoff_request(service, events, payload: dict) -> ZfEvent | None:
    """Resume the same intent, or replace one supersession denial after its gate clears."""
    from zf.runtime.reader_stage_recovery import SUPERSEDED_FANOUT_REASON, repaired_reader_stage_trigger

    action = 'stage-replan-new-generation'
    body = {'action': action, **payload}
    previous = [e for e in events if e.type == 'control.action.requested'
                and e.payload.get('repaired_failure_event_id') == payload['repaired_failure_event_id']]
    if not previous:
        return service.writer.emit('control.action.requested', actor='run-manager', payload=body)
    request = previous[-1]
    if (len(previous) > 2 or len(previous) == 2 and request.causation_id != previous[0].id
            or any(e.actor != 'run-manager' or e.origin != 'kernel' or e.payload != body for e in previous)):
        return None
    starts = [e for e in events if e.type == 'runtime.action.attempt.started' and e.origin == 'kernel'
              and e.causation_id == request.id and e.payload.get('action') == action]
    if not starts:
        return request  # Restart after request publication, before the controlled attempt.
    if len(previous) != 1 or len(starts) != 1:
        return None
    start, = starts
    outcomes = [e for e in events if e.origin == 'kernel' and e.causation_id == start.id
                and e.type in {'runtime.action.attempt.completed', 'runtime.action.attempt.failed'}]
    if len(outcomes) != 1:
        return None
    outcome, = outcomes
    denial = next((e for e in events if e.id == outcome.payload.get('result_event_id')), None)
    if (outcome.type != 'runtime.action.attempt.failed' or outcome.payload.get('status') != 'failed'
            or outcome.payload.get('action') != action or not start.payload.get('attempt_id')
            or outcome.payload.get('attempt_id') != start.payload.get('attempt_id')
            or not denial or denial.origin != 'kernel' or denial.type != 'runtime.action.failed'
            or denial.causation_id != request.id or denial.payload.get('action') != action
            or denial.payload.get('reason') != SUPERSEDED_FANOUT_REASON):
        return None
    try:
        repaired_reader_stage_trigger(service.state_dir, service.config, events, payload, request)
    except ValueError:
        return None
    return service.writer.emit('control.action.requested', actor='run-manager',
                               causation_id=request.id, payload=body)


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
        stage, _ = reader_stage_for_failure(service.config, events, failure)
        if stage is None:
            continue
        if any(e.type == stage.trigger and e.payload.get('repaired_failure_event_id') == failure.id
               and (e.origin == 'kernel' or not e.origin and e.actor == 'zf-cli') for e in events):
            return False
        from zf.runtime.reader_failure_repair import repaired_failure_kind
        if repaired_failure_kind(service.state_dir, events, failure) != 'infra':
            # Successful commands do not resolve mixed or semantic findings.
            # Their existing semantic owner retains the aggregate and receipts.
            return False
        supplemental = operator_candidate_receipts(service, operation, contract, events)
        receipts = {**supplemental, **receipts}
        unverified = {c['command_id'] for c in contract['verification_commands']} - receipts.keys()
        if unverified and not failed_probe:
            # A partial execution allowlist is not the frozen acceptance contract.
            # Hold the checkpoint; never invent permission for remaining commands.
            if not any(e.type == 'human.escalate' and e.origin == 'kernel'
                       and e.payload.get('host_coverage_failure_id') == failure.id
                       and e.payload.get('policy_digest') == digest(asdict(policy)) for e in events):
                service.writer.append(ZfEvent(type='human.escalate', actor='run-manager', origin='kernel',
                    correlation_id=operation['workflow_run_id'], payload={
                        'reason': 'preauthorized_verification_coverage_incomplete',
                        'host_coverage_failure_id': failure.id,
                        'operation_id': operation['operation_id'],
                        'policy_digest': digest(asdict(policy)),
                        'unverified_command_ids': sorted(unverified),
                        'owner_route': 'run_manager', 'blocking_scope': 'verification',
                        'resume_condition': 'bound execution receipts for the complete frozen command contract'}))
            return False
        from zf.runtime.host_verification_retained import reader_probe_inputs_improved
        try:
            improved = reader_probe_inputs_improved(service, operation, contract, policy, receipts)
        except (ValueError, OSError, KeyError, TypeError):
            improved = False  # Keep the failure visible to its diagnosis owner.
        if not improved:
            return False
        from zf.runtime.host_evidence_media import materialize_container_media
        indices = materialize_container_media(service.state_dir, list(receipts.values()), image=policy.image,
                                             policy=policy)
        media = (' Read-only media indices (paths relative to state_dir, source/digests bound): '
                 + ', '.join(str(service.state_dir / ref['ref']) for ref in indices)
                 + '. Open the indexed image files directly with the image viewer; do not extract to scratch '
                 'or /proc. Evaluate only the frozen acceptance obligations: distinguish media retention '
                 'from playback review, and do not infer either from file presence alone.') if indices else ''
        payload = {'repaired_failure_event_id': failure.id, 'approval_ref': policy.approval_ref,
            'reason': 'Preauthorized container execution evidence is available for the original frozen target; independent verification required.',
            'operator_feedback': [
                'Read the following bound container receipts as execution evidence, independently evaluate the original acceptance criteria. '
                'Inspect exit codes and output, including failures. Do not repeat an unavailable sandbox command or treat the receipt as a product verdict. '
                + 'Commands without receipts remain unverified: ' + ', '.join(sorted(unverified)) + '. '
                + ', '.join(str(service.state_dir / r['ref']) for r in receipts.values()) + media]}
        requested = _handoff_request(service, events, payload)
        if requested is None:
            return False
        result = service.execute(action='stage-replan-new-generation', requested_action='stage-replan-new-generation',
                                 requested=requested, payload=payload)
        return bool(result.get('ok'))
    if not failed_probe:
        from zf.runtime.candidate_probe_source_retry import resume_probe_source
        return resume_probe_source(service, operation, contract, policy, events, dispatch, receipts)
    return False
