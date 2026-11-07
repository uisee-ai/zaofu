"""Immutable worker progress evidence, never a second Task state or verdict."""
from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events import EventWriter, ZfEvent
from zf.core.state.locks import locked_path
from zf.core.task.store import TaskStore
from zf.runtime.call_result_envelope import write_immutable_json_sidecar, canonical_json_sha256
from zf.runtime.artifact_input_index import load_attempt_source_manifest
from zf.runtime.artifact_input_lifecycle import input_lifecycle
from zf.runtime.recovery_attempt_facts import recovery_attempt_facts
from zf.runtime.result_submit import SemanticResultSubmitService
from zf.runtime.sidecar_refs import hydrate_sidecar_ref

SCHEMA = 'worker-work-record.v1'


def authorize_work_record(service: SemanticResultSubmitService, operation_id: str, role: str, credential: str):
    operation, request = service.authorize_running_operation(operation_id, role_instance=role, credential=credential)
    task = TaskStore(service.state_dir / 'kanban.json').get(str(operation.get('task_id') or ''))
    if task is None or task.assigned_to != role or task.status != 'in_progress':
        raise ValueError('work record requires the current active Task assignee')
    from zf.runtime.task_run_binding import canonical_task_run
    run_id, _ = canonical_task_run(task, service.event_log.read_all())
    if run_id != operation.get('workflow_run_id'):
        raise ValueError('work record canonical Task Run mismatch')
    facts = recovery_attempt_facts(service.state_dir, task, run_id=run_id)
    identity = facts.get('identity', {})
    if (facts['status'] != 'bound' or identity.get('operation_id') != operation_id or identity.get('instance_id') != role
            or identity.get('status') not in {'prepared','sent','running'} or not identity.get('lease_id')):
        raise ValueError('work record canonical attempt/lease mismatch')
    try:
        expiry = datetime.fromisoformat(str(identity['lease_expires_at']))
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            raise ValueError('expired')
    except (ValueError, KeyError):
        raise ValueError('work record requires an unexpired canonical lease') from None
    manifest = load_attempt_source_manifest(service.state_dir, str(operation.get('active_attempt_id') or ''))
    if (manifest.get('task_id') != task.id or manifest.get('dispatch_id') != task.active_dispatch_id
            or manifest.get('workflow_run_id') != operation.get('workflow_run_id')
            or input_lifecycle(manifest, service.event_log.read_all(), state_dir=service.state_dir)['requires_rebind']):
        raise ValueError('work record input version needs controlled rebind')
    workdir = Path(str(request.get('workdir') or '')).resolve()
    if not request.get('workdir') or not workdir.is_dir():
        raise ValueError('operation has no available pinned workdir')
    binding = {'workflow_run_id': manifest['workflow_run_id'], 'task_id': task.id,
        'operation_id': operation_id, 'attempt_id': manifest['attempt_id'], 'dispatch_id': task.active_dispatch_id,
        'canonical_attempt_id': identity['attempt_id'], 'lease_id': identity['lease_id'],
        'actor': role, 'workdir': str(workdir), 'manifest_digest': canonical_json_sha256(manifest)}
    return binding, manifest, workdir


