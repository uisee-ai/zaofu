"""Causally scoped native tool observations, never product acceptance evidence by fiat."""
from __future__ import annotations

from pathlib import Path
import shlex

from zf.core.security.redaction import redact_obj
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


IDENTITY = ('workflow_run_id', 'task_id', 'operation_id')
MAX_HANDOFF_PAGE_CHARS = 8192


def handoff_evidence_command(operation_id: str) -> str:
    from zf.runtime.cli_command import zf_cli_cmd

    return (f"{zf_cli_cmd()} artifact handoff-evidence --operation {shlex.quote(operation_id)} "
            f"--page-size {MAX_HANDOFF_PAGE_CHARS}")


def capture_execution_evidence(state_dir: Path, source: dict, events: list) -> dict:
    """Freeze only command output causally attached to this settled attempt.

    Bodies are separate immutable, redacted sidecars. No transcript access,
    command execution, inferred exit status, or semantic passing decision.
    """
    dispatches = {}
    entries = []
    for event in events:
        if (event.id == source.get('last_event_id') or
                event.origin == 'kernel' and event.type in {
                    'workflow.operation.settled', 'workflow.operation.cancelled', 'workflow.operation.superseded'}
                and event.payload.get('operation_id') == source['operation_id']):
            break
        if event.type == 'task.pipeline.stage.dispatched' and event.origin == 'kernel':
            p = event.payload
            if (all(source.get(k) and p.get(k) == source[k] for k in IDENTITY)
                    and event.task_id == source['task_id']
                    and source.get('active_attempt_id') and p.get('attempt_id') == source['active_attempt_id']
                    and source.get('role_instance') and p.get('role_instance') == source['role_instance']
                    and p.get('workdir')):
                dispatches[event.id] = event
            continue
        dispatch = dispatches.get(event.causation_id)
        if (not dispatch or event.type not in {'codex.hook.post_tool_use', 'claude.hook.post_tool_use'}
                or event.actor != source.get('role_instance')
                or event.correlation_id != source['workflow_run_id']
                or event.payload.get('cwd') != dispatch.payload['workdir']):
            continue
        p = event.payload
        tool_input = p.get('tool_input')
        if not isinstance(tool_input, dict) or not isinstance(tool_input.get('command'), str):
            continue
        body = {'event_id': event.id, 'event_type': event.type, 'observed_at': event.ts,
                'binding': {k: source[k] for k in IDENTITY}, 'attempt_id': source['active_attempt_id'],
                'dispatch_event_id': dispatch.id, 'command': tool_input['command'],
                'claims_status': 'provider_observation_unverified', 'authorizes_execution': False,
                'status': 'available', 'output': p.get('tool_response')}
        raw = (p.get('refs') or {}).get('raw_output')
        if isinstance(raw, dict):
            try:
                if raw.get('kind') != 'agent_session_output':
                    raise ValueError('unexpected tool output kind')
                body['output'] = hydrate_sidecar_ref(state_dir, raw).payload
                body['source_ref'] = raw
            except (ValueError, OSError):
                body.update(status='unavailable', output=None, reason='raw_output_missing_or_invalid')
        elif not isinstance(body['output'], str):
            body.update(status='unavailable', output=None, reason='no_text_output')
        body = redact_obj(body)
        ref = write_immutable_json_sidecar(state_dir, body, root='task-handoffs/tool-output',
            kind='task_tool_observation', schema_version='task-tool-observation.v1',
            created_by='kernel', source_event_id=event.id)
        entries.append({'event_id': event.id, 'command_preview': body['command'][:160],
                        'command_truncated': len(body['command']) > 160,
                        'status': body['status'], 'ref': ref})
    from zf.runtime.task_host_evidence import capture_host_evidence

    host_entries = capture_host_evidence(state_dir, source, events)
    entries.extend(host_entries)
    return write_immutable_json_sidecar(state_dir, {
        'binding': {k: source[k] for k in IDENTITY}, 'entries': entries,
        'host_event_ids': [row['event_id'] for row in host_entries],
        'claims_status': ('execution_observations_not_product_verdict' if host_entries
                          else 'provider_observation_unverified'), 'authorizes_execution': False,
    }, root='task-handoffs/tool-index', kind='task_tool_index',
        schema_version='task-tool-index.v1', created_by='kernel')


def previous_handoff(state_dir: Path, source: dict, events: list) -> dict | None:
    """Link immutable predecessor history without copying its bodies each turn."""
    from zf.runtime.task_handoff import validate_handoff_body
    request = hydrate_sidecar_ref(state_dir, source['request_ref']).payload['request']
    reads = [r for r in request.get('required_reads', []) if r.get('source_id') == 'task-handoff']
    if not reads:
        return None
    if len(reads) != 1 or not reads[0].get('artifact_sha256'):
        raise ValueError('ambiguous predecessor handoff')
    target = {k: source[k] for k in IDENTITY}
    refs = [e.payload['handoff_ref'] for e in events if e.type == 'task.handoff.prepared'
            and e.origin == 'kernel' and e.task_id == source['task_id']
            and e.payload.get('target') == target
            and e.payload.get('handoff_ref', {}).get('sha256') == reads[0]['artifact_sha256']]
    if not refs or any(ref != refs[0] for ref in refs):
        raise ValueError('missing or ambiguous pinned predecessor handoff')
    validate_handoff_body(hydrate_sidecar_ref(state_dir, refs[0]).payload,
                          target)
    return refs[0]


def read_execution_evidence(state_dir: Path, manifest: dict, binding: dict,
                            source_operation: str = '', event_id: str = '') -> tuple[dict, dict]:
    """Caller has already authenticated the current operation and canonical lease."""
    from zf.runtime.task_handoff import validate_handoff_body
    refs = [s for s in manifest.get('sources', []) if s.get('source_id') == 'task-handoff']
    if len(refs) != 1:
        raise ValueError('current operation has no pinned handoff')
    ref = refs[0]
    target = {k: binding[k] for k in IDENTITY}
    seen = set()
    for _ in range(128):
        if ref['sha256'] in seen:
            raise ValueError('cyclic handoff history')
        seen.add(ref['sha256'])
        body = hydrate_sidecar_ref(state_dir, ref).payload
        validate_handoff_body(body, target)
        source = {**target, 'operation_id': body['source']['operation_id']}
        if not source_operation or source_operation == source['operation_id']:
            descriptor = body.get('execution_evidence_ref')
            if not descriptor:
                raise ValueError('predecessor tool evidence unavailable')
            index = hydrate_sidecar_ref(state_dir, descriptor).payload
            if index.get('binding') != source:
                raise ValueError('tool evidence source identity mismatch')
            if event_id:
                rows = [r for r in index['entries'] if r['event_id'] == event_id]
                if len(rows) != 1:
                    raise ValueError('event is outside pinned predecessor evidence')
                descriptor = rows[0]['ref']
                output = hydrate_sidecar_ref(state_dir, descriptor).payload
                if output.get('binding') != source or output.get('event_id') != event_id:
                    raise ValueError('tool observation identity mismatch')
                return descriptor, output
            previous = None
            if body.get('previous_handoff_ref'):
                prior = hydrate_sidecar_ref(state_dir, body['previous_handoff_ref']).payload
                validate_handoff_body(prior, source)
                previous = prior['source']['operation_id']
            return descriptor, {**index, 'previous_operation': previous}
        ref = body.get('previous_handoff_ref')
        if not ref:
            raise ValueError('operation is outside pinned handoff history')
        target = source
    raise ValueError('handoff history traversal bound exceeded')
