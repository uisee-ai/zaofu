"""Reviewed compensation for a rejected Plan replay that reopened done Tasks.

This action restores certified completion with a forward authority revision.
It neither grants a fresh verdict nor emits a new business driving event.
"""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.task.authority import authority_revision_for, contract_authority_payload
from zf.core.task.store import TaskStore
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.candidate_continuation import _git_commit, _json, digest

SCHEMA = 'terminal-completion-restore.v1'
SOURCE = 'terminal_completion_restore'
BINDING_KEYS = ('workflow_run_id', 'candidate_head_commit', 'candidate_ref', 'task_map_generation',
                'plan_artifact_package_ref', 'plan_artifact_package_digest', 'freeze_receipt_ref')


def _view(service: Any, events: list, tasks: dict) -> Any:
    store = TaskStore(service.state_dir / 'kanban.json')
    return SimpleNamespace(state_dir=service.state_dir, project_root=service.project_root,
        config=service.config, event_log=SimpleNamespace(read_all=lambda: list(events)), event_writer=service.writer,
        task_store=SimpleNamespace(get=lambda task_id: tasks.get(task_id) or store.get(task_id)))


def _scope(service: Any, source: ZfEvent, events: list) -> tuple[str, ZfEvent]:
    from zf.runtime.candidate_verification_authority import frozen_candidate_for_payload

    run = str(source.payload.get('workflow_run_id') or '')
    if source.type != 'task_map.ready' or source.origin != 'kernel' or not run:
        raise ValueError('terminal restore requires an exact kernel Plan source')
    lifecycle = [e for e in events if e.type in {
        'run.paused', 'run.resumed', 'run.cancelled', 'run.failed', 'run.completed'}
        and (e.payload.get('workflow_run_id') or e.payload.get('run_id') or e.correlation_id) == run]
    if not lifecycle or lifecycle[-1].type != 'run.paused' or lifecycle[-1].origin != 'kernel':
        raise ValueError('terminal restore requires a paused nonterminal Run')
    approval = next((e for e in reversed(events) if e.type in {'plan.approved', 'plan.rejected'}
        and e.payload.get('plan_id') == source.id and not e.payload.get('semantic_control')), None)
    if (not approval or approval.type != 'plan.approved' or approval.origin != 'kernel'
            or approval.actor != 'operator' or approval.payload.get('auto')):
        raise ValueError('terminal restore requires exact Owner Plan approval')
    if not any(e.type == 'fanout.cancelled' and e.origin == 'kernel' and e.payload.get('trigger_event_id') == source.id
               and e.payload.get('reason') == 'terminal_tasks' for e in events):
        raise ValueError('terminal restore requires the rejected terminal Task replay')
    candidate = frozen_candidate_for_payload(events, run, payload={})
    if candidate is None or candidate.origin != 'kernel':
        raise ValueError('terminal restore requires a frozen native Candidate')
    for key in ['task_map_generation', 'plan_artifact_package_ref', 'plan_artifact_package_digest']:
        if not candidate.payload.get(key) or candidate.payload[key] != source.payload.get(key):
            raise ValueError('terminal restore Plan differs from the current Candidate')
    admitted = next((e for e in reversed(events) if e.type == 'plan.artifact_package.admitted'
                     and e.payload.get('workflow_run_id') == run), None)
    if admitted is None or admitted.payload.get('package_digest') != candidate.payload['plan_artifact_package_digest']:
        raise ValueError('terminal restore Plan package is no longer current')
    if _git_commit(service.project_root, candidate.payload['candidate_ref']) != candidate.payload['candidate_head_commit']:
        raise ValueError('terminal restore Candidate moved')
    from zf.runtime.workflow_operation import reduce_workflow_operations

    completed = set(candidate.payload.get('completed_task_ids') or [])
    store = TaskStore(service.state_dir / 'kanban.json')
    if any((task := store.get(task_id)) is None or task.status != 'done' or task.active_dispatch_id
           for task_id in completed):
        raise ValueError('terminal restore Candidate contains a nonterminal or active Task')
    if any(op.get('task_id') in completed and op.get('status') in {'requested', 'reserved', 'running', 'suspended'}
           for op in reduce_workflow_operations(events).values()):
        raise ValueError('terminal restore Candidate still owns Task execution')
    return run, candidate