def _read_scoped(workdir: Path, file: Path, limit: int) -> bytes:
    import os
    import stat
    path = file.absolute()
    try:
        relative = path.relative_to(workdir)
    except ValueError:
        raise ValueError('work record file is outside the pinned workdir') from None
    if '..' in relative.parts or not relative.parts or '.git' in relative.parts or any(part.startswith('.env') for part in relative.parts):
        raise ValueError('work record file path is not allowed')
    directory = os.open(workdir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in relative.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('work record evidence must be a regular file')
            raw = stream.read(limit+1)
    finally:
        os.close(directory)
    if len(raw) > limit:
        raise ValueError('work record payload exceeds byte budget')
    from zf.core.security.redaction import redact_text
    text = raw.decode('utf-8')
    if redact_text(text) != text:
        raise ValueError('work record contains sensitive material')
    return raw


def publish_work_record(*, state_dir: Path, event_log, operation_id: str, role: str, credential: str,
                        record_file: Path, request_id: str) -> dict[str, Any]:
    if not request_id or len(request_id) > 128:
        raise ValueError('work record requires a bounded idempotency request id')
    service = SemanticResultSubmitService(state_dir=state_dir, event_log=event_log, event_writer=EventWriter(event_log))
    with locked_path(state_dir / 'work-records' / hashlib.sha256(operation_id.encode()).hexdigest()):
        binding, manifest, workdir = authorize_work_record(service, operation_id, role, credential)
        body = json.loads(_read_scoped(workdir, record_file, 16384))
        if not isinstance(body, dict) or set(body) != {'completed_actions','pending','next_step','evidence_files'}:
            raise ValueError('work record requires completed_actions, pending, next_step and evidence_files')
        for key in ('completed_actions','pending','evidence_files'):
            if (not isinstance(body[key], list) or len(body[key]) > 16
                    or any(not isinstance(v, str) or not v.strip() or len(v) > 1024 for v in body[key])):
                raise ValueError(f'invalid work record {key}')
        if not isinstance(body['next_step'], str) or not body['next_step'].strip() or len(body['next_step']) > 2048:
            raise ValueError('invalid work record next_step')
        captured = []
        for file in body['evidence_files']:
            relative = Path(file)
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('evidence file must be relative to the workdir')
            raw = _read_scoped(workdir, workdir / file, 65536)
            captured.append((file, raw))
        payload_digest = canonical_json_sha256({'body': body, 'evidence': [
            {'path': file, 'sha256': hashlib.sha256(raw).hexdigest()} for file, raw in captured]})
        previous = [event for event in event_log.read_all() if event.type == 'worker.work_record.published'
                    and event.origin == 'kernel' and event.actor == role
                    and event.payload.get('operation_id') == operation_id and event.payload.get('request_id') == request_id]
        if previous:
            event = previous[-1]
            prior = hydrate_sidecar_ref(state_dir, event.payload['work_record_ref']).payload
            if prior['binding'] != binding or prior['payload_digest'] != payload_digest:
                raise ValueError('work record idempotency conflict')
            for evidence_ref in prior['evidence_refs']:
                hydrate_sidecar_ref(state_dir, evidence_ref)
            return {'event_id': event.id, 'work_record_ref': event.payload['work_record_ref'], 'replayed': True}
        evidence = []
        for file, raw in captured:
            # Preserve evidence bytes exactly in an immutable envelope. No claim
            # that a worker-produced log is an independent Verify result.
            import base64
            ref = write_immutable_json_sidecar(state_dir, {'schema_version':'work-record-evidence.v1',
                'path': file, 'sha256': hashlib.sha256(raw).hexdigest(), 'body_base64': base64.b64encode(raw).decode()},
                root='work-records/evidence', kind='work_record_evidence', schema_version='work-record-evidence.v1', created_by=role)
            evidence.append(ref)
        record = {'schema_version': SCHEMA, 'binding': binding, 'request_id': request_id,
            'payload_digest': payload_digest, 'completed_actions': body['completed_actions'], 'pending': body['pending'],
            'next_step': body['next_step'], 'evidence_refs': evidence,
            'claims_status': 'worker_report_unverified', 'authorizes_execution': False}
        descriptor = write_immutable_json_sidecar(state_dir, record, root='work-records', kind='worker_work_record',
            schema_version=SCHEMA, created_by=role)
        current, _, _ = authorize_work_record(service, operation_id, role, credential)
        if binding != current:
            raise ValueError('work record binding changed before publication')
        event = service.event_writer.append(ZfEvent(type='worker.work_record.published', actor=role,
            task_id=binding['task_id'], origin='kernel', payload={
                **binding, 'request_id': request_id, 'work_record_ref': descriptor, 'payload_digest': payload_digest}))
        return {'event_id': event.id, 'work_record_ref': descriptor, 'replayed': False}


def current_work_record(state_dir: Path, events: list, manifest: dict, actor: str) -> dict:
    unavailable = {'status':'unavailable', 'authorizes_execution':False}
    task = TaskStore(state_dir / 'kanban.json').get(manifest.get('task_id',''))
    facts = recovery_attempt_facts(state_dir, task, run_id=manifest.get('workflow_run_id',''))
    identity = facts.get('identity', {})
    if task is None or task.active_dispatch_id != manifest.get('dispatch_id') or task.assigned_to != actor or facts['status'] != 'bound':
        return unavailable
    from zf.runtime.task_run_binding import canonical_task_run
    from zf.runtime.workflow_operation import reduce_workflow_operations
    operation = reduce_workflow_operations(events).get(identity.get('operation_id'), {})
    if (task.status != 'in_progress' or canonical_task_run(task, events)[0] != manifest.get('workflow_run_id')
            or operation.get('status') != 'running' or operation.get('active_attempt_id') != manifest['attempt_id']
            or operation.get('role_instance') != actor):
        return unavailable
    for event in reversed(events):
        if event.type != 'worker.work_record.published' or event.payload.get('attempt_id') != manifest['attempt_id']:
            continue
        if event.origin != 'kernel' or event.actor != actor or event.payload.get('operation_id') != identity.get('operation_id'):
            continue
        try:
            ref = event.payload['work_record_ref']
            record = hydrate_sidecar_ref(state_dir, ref).payload
            binding = record['binding']
            if (record.get('schema_version') != SCHEMA or binding.get('actor') != actor
                    or binding.get('operation_id') != identity.get('operation_id')
                    or binding.get('attempt_id') != manifest['attempt_id'] or binding.get('manifest_digest') != canonical_json_sha256(manifest)
                    or binding.get('canonical_attempt_id') != identity.get('attempt_id') or binding.get('lease_id') != identity.get('lease_id')
                    or binding.get('dispatch_id') != task.active_dispatch_id or not Path(binding.get('workdir','')).is_dir()
                    or input_lifecycle(manifest, events, state_dir=state_dir)['requires_rebind']):
                return unavailable
            for evidence in record['evidence_refs']:
                hydrate_sidecar_ref(state_dir, evidence)
            # Short navigation in the pack; full semantic record remains pinned.
            return {'status':'bound','authorizes_execution':False,'claims_status':'worker_report_unverified',
                'event_id':event.id,'operation_id':identity.get('operation_id'),'work_record_ref':ref,
                'completed_count':len(record['completed_actions']),'pending_count':len(record['pending']),
                'next_step':record['next_step'][:512], 'next_step_complete':len(record['next_step']) <= 512}
        except (OSError, ValueError, KeyError, TypeError):
            return unavailable  # Never silently resurrect an older good record.
    return unavailable
