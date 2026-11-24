"""Deliver old preflight usage on demand without changing frozen call inputs."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.core.config.schema import ZfConfig
from zf.core.events.factory import event_log_from_project
from zf.core.events.log import EventLog
from zf.runtime.artifact_read_ledger import _safe_component
from zf.runtime.call_result_envelope import canonical_json_sha256, write_immutable_json_sidecar
from zf.runtime.plan_execution_delivery import execution_review_delivery
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import load_workflow_operation


def prepare_previous_preflight_delivery(runtime, *, payload: dict, operation_id: str) -> None:
    if payload.get('input_refs'):
        payload['input_refs'] = previous_preflight_sources(
            runtime.state_dir, payload['input_refs'], operation_id, event_log=runtime.event_log)


def previous_preflight_sources(
    state_dir: Path, sources: list[dict[str, Any]], operation_id: str, *,
    event_log: EventLog | None = None, config: ZfConfig | None = None,
) -> list[dict[str, Any]]:
    """Project only the mechanical usage detail of full previous-Plan inputs."""
    if not any(isinstance(s, dict) and s.get('source_id', '').startswith('previous-plan-candidate-')
               for s in sources):
        return sources
    if event_log is None:
        event_log = event_log_from_project(state_dir, config=config)
    operation = load_workflow_operation(event_log, operation_id)
    frozen = None
    if operation is not None:
        request = hydrate_sidecar_ref(state_dir, operation['request_ref']).payload['request']
        attempt = _safe_component(request['active_attempt_id'])
        sha = request['source_manifest_digest']
        frozen = hydrate_sidecar_ref(state_dir, {
            'ref': f'artifacts/attempts/{attempt}/source-manifests/{sha}.json', 'sha256': sha,
        }).payload['sources']
    result = []
    for source in sources:
        if (not isinstance(source, dict)
                or not source.get('source_id', '').startswith('previous-plan-candidate-')
                or source.get('allowed_paths') != ['$']
                or source.get('kind') == 'plan_preflight_delivery'):
            result.append(source)
            continue
        if frozen is not None:
            previous = [s for s in frozen if s.get('source_id') == source['source_id']]
            if len(previous) == 1 and previous[0].get('kind') == 'plan_preflight_delivery':
                body = hydrate_sidecar_ref(state_dir, previous[0]).payload
                original = body['original_source']
                keys = ('ref', 'sha256', 'artifact_id', 'allowed_paths', 'access_scope', 'kind')
                if all(original.get(k) == source.get(k) for k in keys):
                    # A replay retains the exact original delivery and detail set.
                    # Changed source/authority reaches the normal divergence guard.
                    detail_id = body['preflight']['execution_review']['usage_observations']['detail_source_id']
                    companions = []
                    for source_id in (original['source_id'], detail_id):
                        matches = [s for s in frozen if s.get('source_id') == source_id]
                        if len(matches) != 1:
                            raise ValueError('previous preflight detail source missing or ambiguous')
                        hydrate_sidecar_ref(state_dir, matches[0])
                        companions.append(matches[0])
                    result.extend([previous[0], *companions])
                    continue
            result.append(source)  # Legacy inputs remain full even after an upgrade.
            continue
        result.extend(_project(state_dir, source))
    return result


def _delivery_parts(body, source):
    if not isinstance(body, dict) or body.get('schema_version') != 'plan-candidate-preflight.v1':
        return None
    review = body.get('execution_review')
    usage = review.get('usage_observations') if isinstance(review, dict) else None
    if (not isinstance(usage, dict) or usage.get('schema_version') != 'execution-usage-facts.v1'
            or not isinstance(usage.get('groups'), list)):
        return None
    # Reuse only the established mechanical usage projection. All other fields,
    # including unknown future fields and verification/acceptance bodies, stay full.
    delivery, details = execution_review_delivery({'usage_observations': usage})
    suffix = source['source_id'] + '-' + source['sha256']
    original = {**source, 'source_id': 'preflight-original-' + suffix}
    summary = {**delivery['usage_observations'], 'detail_source_id': 'preflight-usage-' + suffix}
    view = {
        'schema_version': 'plan-preflight-delivery.v1', 'original_source': original,
        'preflight': {**body, 'execution_review': {**review, 'usage_observations': summary}},
        'delivery_note': 'Only mechanical usage groups are on demand. Original preflight and exact '
                         'usage detail remain controlled sources; this projection grants no approval.',
    }
    return view, original, details[0], summary


def _project(state_dir, source):
    parts = _delivery_parts(hydrate_sidecar_ref(state_dir, source).payload, source)
    if parts is None:
        return [source]
    view, original, item, summary = parts
    detail = write_immutable_json_sidecar(state_dir, item['body'], root=item['root'],
        kind=item['kind'], schema_version=item['schema_version'], created_by='plan-preflight-delivery')
    detail.update(source_id=summary['detail_source_id'], artifact_id='execution-usage-facts.json',
                  allowed_paths=['$'], access_scope=dict(source.get('access_scope') or {}))
    projected = write_immutable_json_sidecar(state_dir, view, root='plan-preflight-delivery',
        kind='plan_preflight_delivery', schema_version=view['schema_version'], created_by='plan-preflight-delivery')
    return [{**source, **projected, 'artifact_id': 'plan-candidate-preflight.delivery.json',
             'access_scope': dict(source.get('access_scope') or {})}, original, detail]


def matches_previous_preflight_binding(
    state_dir: Path, sources: list[dict], binding: Mapping[str, Any], delivered: Mapping[str, Any],
) -> bool:
    """A signed review can bind the full original while its reader receives the kernel view."""
    source_id = binding['source_id']
    if (not source_id.startswith('previous-plan-candidate-') or delivered.get('source_id') != source_id
            or delivered.get('kind') != 'plan_preflight_delivery'
            or delivered.get('artifact_id') != 'plan-candidate-preflight.delivery.json'
            or delivered.get('allowed_paths') != ['$']
            or delivered.get('ref') != f"artifacts/plan-preflight-delivery/{delivered['sha256']}.json"):
        return False
    view = hydrate_sidecar_ref(state_dir, dict(delivered)).payload
    original = view.get('original_source') if isinstance(view, dict) else None
    if (not isinstance(original, dict) or original.get('allowed_paths') != ['$']
            or original.get('source_id') != f"preflight-original-{source_id}-{binding.get('sha256')}"
            or any(not binding.get(k) or original.get(k) != binding[k] for k in ('ref', 'sha256', 'artifact_id'))
            or any(k in binding and original.get(k) != binding[k] for k in ('kind', 'allowed_paths', 'access_scope'))):
        return False

    def bound_companion(expected):
        matches = [s for s in sources if s.get('source_id') == expected['source_id']]
        return len(matches) == 1 and all(matches[0].get(k) == expected[k] for k in
            ('ref', 'sha256', 'kind', 'artifact_id', 'allowed_paths', 'access_scope'))

    if original.get('access_scope') != delivered.get('access_scope') or not bound_companion(original):
        return False
    parts = _delivery_parts(hydrate_sidecar_ref(state_dir, original).payload, {**original, 'source_id': source_id})
    if parts is None:
        return False
    expected_view, _, item, summary = parts
    if canonical_json_sha256(expected_view) != delivered['sha256']:
        return False
    detail = {'source_id': summary['detail_source_id'], 'artifact_id': 'execution-usage-facts.json',
              'ref': summary['detail_ref'], 'sha256': summary['detail_sha256'], 'kind': item['kind'],
              'allowed_paths': ['$'], 'access_scope': original['access_scope']}
    if not bound_companion(detail):
        return False
    hydrate_sidecar_ref(state_dir, detail)
    return True
