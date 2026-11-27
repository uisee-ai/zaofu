"""Validate and carry operator-authored feedback on authorized reader retries."""
from __future__ import annotations

from collections.abc import Mapping
import shlex
from typing import Any

from zf.core.events.model import ZfEvent


def repair_read_navigation(required: list, *, cli_command: str, attempt_id: str) -> list[str]:
    """Expose the pinned recovery source before bulk product input navigation."""
    for row in required:
        if (not isinstance(row, Mapping)
                or row.get('source_id') != 'critical-reader-repair-context'
                or not row.get('artifact_id')):
            continue
        if row is required[0]:
            return [
                '- Recovery input first: before probing the runner or declaring it unavailable, '
                'read the pinned critical-reader-repair-context and evaluate its bound receipts.',
                '  In the mounted workspace, consume the recovery selection first. For CLI delivery, '
                'start the required-read command below once. Its first selection is the recovery context. '
                'Follow its next_cursor through that selection and the remaining required inputs.',
                '  Check the receipt target, command, source stability, exit code and evidence '
                'before assessing the original acceptance obligations.',
            ]
        # Older declarations can pin a different order. Preserve their cursor and first-read guidance.
        command = ' '.join([
            cli_command, 'artifact read --attempt', shlex.quote(attempt_id),
            '--source critical-reader-repair-context --artifact',
            shlex.quote(str(row['artifact_id'])), '--json-path',
            shlex.quote(str(row.get('json_path') or '$')),
            '--page-start 0 --page-size 4096',
        ])
        return [
            '- Recovery input first: before probing the runner or declaring it unavailable, '
            'read the current recovery context, including any bound host execution receipts.',
            f'  `{command}`. Consume every page with the returned `next_page_start` until null.',
            '  Recovery context is not a product verdict or permission to bypass isolation. '
            'Evaluate receipt target, command, source stability, exit code and evidence; '
            'all original required reads and acceptance obligations remain mandatory.',
        ]
    return []


def input_task_ids(payload: Mapping[str, Any]) -> list[str]:
    """Read priority only; never an instruction to narrow dispatch or acceptance."""
    value = payload.get('input_task_ids', [])
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError('input_task_ids must be a list of non-empty strings')
    return sorted({item.strip() for item in value})


def operator_feedback(payload: Mapping[str, Any]) -> list[str]:
    if 'operator_feedback' not in payload:
        return []
    value = payload['operator_feedback']
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError('operator_feedback must be a list of non-empty strings')
    return [item.strip() for item in value]


def bind_operator_feedback(trigger: ZfEvent, feedback: list[str], *, request_id: str) -> None:
    if not feedback:
        trigger.payload.pop('operator_feedback', None)
        return
    trigger.payload['operator_feedback'] = list(feedback)
    trigger.payload['rework_feedback'] = [
        *trigger.payload.get('rework_feedback', []),
        *({'message': text, 'source': 'operator', 'source_event_id': request_id}
          for text in feedback),
    ]


def repair_input_refs(state_dir, payload: Mapping[str, Any], *, workflow_run_id: str,
                      task_id: str, source_event_id: str) -> list[dict[str, Any]]:
    """Pin repair feedback before context compaction removes inline trigger bodies."""
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar

    trigger = payload.get('trigger_payload')
    trigger = trigger if isinstance(trigger, Mapping) else {}
    fields = {key: payload.get(key) or trigger.get(key) for key in (
        'rework_feedback', 'operator_feedback', 'repair_reason', 'repaired_failure_event_id',
    )}
    if not any(fields.get(key) for key in ('rework_feedback', 'operator_feedback', 'repair_reason')):
        return _replayed_task_repair_input(
            state_dir, payload, workflow_run_id=workflow_run_id, task_id=task_id,
        )
    binding = payload.get('plan_review_disposition_ref') or trigger.get('plan_review_disposition_ref')
    if binding:
        from zf.runtime.sidecar_refs import hydrate_sidecar_ref
        verdict = hydrate_sidecar_ref(state_dir, binding['control_result_ref']).payload
        if (verdict.get('workflow_run_id') != workflow_run_id or verdict.get('verdict') != 'rejected'
                or not verdict.get('reviewed_plan_candidate_digest')
                or verdict['reviewed_plan_candidate_digest'] != binding['reviewed_plan_candidate_digest']):
            raise ValueError('Plan repair feedback differs from the rejected candidate binding')
        assessments = [r for r in verdict.get('execution_assessments', [])
                       if isinstance(r, Mapping) and r.get('disposition') in
                       {'no_repair', 'repair_required', 'needs_clarification'}]
        digest = binding.get('plan_review_disposition_digest')
        if not digest or not assessments or any(
            not isinstance(row.get('runtime_evidence'), Mapping)
            or row['runtime_evidence'].get('sha256') != digest for row in assessments
        ):
            raise ValueError('Plan repair dispositions differ from the frozen execution review')
        fields['plan_review_dispositions'] = {**binding, 'assessments': assessments}
    retained = _fresh_retry_repair_inputs(
        state_dir, payload, fields=fields, workflow_run_id=workflow_run_id, task_id=task_id,
    )
    if retained:
        return retained
    descriptor = write_immutable_json_sidecar(state_dir, {
        'schema_version': 'reader-repair-context.v1', 'workflow_run_id': workflow_run_id,
        'task_id': task_id, 'source_event_id': source_event_id, **fields,
        'authority': 'Recovery context only; preserve provenance, independently evaluate evidence and the current contract.',
    }, root='reader-repair-context', kind='reader_repair_context', schema_version='reader-repair-context.v1',
        created_by='canonical-handoff-resolver', source_event_id=source_event_id)
    return [{**descriptor, 'source_id':'critical-reader-repair-context', 'allowed_paths':['$']}]