def preview(service: Any, source: ZfEvent, mutation_ids: list[str]) -> dict:
    from zf.runtime.candidate_retained_tasks import candidate_child_proofs
    from zf.runtime.task_contract_authority import task_execution_binding
    from zf.runtime.workflow_operation import reduce_workflow_operations

    events = service.writer.event_log.read_all()
    run, candidate = _scope(service, source, events)
    if not mutation_ids or len(mutation_ids) != len(set(mutation_ids)):
        raise ValueError('terminal restore requires unique mutation event IDs')
    store = TaskStore(service.state_dir / 'kanban.json')
    archived = store.list_all_with_archive()
    rows = []
    originals = {}
    for event_id in sorted(mutation_ids):
        event = incident_event(events, source, event_id, run)
        body = _json(service.state_dir, event.payload.get('mutation_ref') or {})
        task_id = event.task_id
        current = store.get(task_id)
        if (task_id in originals or task_id not in candidate.payload.get('completed_task_ids', [])
                or current is None or current.status != 'done' or current.active_dispatch_id
                or body.get('schema_version') != 'task-contract-mutation.v1'
                or body.get('task_id') != task_id
                or body.get('authority_revision') != current.contract_authority_revision
                or body.get('authority_sequence') != current.contract_authority_sequence
                or event.payload.get('contract_authority_revision') != current.contract_authority_revision
                or body.get('execution_binding') != asdict(current.execution_binding)):
            raise ValueError('terminal restore current Task binding differs')
        proposed = store._to_task({**asdict(current), 'contract': body['contract']})
        if contract_authority_payload(proposed.contract) != contract_authority_payload(current.contract):
            raise ValueError('terminal restore current contract changed')
        previous = [task for task in archived if task.id == task_id and task.status == 'done'
                    and task.contract_authority_revision == body['previous_authority_revision']
                    and asdict(task.contract) == body['previous_contract']
                    and asdict(task_execution_binding(task)) == body['previous_execution_binding']]
        if not previous or len({digest(asdict(task)) for task in previous}) != 1:
            raise ValueError('terminal restore original completion archive is missing or ambiguous')
        original = previous[0]
        if not original.completed_at or original.active_dispatch_id:
            raise ValueError('terminal restore original completion is not inactive')
        later = events[events.index(event) + 1:]
        if any(e.task_id == task_id and e.type in {
            'task.contract.revision.applied', 'task.pipeline.stage.dispatched', 'task.done'} for e in later):
            raise ValueError('terminal restore has subsequent Task execution or authority')
        if any(op.get('task_id') == task_id and op.get('status') in {
            'requested', 'reserved', 'running', 'suspended'} for op in reduce_workflow_operations(events).values()):
            raise ValueError('terminal restore Task still owns an operation')
        restored = deepcopy(original)
        restored.execution_binding = task_execution_binding(original)
        restored.contract_authority_sequence = current.contract_authority_sequence + 1
        restored.contract_authority_revision = authority_revision_for(task_id=task_id,
            contract=restored.contract, execution_binding=restored.execution_binding,
            sequence=restored.contract_authority_sequence)
        originals[task_id] = original
        rows.append({'task_id': task_id, 'mutation_event_id': event_id,
            'mutation_ref': event.payload['mutation_ref'], 'original': asdict(original),
            'expected': asdict(current), 'replacement': asdict(restored)})
    # Historical completion is checked with certified old rows; unrelated Tasks
    # retain their actual current identity. No current authority is changed here.
    history = [event for event in events if event.payload.get('source') != SOURCE]
    proofs = candidate_child_proofs(_view(service, history, originals), history, candidate)
    if set(originals) - set(proofs):
        raise ValueError('terminal restore lacks reviewed/native completion evidence')
    from zf.runtime.candidate_verification_authority import prepare_candidate_verification_authority

    authority = prepare_candidate_verification_authority(_view(service, history, originals), payload={},
        workflow_run_id=run, task_id=str(source.payload.get('pdd_id') or source.task_id or run),
        source_event_id=source.id)
    manifest = {'schema_version': SCHEMA, 'workflow_run_id': run, 'source_event_id': source.id,
        'candidate_event_id': candidate.id, 'candidate_head_commit': candidate.payload['candidate_head_commit'],
        'candidate_ref': candidate.payload['candidate_ref'], 'tasks': rows,
        'candidate_binding': {key: candidate.payload.get(key) for key in BINDING_KEYS},
        'reviewed_candidate_authority': authority,
        'completion_proofs': {task_id: proofs[task_id] for task_id in originals}}
    manifest['manifest_digest'] = digest(manifest)
    return manifest


