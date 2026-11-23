"""Locate cited execution observations inside an already authorized handoff chain."""
from __future__ import annotations

from pathlib import Path
import shlex
from typing import Any

from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _event_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == 'evidence_refs' and isinstance(child, list):
                refs.update(item[6:] for item in child
                            if isinstance(item, str) and item.startswith('event:') and item[6:])
            else:
                refs.update(_event_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.update(_event_refs(child))
    return refs


def _pin(ref: dict) -> dict:
    return {'ref': ref['ref'], 'sha256': ref['sha256']}


def cited_execution_evidence(state_dir: Path, body: dict) -> dict:
    """A citation is a lookup lead, never an applicability or acceptance verdict.

    Traverse only immutable predecessor refs already readable by this consumer.
    No global event search, prose classification, new authorization or output replay.
    """
    from zf.runtime.task_handoff import validate_handoff_body
    from zf.runtime.cli_command import zf_cli_cmd

    consumer = body['target']
    target, current = consumer, body
    observations: dict[str, dict] = {}
    citations: dict[str, list[dict]] = {}
    seen: set[str] = set()
    for _ in range(128):
        validate_handoff_body(current, target)
        source = {**target, 'operation_id': current['source']['operation_id']}
        if source['operation_id'] in seen:
            raise ValueError('cyclic cited handoff history')
        seen.add(source['operation_id'])
        if current.get('execution_evidence_ref'):
            index = hydrate_sidecar_ref(state_dir, current['execution_evidence_ref']).payload
            if index.get('binding') != source:
                raise ValueError('cited tool index identity mismatch')
            for row in index['entries']:
                event_id = row['event_id']
                if event_id in observations:
                    raise ValueError('ambiguous cited execution event')
                observations[event_id] = {'source': source, 'row': row}
        for admitted in current['admitted_results']:
            ref = admitted['ref']
            result = hydrate_sidecar_ref(state_dir, ref).payload
            event_ids = _event_refs(result)
            if event_ids and any(result.get(key) != value for key, value in source.items()):
                raise ValueError('cited result identity mismatch')
            for event_id in event_ids:
                citation = {'admission_event_id': admitted['event_id'], 'result_ref': _pin(ref)}
                if citation not in citations.setdefault(event_id, []):
                    citations[event_id].append(citation)
        previous = current.get('previous_handoff_ref')
        if not previous:
            break
        current = hydrate_sidecar_ref(state_dir, previous).payload
        target = source
    else:
        raise ValueError('cited handoff history traversal bound exceeded')

    entries, other_refs = [], []
    command = zf_cli_cmd() + ' artifact handoff-evidence --operation ' + shlex.quote(consumer['operation_id'])
    for event_id, cited_by in sorted(citations.items()):
        indexed = observations.get(event_id)
        if indexed is None:
            other_refs.append({'event_id': event_id, 'cited_by': cited_by,
                               'reason': 'not an indexed execution observation in this pinned chain'})
            continue
        row, source = indexed['row'], indexed['source']
        observation = hydrate_sidecar_ref(state_dir, row['ref']).payload
        if observation.get('binding') != source or observation.get('event_id') != event_id:
            raise ValueError('cited observation identity mismatch')
        entries.append({'event_id': event_id, 'source_operation': source['operation_id'],
            'observed_at': observation.get('observed_at', ''), 'status': row['status'],
            'command_preview': row.get('command_preview', ''),
            'observation_ref': _pin(row['ref']), 'cited_by': cited_by,
            'read_command': command + ' --source-operation ' + shlex.quote(source['operation_id'])
                + ' --event ' + shlex.quote(event_id)})
    return {'schema_version': 'task-handoff-cited-evidence.v1', 'target': consumer,
            'entries': sorted(entries, key=lambda row: (row['observed_at'], row['event_id'])),
            'other_event_refs': other_refs, 'authorizes_execution': False,
            'claims_status': 'cited_historical_observations_not_current_verdict'}


def write_cited_execution_evidence(state_dir: Path, body: dict) -> dict | None:
    index = cited_execution_evidence(state_dir, body)
    if not index['entries'] and not index['other_event_refs']:
        return None
    return write_immutable_json_sidecar(state_dir, index, root='task-handoffs/cited-evidence',
        kind='task_cited_evidence', schema_version=index['schema_version'], created_by='kernel')
