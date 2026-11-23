"""Owner-reviewed file mappings for a new, independently judged Candidate Reader."""
from pathlib import Path

from zf.runtime.host_evidence_media import required_media_paths
from zf.runtime.sidecar_refs import hydrate_sidecar_ref

KEY = 'candidate_media_review_ref'
SCHEMA = 'candidate-media-review.v1'


def review_body(state: Path, ref: dict) -> dict:
    if (not isinstance(ref, dict) or ref.get('kind') != 'candidate_media_review'
            or ref.get('created_by') != 'operator' or ref.get('schema_version') != SCHEMA
            or ref.get('ref') != f"artifacts/operator/candidate-media-reviews/{ref.get('sha256')}.json"
            or ref.get('allowed_paths') not in (None, ['$'])):
        raise ValueError('invalid operator Candidate media review descriptor')
    body = hydrate_sidecar_ref(state, ref, max_bytes=256 * 1024).payload
    if not isinstance(body, dict) or body.get('schema_version') != SCHEMA:
        raise ValueError('invalid operator Candidate media review body')
    return body


def reviewed_paths(command: dict, review: dict) -> set[str]:
    """Replace only legacy directory placeholders; preserve every existing named file."""
    if (review.get('original_command') != command or not isinstance(command.get('producer_paths'), list)
            or not command['producer_paths'] or command.get('owner') != 'candidate_verify'):
        raise ValueError('Candidate media review differs from original command')
    old = command['producer_paths']
    if not all(isinstance(path, str) for path in old) or not any(path.endswith('/') for path in old):
        raise ValueError('Candidate media review requires an existing directory declaration')
    # Only the trailing separator may have been invalid in the legacy declaration.
    required_media_paths({'producer_paths': [p.removesuffix('/') for p in old]})
    keep = required_media_paths({'producer_paths': [p for p in old if not p.endswith('/')]})
    paths = required_media_paths({'producer_paths': review.get('producer_paths')})
    if not paths - keep or len(paths) != len(review['producer_paths']) or not keep <= paths:
        raise ValueError('Candidate media review drops named files or lacks distinct file names')
    return paths


def validate_owner_review(state, events, requested, payload, failure, native):
    ref = payload[KEY]
    if (requested.type != 'operator.intent.created' or requested.origin != 'external'
            or requested.actor != 'operator' or requested.payload.get('action') != 'stage-replan-new-generation'
            or requested.payload.get('action_request') != payload
            or requested.correlation_id != failure.payload.get('workflow_run_id')):
        raise ValueError('Candidate media review requires an exact external operator request')
    review = review_body(state, ref)
    host = next((e for e in events if e.id == review.get('host_receipt_event_id')), None)
    if (review.get('failure_event_id') != failure.id or review.get('workflow_run_id') != requested.correlation_id
            or review.get('candidate_event_id') != native.id
            or review.get('target_commit') != native.payload.get('candidate_head_commit')
            or host is None or host.origin != 'kernel' or host.type != 'runtime.action.attempt.completed'
            or host.payload.get('action') != 'task-host-assist' or host.payload.get('kind') != 'verify'
            or host.payload.get('workflow_run_id') != requested.correlation_id
            or host.payload.get('task_map_generation') != native.payload.get('task_map_generation')
            or host.payload.get('task_id') != failure.payload.get('pdd_id')
            or host.payload.get('receipt_ref') != review.get('original_receipt')):
        raise ValueError('Candidate media review receipt or failure scope mismatch')
    receipt = hydrate_sidecar_ref(state, host.payload['receipt_ref']).payload
    if (receipt.get('target_commit') != review['target_commit'] or not receipt.get('source_stable')
            or receipt.get('execution_origin') != 'preauthorized_container' or receipt.get('exit_code') != 0
            or receipt.get('status') != 'completed'):
        raise ValueError('Candidate media review requires a successful original execution')
    names = reviewed_paths(receipt['manifest']['command'], review)
    archive = hydrate_sidecar_ref(state, receipt['evidence_ref'], max_bytes=200 * 1024 * 1024).payload
    if (archive.get('target_commit') != review['target_commit'] or archive.get('image') != receipt.get('image')
            or not names <= {f['path'] for f in archive['files']}):
        raise ValueError('Candidate media review names missing from original archive')
    return ref


def authorized_review(events, *, run, fanout_id, source_event_id):
    """Follow the actual fanout trigger and the completed controlled action, never worker fields."""
    starts = [e for e in events if e.type == 'fanout.started' and e.origin == 'kernel'
              and e.payload.get('fanout_id') == fanout_id and e.payload.get('trace_id', e.correlation_id) == run]
    trigger_id = starts[-1].payload['trigger_event_id'] if starts else source_event_id
    trigger = next((e for e in reversed(events) if e.id == trigger_id), None)
    if not trigger or trigger.type != 'candidate.ready' or KEY not in trigger.payload:
        return None
    ref = trigger.payload[KEY]
    request = next((e for e in events if e.id == trigger.causation_id), None)
    if (trigger.origin != 'kernel' or trigger.payload.get('workflow_run_id') != run
            or not request or request.origin != 'external' or request.actor != 'operator'
            or request.type != 'operator.intent.created' or request.correlation_id != run
            or request.payload.get('action') != 'stage-replan-new-generation'
            or request.payload.get('action_request', {}).get(KEY) != ref
            or request.payload['action_request'].get('repaired_failure_event_id') != trigger.payload.get('repaired_failure_event_id')):
        raise ValueError('Candidate media review lacks its bound operator request')
    effects = {e.id for e in events if e.type == 'runtime.action.attempt.started' and e.origin == 'kernel'
               and e.causation_id == request.id and e.payload.get('action') == 'stage-replan-new-generation'
               and e.payload.get('payload', {}).get(KEY) == ref}
    if not any(e.type == 'runtime.action.attempt.completed' and e.origin == 'kernel' and e.causation_id in effects
               and e.payload.get('action') == 'stage-replan-new-generation' and e.payload.get('status') == 'applied'
               and e.payload.get('result_event_id') == trigger.id for e in events):
        raise ValueError('Candidate media review recovery action is incomplete')
    return ref