def _fresh_retry_repair_inputs(state_dir, payload, *, fields, workflow_run_id, task_id):
    """Keep unchanged recovery bodies pinned when minting a new reader call.

    A new dispatch causation must not replace an immutable input while its old
    required-read locator remains in the request. Retain both original content
    and provenance; this grants no old attempt's read coverage to the new one.
    """
    from pathlib import Path
    from zf.core.events.log import EventLog
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    from zf.runtime.workflow_operation import load_workflow_operation

    parent = str(payload.get('retry_of_operation_id') or '')
    reads = [r for r in payload.get('required_reads', [])
             if r.get('source_id') == 'critical-reader-repair-context']
    if not parent or not reads:
        return []
    operation = load_workflow_operation(EventLog(Path(state_dir) / 'events.jsonl'), parent)
    if not operation or operation.get('operation_type') != 'fanout_reader_child':
        raise ValueError('reader retry recovery input has no parent Reader operation')
    body = hydrate_sidecar_ref(state_dir, operation['request_ref']).payload
    if any(body.get(key) != value for key, value in (
        ('workflow_run_id', workflow_run_id), ('task_id', task_id), ('operation_id', parent),
        ('role_instance', payload.get('role_instance')),
    )):
        raise ValueError('reader retry recovery input parent identity mismatch')
    refs = []
    for row in reads:
        if row not in body['request'].get('required_reads', []):
            raise ValueError('reader retry recovery input was not pinned by its parent')
        digest = str(row.get('artifact_sha256') or '')
        descriptor = {'ref': f'artifacts/reader-repair-context/{digest}.json', 'sha256': digest,
                      'kind': 'reader_repair_context', 'content_type': 'application/json'}
        context = hydrate_sidecar_ref(state_dir, descriptor).payload
        if (row.get('artifact_id') != f'{digest}.json'
                or context.get('schema_version') != 'reader-repair-context.v1'
                or context.get('workflow_run_id') != workflow_run_id
                or context.get('task_id') != task_id
                or any(context.get(key) != value for key, value in fields.items())):
            raise ValueError('reader retry recovery context changed; requires a new recovery input')
        refs.append({**descriptor, 'source_id': row['source_id'], 'allowed_paths': ['$']})
    return refs


def _replayed_task_repair_input(state_dir, payload: Mapping[str, Any], *,
                              workflow_run_id: str, task_id: str) -> list[dict[str, Any]]:
    """Retain one immutable recovery input after an unsent task retry.

    The rework recommendation leaves the scheduler projection once its successor
    exists. That must not remove an input already frozen with that operation.
    Explicit new feedback still goes through ordinary request divergence checks.
    """
    import re
    from pathlib import Path

    from zf.core.events.log import EventLog
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    from zf.runtime.workflow_operation import load_workflow_operation

    operation_id = str(payload.get('operation_id') or '')
    if not operation_id or not payload.get('task_pipeline_stage'):
        return []
    operation = load_workflow_operation(EventLog(Path(state_dir) / 'events.jsonl'), operation_id)
    if operation is None:
        return []
    body = hydrate_sidecar_ref(state_dir, operation['request_ref']).payload
    request = body['request']
    source_id = 'critical-reader-repair-context'
    reads = [row for row in request.get('required_reads', []) if row.get('source_id') == source_id]
    if not reads:
        return []
    if any(body.get(key) != value for key, value in (
        ('workflow_run_id', workflow_run_id), ('task_id', task_id), ('operation_id', operation_id),
    )):
        raise ValueError('replayed repair input belongs to a different task operation')
    for key in ('task_pipeline_stage', 'operation_generation', 'task_map_generation',
                'contract_snapshot_digest'):
        if request.get(key) != payload.get(key):
            raise ValueError(f'replayed repair input identity changed: {key}')
    digests = {str(row.get('artifact_sha256') or '') for row in reads}
    if len(digests) != 1 or not re.fullmatch(r'[0-9a-f]{64}', next(iter(digests))):
        raise ValueError('replayed repair input has an ambiguous or invalid digest')
    digest = next(iter(digests))
    descriptor = {'ref': f'artifacts/reader-repair-context/{digest}.json', 'sha256': digest,
                  'kind': 'reader_repair_context', 'content_type': 'application/json'}
    context = hydrate_sidecar_ref(state_dir, descriptor).payload
    if (context.get('schema_version') != 'reader-repair-context.v1'
            or context.get('workflow_run_id') != workflow_run_id or context.get('task_id') != task_id):
        raise ValueError('replayed repair context identity mismatch')
    return [{**descriptor, 'source_id': source_id, 'allowed_paths': ['$']}]