def incident_event(events: list, source: ZfEvent, event_id: str, run: str) -> ZfEvent:
    """Bind the same immutable incident during initial apply and crash replay."""
    event = next((event for event in events if event.id == event_id), None)
    if (event is None or event.type != 'task.contract.revision.applied' or event.origin != 'kernel'
            or event.correlation_id != run or event.payload.get('source') != 'refactor_replan_adoption'
            or event.payload.get('old_status') != 'done' or not event.payload.get('reopened_from_terminal')
            or event.payload.get('new_task_map_ref') != source.payload.get('task_map_ref')):
        raise ValueError('terminal restore mutation is not a completed Task replan')
    index = events.index(event)
    if not any(e.type == 'plan.approved' and e.origin == 'kernel' and e.actor == 'operator'
               and e.payload.get('plan_id') == source.id and not e.payload.get('semantic_control')
               and not e.payload.get('auto') for e in events[:index]):
        raise ValueError('terminal restore mutation lacks its prior Owner approval')
    if not any(e.type == 'fanout.cancelled' and e.origin == 'kernel'
               and e.payload.get('trigger_event_id') == source.id and e.payload.get('reason') == 'terminal_tasks'
               for e in events[index + 1:]):
        raise ValueError('terminal restore mutation lacks its subsequent admission rejection')
    return event


def write_preview(service: Any, source: ZfEvent, payload: dict) -> dict:
    manifest = preview(service, source, payload.get('mutation_event_ids') or [])
    descriptor = write_immutable_json_sidecar(service.state_dir, manifest,
        root='terminal-completion-restore/manifests', kind='terminal_completion_restore_manifest',
        schema_version=SCHEMA, created_by='controlled-terminal-recovery')
    return {'ok': True, 'action': 'stage-retrigger', 'manifest': manifest, 'manifest_ref': descriptor}


def _manifest(state: Path, descriptor: dict) -> dict:
    from zf.runtime.task_contract_authority import task_execution_binding

    manifest = _json(state, descriptor)
    if (manifest.get('schema_version') != SCHEMA or manifest.get('manifest_digest') != digest({
            key: value for key, value in manifest.items() if key != 'manifest_digest'})):
        raise ValueError('terminal restore manifest identity differs')
    store = TaskStore(state / 'kanban.json')
    archived = {digest(asdict(task)) for task in store.list_all_with_archive()}
    rows = manifest.get('tasks') or []
    ids = [row['task_id'] for row in rows]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('terminal restore manifest Task set differs')
    for row in rows:
        original = store._to_task(row['original'])
        expected = store._to_task(row['expected'])
        replacement = deepcopy(original)
        replacement.execution_binding = task_execution_binding(original)
        replacement.contract_authority_sequence = expected.contract_authority_sequence + 1
        replacement.contract_authority_revision = authority_revision_for(task_id=original.id,
            contract=original.contract, execution_binding=replacement.execution_binding,
            sequence=replacement.contract_authority_sequence)
        if (original.id != row['task_id'] or expected.id != row['task_id']
                or original.status != 'done' or expected.status != 'done'
                or original.active_dispatch_id or expected.active_dispatch_id
                or not original.completed_at or row['replacement'] != asdict(replacement)):
            raise ValueError('terminal restore replacement is not the original with normalized binding and forward authority')
        mutation = _json(state, row['mutation_ref'])
        if (digest(row['original']) not in archived or mutation.get('schema_version') != 'task-contract-mutation.v1'
                or mutation.get('task_id') != original.id
                or mutation.get('previous_authority_revision') != original.contract_authority_revision
                or mutation.get('previous_contract') != row['original']['contract']
                or mutation.get('previous_execution_binding') != asdict(task_execution_binding(original))
                or mutation.get('authority_revision') != expected.contract_authority_revision
                or mutation.get('authority_sequence') != expected.contract_authority_sequence
                or mutation.get('execution_binding') != row['expected']['execution_binding']):
            raise ValueError('terminal restore archive or mutation binding differs')
    return manifest


