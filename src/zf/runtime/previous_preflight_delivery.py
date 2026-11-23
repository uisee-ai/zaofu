"""Deliver old preflight usage on demand without changing frozen call inputs."""
from __future__ import annotations

from zf.runtime.artifact_read_ledger import _safe_component
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_execution_delivery import execution_review_delivery
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import load_workflow_operation


def prepare_previous_preflight_delivery(runtime, *, payload: dict, operation_id: str) -> None:
    sources = payload.get('input_refs') or []
    if not any(isinstance(s, dict) and s.get('source_id', '').startswith('previous-plan-candidate-')
               for s in sources):
        return
    operation = load_workflow_operation(runtime.event_log, operation_id)
    frozen = None
    if operation is not None:
        request = hydrate_sidecar_ref(runtime.state_dir, operation['request_ref']).payload['request']
        attempt = _safe_component(request['active_attempt_id'])
        sha = request['source_manifest_digest']
        frozen = hydrate_sidecar_ref(runtime.state_dir, {
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
                body = hydrate_sidecar_ref(runtime.state_dir, previous[0]).payload
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
                        hydrate_sidecar_ref(runtime.state_dir, matches[0])
                        companions.append(matches[0])
                    result.extend([previous[0], *companions])
                    continue
            result.append(source)  # Legacy inputs remain full even after an upgrade.
            continue
        result.extend(_project(runtime.state_dir, source))
    payload['input_refs'] = result


def _project(state_dir, source):
    body = hydrate_sidecar_ref(state_dir, source).payload
    if not isinstance(body, dict) or body.get('schema_version') != 'plan-candidate-preflight.v1':
        return [source]
    review = body.get('execution_review')
    usage = review.get('usage_observations') if isinstance(review, dict) else None
    if (not isinstance(usage, dict) or usage.get('schema_version') != 'execution-usage-facts.v1'
            or not isinstance(usage.get('groups'), list)):
        return [source]
    # Reuse only the established mechanical usage projection. All other fields,
    # including unknown future fields and verification/acceptance bodies, stay full.
    delivery, details = execution_review_delivery({'usage_observations': usage})
    item = details[0]
    detail = write_immutable_json_sidecar(state_dir, item['body'], root=item['root'],
        kind=item['kind'], schema_version=item['schema_version'], created_by='plan-preflight-delivery')
    suffix = source['source_id'] + '-' + source['sha256']
    detail.update(source_id='preflight-usage-' + suffix, artifact_id='execution-usage-facts.json',
                  allowed_paths=['$'], access_scope=dict(source.get('access_scope') or {}))
    summary = {**delivery['usage_observations'], 'detail_source_id': detail['source_id']}
    original = {**source, 'source_id': 'preflight-original-' + suffix}
    projected = write_immutable_json_sidecar(state_dir, {
        'schema_version': 'plan-preflight-delivery.v1', 'original_source': original,
        'preflight': {**body, 'execution_review': {**review, 'usage_observations': summary}},
        'delivery_note': 'Only mechanical usage groups are on demand. Original preflight and exact '
                         'usage detail remain controlled sources; this projection grants no approval.',
    }, root='plan-preflight-delivery', kind='plan_preflight_delivery',
        schema_version='plan-preflight-delivery.v1', created_by='plan-preflight-delivery')
    return [{**source, **projected, 'artifact_id': 'plan-candidate-preflight.delivery.json',
             'access_scope': dict(source.get('access_scope') or {})}, original, detail]
