"""Retain exact post-settlement host facts for a new immutable task handoff."""
from pathlib import Path
from collections import Counter

from zf.core.security.redaction import redact_obj
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_host_manifest import digest


IDENTITY = ('workflow_run_id', 'task_id', 'operation_id', 'active_attempt_id',
            'lease_id', 'dispatch_id', 'request_hash', 'task_map_generation', 'operation_generation')


def host_execution_facts(state_dir: Path, source: dict, events: list) -> list[dict]:
    """Read authenticated completed effects without writing or granting execution.

    Host assistance requires a settled operation. Its receipts therefore occur
    after the native-tool cutoff, but before the successor handoff is frozen.
    Reading these historical facts grants no new command or execution authority.
    """
    expected = {key: source.get(key) for key in IDENTITY}
    if (any(value in (None, '') for key, value in expected.items() if key != 'operation_generation')
            or type(expected['operation_generation']) is not int or expected['operation_generation'] < 0):
        return []
    settled = next((i for i, e in enumerate(events)
        if e.type == 'workflow.operation.settled' and e.origin == 'kernel'
        and e.payload.get('operation_id') == source['operation_id']), None)
    if settled is None:
        return []
    starts = {e.id: e for e in events[settled + 1:]
              if e.type == 'runtime.action.attempt.started' and e.origin == 'kernel'}
    completed = [e for e in events[settled + 1:]
                 if e.type == 'runtime.action.attempt.completed']
    positions = {e.id: i for i, e in enumerate(events)}
    completion_counts = Counter(e.causation_id for e in completed)
    entries = []
    for event in completed:
        p = event.payload
        start = starts.get(event.causation_id)
        if (not start or event.origin != 'kernel' or event.actor != start.actor
                or event.task_id != expected['task_id'] or start.task_id != expected['task_id']
                or event.correlation_id != expected['workflow_run_id']
                or start.correlation_id != expected['workflow_run_id']
                or positions[start.id] >= positions[event.id]
                or completion_counts[start.id] != 1
                or any(p.get(k) != v or start.payload.get(k) != v for k, v in expected.items())
                or p.get('action') != 'task-host-assist' or p.get('kind') != 'verify'
                or not p.get('approval_ref') or p.get('status') not in {'completed', 'failed'}
                or any(start.payload.get(k) != p.get(k)
                       for k in ('action', 'kind', 'approval_ref', 'manifest_digest'))):
            continue
        try:
            descriptor = p['receipt_ref']
            if descriptor.get('kind') != 'task_host_receipt' or descriptor.get('source_event_id') != start.id:
                continue
            receipt = hydrate_sidecar_ref(state_dir, descriptor).payload
            manifest = receipt['manifest']
            reviewed = digest({k: v for k, v in manifest.items() if k != 'manifest_digest'})
            command = manifest['command']
            if (receipt.get('schema_version') != 'task-host-receipt.v1'
                    or receipt.get('kind') != 'verify' or receipt.get('action') != 'task-host-assist'
                    or receipt.get('product_verdict') != 'not_evaluated'
                    or receipt.get('status') != p['status'] or type(receipt.get('exit_code')) is not int
                    or receipt.get('source_stable') is not True
                    or receipt.get('approval_ref') != p['approval_ref']
                    or receipt.get('execution_origin') not in {'preauthorized_container', 'operator_authorized_host'}
                    or manifest.get('schema_version') != 'task-host-manifest.v1'
                    or manifest.get('kind') != 'verify' or manifest.get('operation_id') != source['operation_id']
                    or not receipt.get('target_commit') or receipt['target_commit'] != manifest.get('head')
                    or any(receipt.get(k) != v for k, v in expected.items())
                    or any(manifest.get('identity', {}).get(k) != v
                           for k, v in expected.items() if k != 'operation_id')
                    or any(value != reviewed for value in (
                        p.get('manifest_digest'), receipt.get('manifest_digest'), manifest.get('manifest_digest')))
                    or any(not command.get(k) or command[k] != receipt.get(k)
                           for k in ('command_id', 'command', 'command_digest'))):
                continue
        except (KeyError, TypeError, AttributeError, ValueError, OSError):
            continue
        body = {'event_id': event.id, 'event_type': event.type, 'observed_at': event.ts,
                'binding': {k: source[k] for k in ('workflow_run_id', 'task_id', 'operation_id')},
                'attempt_id': source['active_attempt_id'], 'command': command['command'],
                'command_id': command['command_id'], 'command_digest': command['command_digest'],
                'target_commit': receipt['target_commit'], 'exit_code': receipt['exit_code'],
                'execution_origin': receipt['execution_origin'], 'receipt_ref': descriptor,
                'claims_status': 'host_execution_fact_not_product_verdict', 'authorizes_execution': False,
                'status': 'available', 'output': None}
        try:
            output = receipt['output_ref']
            if output.get('kind') != 'host_probe_output' or output.get('source_event_id') != start.id:
                raise ValueError('host output provenance mismatch')
            body['output'] = hydrate_sidecar_ref(state_dir, output).payload
            if not isinstance(body['output'], str):
                raise ValueError('host output is not text')
            body['source_ref'] = output
        except (KeyError, TypeError, AttributeError, ValueError, OSError):
            body.update(status='unavailable', output=None, reason='host_output_missing_or_invalid')
        entries.append(redact_obj(body))
    return entries


def capture_host_evidence(state_dir: Path, source: dict, events: list) -> list[dict]:
    """Freeze the shared authenticated facts for the existing Task handoff."""
    entries = []
    for body in host_execution_facts(state_dir, source, events):
        ref = write_immutable_json_sidecar(state_dir, body, root='task-handoffs/tool-output',
            kind='task_tool_observation', schema_version='task-tool-observation.v1',
            created_by='kernel', source_event_id=body['event_id'])
        entries.append({'event_id': body['event_id'], 'command_preview': body['command'][:160],
                        'command_truncated': len(body['command']) > 160,
                        'status': body['status'], 'ref': ref})
    return entries