def apply(service: Any, source: ZfEvent, payload: dict, requested: ZfEvent) -> ZfEvent:
    from zf.runtime.candidate_retained_tasks import candidate_child_proofs

    events = service.writer.event_log.read_all()
    descriptor = payload.get('manifest_ref') or {}
    manifest = _manifest(service.state_dir, descriptor)
    intent = next((e for e in events if e.id == requested.id), None)
    if (intent is None or intent.type != 'operator.intent.created' or intent.actor != 'operator'
            or intent.origin != 'external' or intent.payload.get('action') != 'stage-retrigger'
            or intent.payload.get('action_request') != payload
            or payload.get('reviewed_digest') != manifest['manifest_digest']
            or not str(payload.get('reason') or '').strip()):
        raise ValueError('terminal restore requires an exact manifest-bound Owner intent')
    run, candidate = _scope(service, source, events)
    if (manifest['source_event_id'] != source.id or manifest['workflow_run_id'] != run
            or manifest['candidate_binding'] != {key: candidate.payload.get(key) for key in BINDING_KEYS}):
        raise ValueError('terminal restore manifest scope is stale')
    store = TaskStore(service.state_dir / 'kanban.json')
    rows = manifest['tasks']
    for row in rows:
        event = incident_event(events, source, row['mutation_event_id'], run)
        if event.task_id != row['task_id'] or event.payload.get('mutation_ref') != row['mutation_ref']:
            raise ValueError('terminal restore incident event and sidecar differ')
    expected = [store._to_task(row['expected']) for row in rows]
    replacements = [store._to_task(row['replacement']) for row in rows]
    actual = [asdict(store.get(row['task_id'])) for row in rows]
    if actual == [row['expected'] for row in rows]:
        checked = preview(service, source, [row['mutation_event_id'] for row in rows])
        if checked != manifest:
            raise ValueError('terminal restore reviewed inputs changed')
    elif actual != [row['replacement'] for row in rows]:
        raise ValueError('terminal restore requires the entire old or exact restored batch')
    originals = {row['task_id']: store._to_task(row['original']) for row in rows}
    history = [event for event in events if event.payload.get('source') != SOURCE]
    proofs = candidate_child_proofs(_view(service, history, originals), history, candidate)
    if manifest['completion_proofs'] != {task_id: proofs.get(task_id) for task_id in originals}:
        raise ValueError('terminal restore original completion evidence changed')
    from zf.runtime.candidate_verification_authority import prepare_candidate_verification_authority

    authority = prepare_candidate_verification_authority(_view(service, history, originals), payload={},
        workflow_run_id=run, task_id=str(source.payload.get('pdd_id') or source.task_id or run),
        source_event_id=source.id)
    if authority != manifest['reviewed_candidate_authority']:
        raise ValueError('terminal restore Candidate member authority changed')
    receipts = {}
    for row in rows:
        old, new = row['expected'], row['replacement']
        body = {'schema_version': 'task-contract-mutation.v1', 'task_id': row['task_id'],
            'source': SOURCE, 'actor': service.actor,
            'previous_authority_revision': old['contract_authority_revision'],
            'authority_revision': new['contract_authority_revision'],
            'authority_sequence': new['contract_authority_sequence'],
            'previous_contract': old['contract'], 'previous_execution_binding': old['execution_binding'],
            'contract': new['contract'], 'execution_binding': new['execution_binding'],
            'task_updates': {key: value for key, value in new.items() if key not in {
                'contract', 'execution_binding', 'contract_authority_revision', 'contract_authority_sequence'}
                and old.get(key) != value},
            'audit_payload': {'completion_restore_ref': descriptor, 'owner_intent_id': requested.id}}
        mutation = write_immutable_json_sidecar(service.state_dir, body, root='task-contract-mutations',
            kind='task_contract_mutation', schema_version='task-contract-mutation.v1',
            created_by=service.actor, source_event_id=requested.id)
        receipts[row['task_id']] = mutation
        _emit_once(service, events, requested, manifest, row, descriptor, mutation,
                   'task.contract.mutation.prepared', 'prepared')
    if not store.compare_and_restore_terminal_batch(expected=expected, replacements=replacements):
        raise ValueError('terminal restore Task batch CAS rejected')
    for row in rows:
        _emit_once(service, events, requested, manifest, row, descriptor, receipts[row['task_id']],
                   'task.contract.revision.applied', 'applied')
        last = _emit_once(service, events, requested, manifest, row, descriptor, receipts[row['task_id']],
                          'task.status_changed', 'restored')
    return last


