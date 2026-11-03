"""Bounded input accounting; bytes, provider usage and recovery stay distinct."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from zf.runtime.artifact_access import artifact_access_allowed
from zf.runtime.call_result_envelope import canonical_json_sha256


def input_cost_report(*, state_dir: Path, manifest: dict, identity: Mapping[str, str],
                      events: list, reads: list[dict], consumer_actor: str = '') -> dict[str, Any]:
    from zf.runtime.workflow_operation import reduce_workflow_operations
    from zf.runtime.execution_usage_facts import execution_usage_facts
    attempt = manifest['attempt_id']
    sources = [s for s in manifest['sources'] if artifact_access_allowed(s.get('access_scope'),
        actor=identity.get('actor', ''), role=identity.get('role', ''), purpose=identity.get('purpose', ''))]
    allowed = {(s['source_id'], s['artifact_id'], s['sha256'], s.get('occurrence_id', '')): s for s in sources}
    operations = [op for op in reduce_workflow_operations(events).values()
        if op.get('active_attempt_id') == attempt and op.get('workflow_run_id') == manifest.get('workflow_run_id')
        and op.get('task_id') == manifest.get('task_id')]
    operation = operations[0] if len(operations) == 1 else {}
    actor = consumer_actor or str(operation.get('role_instance') or '')
    seen, delivered, ranges = set(), {}, set()
    produced = repeated = 0
    reasons: Counter = Counter()
    read_ms = 0.0
    for row in reads:
        source = allowed.get((row.get('source_id'), row.get('artifact_id'), row.get('artifact_sha256'), row.get('occurrence_id', '')))
        if (not source or row.get('attempt_id') != attempt or row.get('status') != 'read'
                or not actor or row.get('consumer_actor') != actor
                or ('$' not in source.get('allowed_paths', ['$']) and row.get('json_path', '$') not in source['allowed_paths'])):
            continue
        # Aliases emitted in one bundle are one physical body range; distinct
        # CLI deliveries still count even if the immutable output ref is equal.
        delivery = row.get('delivery_id') or row.get('read_at')
        body = (row.get('selected_sha256') or row.get('artifact_sha256'), row.get('page_start'),
                row.get('page_end'), None if row.get('selected_sha256') else row.get('json_path'), row.get('returned_bytes'))
        key = (delivery, body)
        if key in seen:
            continue
        seen.add(key)
        count = max(0, int(row.get('returned_bytes') or 0))
        produced += count
        if body in ranges:
            repeated += count
        ranges.add(body)
        reason = row.get('read_reason')
        reasons[reason if reason in {'initial','recovery','retry'} else 'unspecified'] += count
        delivered[delivery] = row
    for row in delivered.values():
        read_ms += float(row.get('host_elapsed_ms') or 0)
    physical = {(s['sha256'], r.get('source_bytes')) for s in sources for r in s.get('read_selections', [])}
    selections = {(r.get('selected_sha256'), r.get('selected_bytes')) for s in sources for r in s.get('read_selections', [])}
    observations = [e.payload for e in events if e.type == 'artifact.input.observed'
        and e.payload.get('attempt_id') == attempt and e.actor == actor
        and e.payload.get('manifest_digest') == canonical_json_sha256(manifest)]
    usage = None
    if operation and actor == operation.get('role_instance') and len(sources) == len(manifest['sources']):
        facts = execution_usage_facts(state_dir, workflow_run_id=manifest.get('workflow_run_id', ''))
        groups = [g for g in facts['groups'] if g['operation_id'] == operation['operation_id']
                  and g['attempt_id'] == attempt and g['role_instance'] == actor]
        if len(groups) == 1:
            usage = {k: groups[0][k] for k in ('input_tokens','output_tokens','cache_read_tokens',
                'cache_creation_tokens','cache_creation_1h_tokens','estimated_usd','provider_reported_usd','billed_usd','sample_count')}
    from zf.runtime.artifact_read_requirements import required_reads_for_attempt
    try:
        required = required_reads_for_attempt(events, attempt, state_dir=state_dir, operation_id=operation.get('operation_id',''))
    except (OSError, ValueError):
        required = None
    comparison = {key: manifest.get(key, '') for key in ('task_id','contract_revision','task_map_generation','target_commit',
        'plan_artifact_package_digest','read_purpose')}
    comparison['required_reads'] = required
    comparison['selections'] = sorted((s['source_id'],s['artifact_id'],s['sha256'],tuple(s.get('allowed_paths', ['$']))) for s in sources)
    return {'schema_version': 'attempt-input-cost.v1', 'attempt_id': attempt, 'actor': actor,
        'binding_digest': canonical_json_sha256(comparison),
        'comparison_qualified': bool(required is not None and sources and manifest.get('contract_revision') and manifest.get('plan_artifact_package_digest')),
        'visible_sources': len(sources), 'scope_complete': len(sources) == len(manifest['sources']),
        'source_bytes': sum(n for _, n in physical) if physical and all(n is not None for _,n in physical) else None,
        'selected_bytes': sum(n for _, n in selections) if selections and all(n is not None for _,n in selections) else None,
        'snapshot_metadata_bytes': sum(len(json.dumps(s.get('read_selections', []), ensure_ascii=False).encode()) for s in sources),
        'navigation_metadata_bytes': sum(len(json.dumps(r.get('navigation',{}), ensure_ascii=False).encode())
                                         for s in sources for r in s.get('read_selections', [])),
        'produced_body_bytes': produced, 'repeated_exact_body_bytes': repeated,
        'read_deliveries': len(delivered), 'read_bytes_by_reason': dict(reasons),
        'host_read_ms': read_ms if delivered and all('host_elapsed_ms' in r for r in delivered.values()) else None,
        'navigation': {'observed_calls': len(observations), 'output_bytes': sum(o['output_bytes'] for o in observations),
                       'host_ms': sum(o['host_elapsed_ms'] for o in observations)},
        'provider_usage': usage, 'provider_attribution': 'whole_operation' if usage else 'unknown',
        'current_memory': 'unknown', 'body_read_evidence': False,
        'limitations': ['索引构建成本未单独归因；历史未观测调用不计零成本。',
            'Provider usage 是整个 operation，不能按字节分摊为某次读取或节约。',
            '重复正文只统计完全相同范围；read reason 为调用者声明，非完成证据。']}


def compare_input_costs(before: dict, after: dict) -> dict:
    if (before.get('schema_version') != 'attempt-input-cost.v1' or after.get('schema_version') != 'attempt-input-cost.v1'
            or not before.get('comparison_qualified') or not after.get('comparison_qualified')
            or before.get('binding_digest') != after.get('binding_digest') or before.get('actor') != after.get('actor')):
        raise ValueError('cost comparison requires identical material, acceptance binding, selection and consumer')
    return {'schema_version': 'input-cost-comparison.v1', 'same_scope': True,
        'changes': {key: after[key]-before[key] for key in ('produced_body_bytes','repeated_exact_body_bytes','read_deliveries')},
        'provider_savings': None, 'quality_qualification': 'not_evaluated'}


def record_input_observation(log, manifest: dict, identity: dict, *, kind: str, output: str, elapsed_ms: float) -> None:
    from zf.core.events import EventWriter, ZfEvent
    EventWriter(log).append(ZfEvent(type='artifact.input.observed', actor=identity['actor'], task_id=manifest.get('task_id'),
        payload={'workflow_run_id': manifest.get('workflow_run_id'), 'attempt_id': manifest['attempt_id'],
                 'operation_id': identity.get('operation_id',''), 'manifest_digest': canonical_json_sha256(manifest),
                 'kind': kind, 'output_bytes': len((output+'\n').encode()), 'host_elapsed_ms': round(elapsed_ms, 3)}))
