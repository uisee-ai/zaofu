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
    produced = repeated = pages = partial = 0
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
        pages += int(row.get('page_start') is not None)
        partial += int(bool(row.get('truncated')))
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
    from zf.runtime.plan_review_input_diagnostics import input_size
    required_cost = (input_size({'sources': sources}, required)
                     if required is not None and len(sources) == len(manifest['sources']) else None)
    from zf.runtime.artifact_input_timing import attempt_timing
    review = None
    if operation and len(sources) == len(manifest['sources']) and actor == operation.get('role_instance'):
        review = review_selection(events, operation, manifest)
    return {'schema_version': 'attempt-input-cost.v1', 'attempt_id': attempt, 'actor': actor,
        'binding_digest': canonical_json_sha256(comparison),
        'comparison_qualified': bool(required is not None and sources and manifest.get('contract_revision') and manifest.get('plan_artifact_package_digest')),
        'visible_sources': len(sources), 'scope_complete': len(sources) == len(manifest['sources']),
        'source_bytes': sum(n for _, n in physical) if physical and all(n is not None for _,n in physical) else None,
        'selected_bytes': sum(n for _, n in selections) if selections and all(n is not None for _,n in selections) else None,
        'selected_bytes_scope': 'all_visible_selections_not_required_reads',
        'required_input': required_cost,
        'snapshot_metadata_bytes': sum(len(json.dumps(s.get('read_selections', []), ensure_ascii=False).encode()) for s in sources),
        'navigation_metadata_bytes': sum(len(json.dumps(r.get('navigation',{}), ensure_ascii=False).encode())
                                         for s in sources for r in s.get('read_selections', [])),
        'produced_body_bytes': produced, 'repeated_exact_body_bytes': repeated,
        'read_deliveries': len(delivered), 'read_bytes_by_reason': dict(reasons),
        'paging': {'returned_pages': pages, 'partial_returns': partial,
                   'truncation_repair_calls': None},
        'timing': attempt_timing(events, operation, attempt) if actor == operation.get('role_instance') else None,
        'review_selection': review,
        'review_outcome': review_outcome(state_dir, events, operation, attempt)
            if actor == operation.get('role_instance') and len(sources) == len(manifest['sources']) else None,
        'provider_tool_calls': None,
        'host_read_ms': read_ms if delivered and all('host_elapsed_ms' in r for r in delivered.values()) else None,
        'navigation': {'observed_calls': len(observations), 'output_bytes': sum(o['output_bytes'] for o in observations),
                       'host_ms': sum(o['host_elapsed_ms'] for o in observations)},
        'provider_usage': usage, 'provider_attribution': 'whole_operation' if usage else 'unknown',
        'current_memory': 'unknown', 'body_read_evidence': False,
        'limitations': ['索引构建成本未单独归因；历史未观测调用不计零成本。',
            'Provider usage 是整个 operation，不能按字节分摊为某次读取或节约。',
            '重复正文只统计完全相同范围；read reason 为调用者声明，非完成证据。',
            '分页与截断补读不可等同；执行墙钟时间包含工具和等待，不代表模型推理时间。']}


def review_selection(events: list, operation: dict, manifest: dict) -> dict | None:
    """Read the exact kernel dispatch observation, not a later recomputation."""
    if not operation.get('operation_id') or not operation.get('request_hash'):
        return None
    observations = {}
    for event in events:
        p = event.payload
        if (event.type != 'fanout.synth.dispatched' or event.origin != 'kernel'
                or p.get('operation_id') != operation.get('operation_id')
                or p.get('request_hash') != operation.get('request_hash')
                or p.get('workflow_run_id') != manifest.get('workflow_run_id')
                or p.get('attempt_id') != manifest.get('attempt_id')
                or p.get('attempt_source_manifest_digest') != canonical_json_sha256(manifest)):
            continue
        value = p.get('plan_review_optimization')
        if isinstance(value, dict) and value.get('schema_version') == 'plan-review-input-diagnostics.v1':
            observations[canonical_json_sha256(value)] = value
    return next(iter(observations.values())) if len(observations) == 1 else None


def review_outcome(state_dir: Path, events: list, operation: dict, attempt: str) -> dict | None:
    """Report admitted declarations, never classify finding prose as truth."""
    from zf.runtime.call_result_envelope import hydrate_call_result_envelope
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    admissions = [event for event in events if event.type == 'workflow.call.result.admitted'
                  and event.payload.get('operation_id') == operation.get('operation_id')
                  and event.payload.get('workflow_run_id') == operation.get('workflow_run_id')]
    matched = []
    for event in admissions:
        try:
            envelope = hydrate_call_result_envelope(state_dir, event.payload['envelope_ref'])
            identity = envelope.get('identity') or {}
            if (identity.get('attempt_id') != attempt or identity.get('operation_id') != operation.get('operation_id')
                    or identity.get('workflow_run_id') != operation.get('workflow_run_id')):
                continue
            descriptor = envelope.get('control_result') or envelope.get('control_result_ref')
            admitted = event.payload.get('control_result_ref')
            if (not isinstance(descriptor, dict) or not isinstance(admitted, dict)
                    or any(not descriptor.get(key) or descriptor.get(key) != admitted.get(key)
                           for key in ('ref', 'sha256', 'schema_version'))):
                continue
            control = hydrate_sidecar_ref(state_dir, admitted).payload
            if control.get('schema_version') != 'plan-synthesis-result.v1':
                continue
            findings = control.get('findings')
            counts = Counter((row.get('status') if row.get('status') in {'confirmed', 'closed', 'withdrawn', 'unverified'}
                              else 'unspecified') for row in findings if isinstance(row, dict)) if isinstance(findings, list) else None
            matched.append((descriptor['sha256'], {'verdict': control.get('verdict'),
                'finding_status_counts': dict(counts) if counts is not None else None,
                'quality_attribution': 'reviewer_declared_not_independent_ground_truth'}))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    unique = dict(matched)
    return next(iter(unique.values())) if len(unique) == 1 else None


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
