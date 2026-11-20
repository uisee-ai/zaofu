"""Mechanical certificate assembly from an explicit agent scope claim."""
from __future__ import annotations

import hashlib
import json
import re

from zf.runtime.plan_local_review import scoped_port_paths


def prepare_scope_evidence(baseline: dict[str, bytes], candidate: dict[str, bytes], claim: dict) -> dict:
    """Compute exact hashes; never infer semantic scope or runtime eligibility."""
    affected = claim.get('affected_task_ids')
    evidence = claim.get('evidence_refs')
    if (claim.get('scope_complete') is not True or claim.get('verification_responsibility_preserved') is not True
            or not isinstance(affected, list) or not affected or not all(isinstance(x, str) and x for x in affected)
            or not isinstance(evidence, list) or not evidence or not all(isinstance(x, str) and x for x in evidence)
            or not isinstance(claim.get('workflow_run_id'), str) or not claim['workflow_run_id']
            or not re.fullmatch(r'[a-f0-9]{64}', str(claim.get('baseline_package_sha256') or ''))):
        raise ValueError('an explicit complete scope claim and baseline identity are required')
    old_map, new_map = json.loads(baseline['task_map']), json.loads(candidate['task_map'])
    if not isinstance(old_map, dict) or not isinstance(new_map, dict) or set(baseline) != set(candidate):
        raise ValueError('baseline and candidate must cover the same explicit ports')
    ports = []
    for name in sorted(set(baseline) - {'task_map'}):
        old, new = json.loads(baseline[name]), json.loads(candidate[name])
        if not isinstance(old, dict) or not isinstance(new, dict):
            raise ValueError('Plan ports must be JSON objects')
        if old.get('schema_version') != 'task-scoped-plan-input.v1':
            if baseline[name] != candidate[name]:
                raise ValueError('a non-scoped port changed; use full review')
            continue
        if scoped_port_paths(old_map=old_map, new_map=new_map, old_port=old, new_port=new,
                             affected_task_ids=affected) is None:
            raise ValueError('scope is structurally incomplete; use full review')
        ports.append({'logical_name': name, 'baseline_sha256': hashlib.sha256(baseline[name]).hexdigest(),
                      'candidate_sha256': hashlib.sha256(candidate[name]).hexdigest()})
    if not ports:
        raise ValueError('at least one complete task-scoped port is required')
    return {'schema_version': 'scoped-plan-review.v1',
            **{key: claim[key] for key in ('workflow_run_id', 'baseline_package_sha256',
                'affected_task_ids', 'evidence_refs', 'scope_complete', 'verification_responsibility_preserved')},
            'baseline_task_map_sha256': hashlib.sha256(baseline['task_map']).hexdigest(),
            'candidate_task_map_sha256': hashlib.sha256(candidate['task_map']).hexdigest(), 'ports': ports}
