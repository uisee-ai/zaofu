"""Bounded, non-authoritative observation of Plan review input selection."""
from __future__ import annotations

from typing import Any, Mapping, Sequence


def input_size(manifest: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    sources = {(str(s.get('source_id') or ''), str(s.get('artifact_id') or ''), str(s.get('sha256') or '')): s
               for s in manifest.get('sources', []) if isinstance(s, Mapping)}
    distinct: dict[str, int] = {}
    unknown = 0
    for row in rows:
        key = (str(row.get('source_id') or ''), str(row.get('artifact_id') or ''), str(row.get('artifact_sha256') or ''))
        source = sources.get(key, {})
        selections = [item for item in source.get('read_selections', []) if isinstance(item, Mapping)
                      and item.get('json_path') == (row.get('json_path') or '$')]
        if len(selections) != 1:
            unknown += 1
            continue
        digest = selections[0].get('selected_sha256')
        size = selections[0].get('selected_bytes')
        if not isinstance(digest, str) or len(digest) != 64 or type(size) is not int or size < 0:
            unknown += 1
            continue
        distinct[digest] = size
    return {'required_rows': len(rows), 'distinct_selected_bytes': sum(distinct.values()),
            'unknown_rows': unknown}


def review_diagnostics(manifest: Mapping[str, Any], *, before: list[dict[str, Any]],
                       after: list[dict[str, Any]], explicit: bool) -> dict[str, Any]:
    sources = manifest.get('sources') or []
    ids = {str(s.get('source_id') or '') for s in sources if isinstance(s, Mapping)}
    if len(after) < len(before):
        mode, reason = 'incremental', 'verified_prior_evidence_and_delta'
    elif explicit:
        mode, reason = 'full', 'explicit_required_reads'
    elif 'plan-rework-package' not in ids:
        mode, reason = 'full', 'no_baseline_package'
    elif 'child-artifact-unchanged-review' not in ids:
        mode, reason = 'full', 'prior_evidence_or_binding_unavailable'
    else:
        mode, reason = 'full', 'proof_not_eligible_or_no_reduction'
    return {'schema_version': 'plan-review-input-diagnostics.v1', 'strategy': 'cross_version_evidence',
            'mode': mode, 'reason': reason,
            'before': input_size(manifest, before), 'after': input_size(manifest, after)}
