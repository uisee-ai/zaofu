"""Read-only historical completion view authorized by exact compensation receipts."""
from dataclasses import asdict
from types import SimpleNamespace

from zf.core.task.store import TaskStore
from zf.runtime.terminal_completion_restore import BINDING_KEYS, SOURCE, _manifest, incident_event


def compensated_completion_view(runtime, events, candidate):
    store = runtime.task_store
    parser = TaskStore(runtime.state_dir / 'kanban.json')
    originals = {}
    descriptors = {}
    for event in events:
        if (event.type != 'task.status_changed' or event.origin != 'kernel'
                or event.payload.get('source') != SOURCE):
            continue
        descriptor = event.payload.get('completion_restore_ref') or {}
        manifest = _manifest(runtime.state_dir, descriptor)
        if manifest['candidate_binding'] != {key: candidate.payload.get(key) for key in BINDING_KEYS}:
            continue
        row = next((row for row in manifest['tasks'] if row['task_id'] == event.task_id), None)
        current = store.get(event.task_id)
        if row is None or current is None or asdict(current) != row['replacement']:
            raise ValueError('compensated completion current authority changed')
        source = next((e for e in events if e.id == manifest['source_event_id']), None)
        if source is None or source.type != 'task_map.ready' or source.origin != 'kernel':
            raise ValueError('compensated completion Plan source is missing')
        mutation = incident_event(events, source, row['mutation_event_id'], manifest['workflow_run_id'])
        if mutation.task_id != row['task_id'] or mutation.payload.get('mutation_ref') != row['mutation_ref']:
            raise ValueError('compensated completion incident binding differs')
        intent = next((e for e in events if e.id == event.payload.get('owner_intent_id')), None)
        request = (intent.payload.get('action_request') or {}) if intent else {}
        if (intent is None or intent.type != 'operator.intent.created' or intent.actor != 'operator'
                or intent.origin != 'external' or intent.payload.get('action') != 'stage-retrigger'
                or not request.get('restore_completed_tasks') or request.get('manifest_ref') != descriptor
                or request.get('reviewed_digest') != manifest['manifest_digest']
                or not any(e.type == 'task.contract.revision.applied' and e.origin == 'kernel'
                    and e.task_id == event.task_id and e.payload.get('source') == SOURCE
                    and e.payload.get('completion_restore_ref') == descriptor
                    and e.payload.get('contract_authority_revision') == current.contract_authority_revision
                    for e in events)):
            raise ValueError('compensated completion lacks admitted Owner/mutation evidence')
        originals[event.task_id] = parser._to_task(row['original'])
        descriptors[event.task_id] = descriptor
    if not originals:
        return runtime, descriptors
    values = dict(vars(runtime))
    values['task_store'] = SimpleNamespace(get=lambda task_id: originals.get(task_id) or store.get(task_id))
    return SimpleNamespace(**values), descriptors