def _emit_once(service, events, requested, manifest, row, descriptor, mutation, kind, status):
    existing = next((e for e in events if e.type == kind and e.origin == 'kernel'
        and e.task_id == row['task_id'] and e.payload.get('source') == SOURCE
        and e.payload.get('completion_restore_ref') == descriptor), None)
    if existing:
        return existing
    event = service.writer.append(ZfEvent(type=kind, origin='kernel', actor=service.actor,
        task_id=row['task_id'], correlation_id=manifest['workflow_run_id'], causation_id=requested.id,
        payload={'source': SOURCE, 'status': 'done' if kind == 'task.status_changed' else status,
            'compensation_phase': status, 'from': 'done', 'to': 'done',
            'workflow_run_id': manifest['workflow_run_id'], 'completion_restore_ref': descriptor,
            'owner_intent_id': requested.id, 'mutation_ref': mutation,
            'contract_mutation_ref': mutation['ref'], 'contract_mutation_digest': mutation['sha256'],
            'previous_authority_revision': row['expected']['contract_authority_revision'],
            'contract_authority_revision': row['replacement']['contract_authority_revision'],
            'contract_authority_sequence': row['replacement']['contract_authority_sequence']}))
    events.append(event)
    return event


def execute(service: Any, source: ZfEvent, payload: dict, requested: ZfEvent, requested_action: str) -> dict:
    from zf.core.state.locks import locked_path

    allowed = {'source_event_id', 'restore_completed_tasks', 'dry_run', 'mutation_event_ids',
               'manifest_ref', 'reviewed_digest', 'reason'}
    try:
        if set(payload) - allowed:
            raise ValueError('terminal restore cannot be combined with another retrigger option')
        with locked_path(service.state_dir / 'locks/plan-output-recovery'):
            if payload.get('dry_run'):
                return write_preview(service, source, payload)
            event = apply(service, source, payload, requested)
        return service._recovery_ok(requested, event, 'stage-retrigger', requested_action, {
            'restored_task_ids': [row['task_id'] for row in _manifest(service.state_dir, payload['manifest_ref'])['tasks']],
            'completion_restore_ref': payload['manifest_ref'], 'business_retriggered': False})
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return service._recovery_failed(requested, 'stage-retrigger', requested_action, str(exc), 409)
