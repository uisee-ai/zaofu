"""Lossless delivery projection of mechanical Plan diagnostics, not Plan ports."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.runtime.call_result_envelope import canonical_json_sha256


def execution_review_delivery(review: Mapping[str, Any]) -> tuple[dict, list[dict]]:
    """Return required summary and immutable detail bodies without IO.

    Semantic Task Map/AC/command artifacts are unchanged. Detail refs are
    content-addressed exactly as the sanctioned sidecar writer persists them.
    """
    body = dict(review)
    details: list[dict] = []

    def reference(value: dict, name: str, schema: str) -> dict:
        root = f'plan-execution-{name}'
        digest = canonical_json_sha256(value)
        details.append({'body': value, 'root': root, 'schema_version': schema,
                        'kind': f'plan_execution_{name}', 'source_id': root})
        return {'detail_source_id': root, 'detail_ref': f'artifacts/{root}/{digest}.json',
                'detail_sha256': digest}

    usage = body.get('usage_observations')
    if isinstance(usage, Mapping) and usage.get('schema_version') == 'execution-usage-facts.v1':
        groups = usage.get('groups')
        body['usage_observations'] = {
            **{key: value for key, value in usage.items() if key != 'groups'},
            'group_count': len(groups) if isinstance(groups, list) else 0,
            'total_tokens': sum(int(g.get('total_tokens') or 0) for g in groups if isinstance(g, Mapping))
                            if isinstance(groups, list) else 0,
            **reference(dict(usage), 'usage', 'execution-usage-facts.v1'),
        }
    verification = body.get('verification')
    requirements = verification.get('task_requirements') if isinstance(verification, Mapping) else None
    if isinstance(requirements, list) and requirements:
        detail = {'schema_version': 'plan-execution-verification.v1',
                  'input_binding': body.get('input_binding', {}), 'task_requirements': requirements}
        body['verification'] = {
            **{key: value for key, value in verification.items() if key != 'task_requirements'},
            'task_requirement_count': len(requirements),
            'command_count': sum(len(r.get('commands', [])) for r in requirements),
            'acceptance_count': sum(len(r.get('acceptance_criteria', [])) for r in requirements),
            'detail_note': 'Mechanical owner selections only. Original semantic AC and commands remain '
                           'required Plan inputs; inspect this detail for per-task stage-selection questions.',
            **reference(detail, 'verification', 'plan-execution-verification.v1'),
        }
    return body, details


def expanded_verification(state_dir: Path, review: Mapping[str, Any], sources: list) -> dict:
    """Compare upgrades without replacing a frozen attempt's delivery contract."""
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    result = dict(review)
    summary = result.get('verification')
    if not isinstance(summary, Mapping) or summary.get('detail_source_id') != 'plan-execution-verification':
        return result
    matches = [s for s in sources if s.get('source_id') == 'plan-execution-verification']
    if len(matches) != 1:
        raise ValueError('missing or ambiguous plan execution verification detail')
    source = matches[0]
    if summary.get('detail_ref') != source.get('ref') or summary.get('detail_sha256') != source.get('sha256'):
        raise ValueError('plan execution verification detail binding mismatch')
    detail = hydrate_sidecar_ref(state_dir, dict(source)).payload
    rows = detail['task_requirements']
    if (detail.get('schema_version') != 'plan-execution-verification.v1'
            or detail.get('input_binding') != review.get('input_binding', {})
            or summary.get('task_requirement_count') != len(rows)
            or summary.get('command_count') != sum(len(r.get('commands', [])) for r in rows)
            or summary.get('acceptance_count') != sum(len(r.get('acceptance_criteria', [])) for r in rows)):
        raise ValueError('plan execution verification summary mismatch')
    derived = {'detail_source_id', 'detail_ref', 'detail_sha256', 'detail_note',
               'task_requirement_count', 'command_count', 'acceptance_count'}
    result['verification'] = {**{k: v for k, v in summary.items() if k not in derived},
                              'task_requirements': rows}
    return result
