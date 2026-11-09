"""Declared input coverage preflight, shared by producers and Plan Preview.

Semantic omissions are questions for Critic, never a keyword-driven verdict.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Mapping

from zf.runtime.artifact_navigation import build_navigation
from zf.runtime.artifact_role_inputs import PROFILES
from zf.runtime.call_result_envelope import canonical_json_sha256
from zf.runtime.task_scoped_plan_inputs import task_input_paths

PORTS = ('task_map', 'task_execution_input', 'execution_spec', 'source_index', 'source_inventory', 'coverage_ledger')


def _rows(value: Any) -> list[dict]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _strings(value: Any) -> list[str]:
    return [s for s in value if isinstance(s, str) and s] if isinstance(value, list) else []


def inspect_plan_inputs(artifacts: Mapping[str, Any], *, task_id: str = '', offset: int = 0, limit: int = 5,
                        bindings: list[dict] | None = None, plan_id: str = '') -> dict:
    if not 1 <= limit <= 20 or offset < 0 or len(task_id) > 256:
        raise ValueError('input review requires limit 1..20 and nonnegative offset')
    if any(not isinstance(value, dict) for value in artifacts.values()):
        raise ValueError('Plan ports must contain JSON objects')
    task_map = artifacts.get('task_map') or {}
    if not isinstance(task_map, dict) or not isinstance(task_map.get('tasks'), list):
        raise ValueError('task_map.tasks is required')
    tasks = _rows(task_map['tasks'])
    ids = [str(t.get('task_id') or t.get('id') or '') for t in tasks]
    duplicate_ids = {key for key,count in Counter(ids).items() if count > 1}
    sources = _rows((artifacts.get('source_index') or {}).get('traceability', (artifacts.get('source_index') or {}).get('rows')))
    ledger = _rows((artifacts.get('coverage_ledger') or {}).get('entries', (artifacts.get('coverage_ledger') or {}).get('rows')))
    inventory = artifacts.get('source_inventory') or {}
    visual = [*_rows(inventory.get('pages')), *_rows(inventory.get('forms'))]
    candidates = [task for task in tasks if not task_id or str(task.get('task_id') or task.get('id')) == task_id]
    partitions = _rows((artifacts.get('task_execution_input') or {}).get('tasks'))
    extra_partitions = sorted({str(p.get('task_id') or '') for p in partitions} - set(ids))
    known_refs = {str(s.get('source_ref') or '') for s in sources} - {''}
    items = []
    for task in candidates[offset:offset+limit]:
        key = str(task.get('task_id') or task.get('id') or '')
        issues = []
        if not key or key in duplicate_ids:
            issues.append('task_id_missing_or_duplicated')
        dependencies = _strings(task.get('blocked_by') or task.get('dependencies'))
        issues.extend('unknown_dependency:'+dep for dep in dependencies if dep not in ids or dep == key)
        criteria = [str(ac.get('id') or '') for ac in _rows(task.get('acceptance_criteria'))]
        mapped = [s for s in sources if key in [str(s.get('task_id') or ''), *_strings(s.get('task_ids'))]]
        source_refs = sorted(set([str(task.get('source_ref') or ''), *_strings(task.get('source_refs')),
            *[str(s.get('source_ref') or '') for s in mapped],
            *[str(ac.get('source_ref') or '') for ac in _rows(task.get('acceptance_criteria'))]]) - {''})
        if not mapped:
            issues.append('source_mapping_unknown' if 'source_index' not in artifacts else 'source_not_mapped')
        if known_refs:
            issues.extend('source_reference_unknown:'+ref for ref in source_refs if ref not in known_refs)
        images = sorted({image for row in visual if key in [str(row.get('task_id') or ''), *_strings(row.get('task_ids'))]
                         for image in _strings(row.get('reference_images'))})
        input_body = artifacts.get('task_execution_input')
        selections, nav = [], {'entries': [], 'complete': False}
        if input_body is None:
            issues.append('task_execution_input_unavailable')
        else:
            try:
                selections = task_input_paths(input_body, task_id=key)
                if selections == ['$']:
                    issues.append('legacy_full_input')
                else:
                    body = next(row['body'] for row in input_body['tasks'] if row['task_id'] == key)
                    nav = build_navigation(json.dumps({'shared': input_body['shared'], 'task': body}, ensure_ascii=False))
                    declared_ac = {e['id'] for e in nav['entries'] if e.get('relation') == 'acceptance'}
                    issues.extend('acceptance_reference_missing:'+ac for ac in criteria if ac and ac not in declared_ac)
                    declared_sources = {e['id'] for e in nav['entries'] if e.get('relation') == 'source'}
                    issues.extend('source_reference_missing:'+ref for ref in source_refs if ref not in declared_sources)
                    if known_refs:
                        issues.extend('source_reference_unknown:'+ref for ref in sorted(declared_sources-known_refs))
                    declared_images = {e['id'] for e in nav['entries'] if e.get('relation') in {'image','source'}}
                    issues.extend('image_reference_missing:'+image for image in images if image not in declared_images)
                    if not nav['complete']:
                        issues = [issue.replace('acceptance_reference_missing:', 'reference_lookup_incomplete:').replace('source_reference_missing:', 'reference_lookup_incomplete:').replace('image_reference_missing:', 'reference_lookup_incomplete:') for issue in issues]
            except (ValueError, KeyError, TypeError) as exc:
                issues.append('partition_invalid:'+str(exc)[:160])
        evidence = task.get('evidence_contract') if isinstance(task.get('evidence_contract'), dict) else {}
        policy = evidence.get('plan_input_selection')
        policy_valid = isinstance(policy, dict) and set(policy) == {'schema_version','implementation','task-verify'} and policy.get('schema_version') == 'task-plan-input-selection.v1'
        if policy_valid:
            policy_valid = all(isinstance(policy[p], list) and policy[p] and len(policy[p]) == len(set(_strings(policy[p])))
                and 'task_execution_input' in policy[p] for p in ('implementation','task-verify'))
        if policy is not None and not policy_valid:
            issues.append('invalid_selection_declaration')
        available_ports = {row.get('name') for row in bindings} if bindings is not None else set(artifacts)
        profiles = []
        for profile, (scope, reason) in PROFILES.items():
            if profile == 'plan-synth':
                continue
            selected = policy[profile] if policy_valid and profile in {'implementation','task-verify'} else None
            profiles.append({'profile': profile, 'scope': scope, 'reason': reason,
                'unavailable_ports': [port for port in selected or [] if port not in available_ports],
                'ports': selected, 'policy': 'declared_selection' if selected else 'existing_profile_required_reads',
                'paths': selections if selected and 'task_execution_input' in selected else None})
        declared_exclusions = [row for row in ledger if row.get('disposition') in {'excluded','out_of_scope','not_applicable','explicit_non_goal'}
                               and key in [str(row.get('task_id') or ''), *_strings(row.get('task_ids'))]]
        counts = {'acceptance': len(criteria), 'source_refs': len(source_refs), 'images': len(images), 'issues': len(issues)}
        items.append({'task_id': key, 'title': str(task.get('title') or '')[:160], 'dependencies': dependencies[:16],
            'acceptance_ids': criteria[:16], 'source_refs': source_refs[:16], 'image_refs': images[:16],
            'profiles': profiles, 'issues': issues[:32], 'counts': counts,
            'declared_exclusions': [{'source_id': str(r.get('source_id') or '')[:160], 'reason': str(r.get('reason') or '')[:256]} for r in declared_exclusions[:8]],
            'navigation_complete': nav['complete'],
            'complete': max(len(criteria),len(source_refs),len(images),len(dependencies)) <= 16 and len(issues) <= 32 and len(declared_exclusions) <= 8,
            'semantic_review': 'required', 'approval': 'not_evaluated'})
    source_status = []
    for source in sources:
        source_id = str(source.get('source_id') or '')
        declarations = [r for r in ledger if r.get('source_id') == source_id]
        disposition = str(declarations[0].get('disposition') or 'unknown') if len(declarations) == 1 else 'conflict' if declarations else 'unmapped'
        source_status.append({'source_id': source_id, 'disposition': disposition,
            'source_ref': str(source.get('source_ref') or '')[:256],
            'reason': str(declarations[0].get('reason') or '')[:256] if len(declarations) == 1 else ''})
    result = {'plan_id': plan_id, 'source_status': source_status[:8], 'source_total': len(source_status), 'source_status_complete': len(source_status) <= 8,
        'schema_version': 'plan-input-review.v1', 'binding_digest': canonical_json_sha256(bindings or artifacts),
        'extra_partition_ids': extra_partitions[:16], 'extra_partition_count': len(extra_partitions),
        'partition_membership_complete': len(extra_partitions) <= 16,
        'scope': 'declared_plan_inputs', 'task_id': task_id, 'total': len(candidates), 'offset': offset,
        'items': items, 'next_offset': None,
        'coverage': 'declarations_only', 'source_inventory_available': 'source_inventory' in artifacts,
        'source_index_available': 'source_index' in artifacts,
        'semantic_questions': ['异常流程是否保留？','共享规则和依赖接口是否完整？','原始图片的页面、状态、位置和视觉要求是否均有归属？']}
    while True:
        end = offset + len(result['items'])
        result['next_offset'] = end if end < len(candidates) else None
        if len(json.dumps(result, ensure_ascii=False).encode()) < 8192:
            return result
        if len(result['items']) <= 1:
            raise ValueError('one task input review exceeds 8192 bytes; inspect bound original artifacts')
        result['items'].pop()
