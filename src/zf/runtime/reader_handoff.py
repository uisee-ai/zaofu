"""Reuse the handoff body for a controlled same-child Reader successor."""
from __future__ import annotations

from zf.runtime.artifact_input_index import load_attempt_source_manifest
from zf.runtime.call_result_envelope import canonical_json_sha256, write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_handoff import SCHEMA, predecessor_work_record, validate_handoff_body
from zf.runtime.workflow_operation import TERMINAL_OPERATION_STATUSES, reduce_workflow_operations

SOURCE_ID = 'reader-handoff'


def attach_reader_handoff(runtime, payload: dict) -> None:
    """Freeze historical facts as an input, never a Task receipt or authority."""
    predecessor = payload.get('retry_of_operation_id')
    if not predecessor:
        return
    events = runtime.event_log.read_all()
    records = [e for e in events if e.type == 'worker.work_record.published' and e.origin == 'kernel'
               and e.payload.get('operation_id') == predecessor]
    operations = reduce_workflow_operations(events)
    source = operations.get(predecessor, {})
    request = (hydrate_sidecar_ref(runtime.state_dir, source['request_ref']).payload['request']
               if source.get('request_ref') else {})
    manifest = {}
    if not records and request.get('source_manifest_digest'):
        manifest = load_attempt_source_manifest(runtime.state_dir, source['active_attempt_id'])
        if request['source_manifest_digest'] != canonical_json_sha256(manifest):
            raise ValueError('Reader handoff predecessor manifest changed')
    inherited = [s for s in manifest.get('sources', []) if s.get('source_id') == SOURCE_ID]
    if not records and not inherited:
        # Optional historical input must not change preparation/admission of
        # legacy calls that never published a checkpoint.
        payload['input_refs'] = [s for s in payload.get('input_refs', []) if s.get('source_id') != SOURCE_ID]
        return
    if (source.get('operation_type') != 'fanout_reader_child'
            or source.get('status') not in TERMINAL_OPERATION_STATUSES
            or source.get('workflow_run_id') != payload['workflow_run_id']
            or source.get('task_id') != payload.get('task_id', '')
            or source.get('role_instance') != payload.get('role_instance')
            or predecessor != payload.get('parent_operation_id')):
        raise ValueError('Reader handoff predecessor is not settled in the same scope')
    for key in ('fanout_id', 'child_id', 'target_ref', 'target_commit',
                'contract_snapshot_digest', 'target_snapshot_digest'):
        if str(request.get(key) or '') != str(payload.get(key) or ''):
            raise ValueError('Reader handoff predecessor scope changed: ' + key)
    target = {key: str(payload.get(key) or '') for key in ('workflow_run_id', 'task_id', 'operation_id')}
    # Retarget historical facts; never copy a predecessor's read/submit authority.
    payload['input_refs'] = [s for s in payload.get('input_refs', []) if s.get('source_id') != SOURCE_ID]
    current = operations.get(target['operation_id'])
    descriptor = None
    if current:
        manifest = load_attempt_source_manifest(runtime.state_dir, current['active_attempt_id'])
        refs = [s for s in manifest.get('sources', []) if s.get('source_id') == SOURCE_ID]
        if refs:
            descriptor = refs[0]
            validate_handoff_body(hydrate_sidecar_ref(runtime.state_dir, descriptor).payload, target)
    else:
        if not records:
            _, record = predecessor_work_record(runtime.state_dir, manifest, source)
            inherited_body = hydrate_sidecar_ref(runtime.state_dir, inherited[0]).payload
            # One pinned body and one record, regardless of the number of retries.
            # A newer publication (even corrupt) always wins over inherited history.
            if inherited_body.get('work_record') != record:
                raise ValueError('Reader handoff inherited record changed')
            body = {**inherited_body, 'target': target}
            validate_handoff_body(body, target)
            descriptor = write_immutable_json_sidecar(runtime.state_dir, body,
                root='task-handoffs/bodies', kind='task_handoff', schema_version=SCHEMA,
                created_by='kernel', source_event_id=source.get('last_event_id', ''))
        if records:
            event = records[-1]
            ref = event.payload['work_record_ref']
            record = hydrate_sidecar_ref(runtime.state_dir, ref).payload
            binding = record.get('binding', {})
            expected = dict(workflow_run_id=source['workflow_run_id'], task_id=source['task_id'],
                operation_id=predecessor, attempt_id=source['active_attempt_id'], dispatch_id=source['dispatch_id'],
                actor=source['role_instance'], request_hash=source['request_hash'],
                fanout_id=request['fanout_id'], child_id=request['child_id'])
            if (record.get('schema_version') != 'worker-work-record.v1'
                    or record.get('authorizes_execution') is not False or record.get('evidence_refs') != []
                    or event.actor != source['role_instance']
                    or any(binding.get(k) != v or event.payload.get(k) != v for k, v in expected.items())):
                raise ValueError('Reader handoff work record identity mismatch')
            body = {
                'schema_version': SCHEMA, 'target': target,
                'source': {k: source[k] for k in ('operation_id', 'active_attempt_id', 'dispatch_id', 'status')},
                'objective': 'Continue the current Reader evaluation', 'contract_revision': payload.get('contract_revision', ''),
                'remaining_acceptance': [], 'workspace': {'candidate_status': 'preserved_not_accepted'},
                'admitted_results': [], 'work_record': record, 'unknown_effects': [],
                'next_action': 'Recheck current inputs and pending criteria. Historical claims are unverified; submit the original result contract.',
                'references': [ref], 'authorizes_execution': False, 'business_completed': False,
            }
            validate_handoff_body(body, target)
            descriptor = write_immutable_json_sidecar(runtime.state_dir, body,
                root='task-handoffs/bodies', kind='task_handoff', schema_version=SCHEMA,
                created_by='kernel', source_event_id=event.id)
    if descriptor:
        payload['input_refs'].append({**descriptor, 'source_id': SOURCE_ID, 'artifact_id': SOURCE_ID, 'allowed_paths': ['$']})
