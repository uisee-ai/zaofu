"""Pin explicitly declared Plan inputs; never infer authority from document links."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping

from zf.runtime.artifact_read_ledger import materialize_attempt_source_ref
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, verify_sidecar_ref

_BODY_KINDS = {'requirement_authority', 'source_document', 'plan_supplemental_input'}


def declared_plan_inputs(
    *, state_dir: Path, project_root: Path, payload: Mapping[str, Any],
    trigger: Mapping[str, Any], source_refs: Mapping[str, Any],
    existing: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Include bodies already named by the caller, preserving any digest/scope.

    These are review inputs, not a replacement for the current requirement or
    an owner amendment. Opaque/non-file refs remain references, as before.
    """
    carriers = [trigger, payload, {'source_refs': source_refs}]
    # Scan completion can retain the canonical requirement ref while dropping
    # its inline source_refs. Recover declarations from that exact frozen body,
    # never from a mutable current Task or recursively followed document links.
    for source in existing:
        if source.get('kind') == 'requirement_spec' and source.get('ref', '').endswith('.json'):
            body = hydrate_sidecar_ref(state_dir, source).payload
            if isinstance(body, Mapping) and body.get('schema_version') == 'requirement-spec.v1':
                carriers.append(body)
    candidates: list[Any] = []
    for carrier in carriers:
        declared = carrier.get('source_refs')
        for declarations in (carrier, declared if isinstance(declared, Mapping) else {}):
            for key in ('source_ref', 'task_source_ref', 'task_spec_ref'):
                ref = declarations.get(key)
                if ref:
                    candidates.append({'ref': ref, 'sha256': str(
                        declarations.get(key.removesuffix('_ref') + '_digest') or ''
                    ).removeprefix('sha256:')})
        if isinstance(carrier.get('artifact_refs'), list):
            candidates.extend(row for row in carrier['artifact_refs']
                              if isinstance(row, Mapping) and row.get('kind') in _BODY_KINDS)
    seen = {(s.get('ref'), s.get('sha256')): _read_limits(s) for s in existing}
    result = []
    for raw in candidates:
        descriptor = raw if isinstance(raw, Mapping) else {'ref': raw}
        ref = str(descriptor.get('ref') or descriptor.get('path') or '').strip()
        if not ref:
            continue
        source = materialize_attempt_source_ref(
            state_dir=state_dir, project_root=project_root, ref=ref,
            source_id='plan-declared-input-' + hashlib.sha256(ref.encode()).hexdigest()[:16],
            kind='plan_declared_input')
        expected = str(descriptor.get('sha256') or '')
        if expected and source.get('sha256') != expected:
            raise ValueError('declared Plan input digest mismatch')
        if not source:
            if ':' not in ref:
                raise ValueError('declared Plan source body is unavailable')
            continue
        for key in ('access_scope', 'retention', 'source_event_id', 'allowed_paths'):
            if key in descriptor:
                source[key] = descriptor[key]
        identity = (source['ref'], source['sha256'])
        limits = _read_limits(source)
        if identity in seen:
            if seen[identity] != limits:
                raise ValueError('duplicate Plan input has conflicting read limits')
            continue
        result.append(source)
        seen[identity] = limits
    return result


def _read_limits(source: Mapping[str, Any]) -> tuple:
    paths = source.get('allowed_paths', ['$'])
    scope = source.get('access_scope', {})
    if (not isinstance(paths, list) or not paths
            or any(not isinstance(path, str) or not path.strip() for path in paths)
            or not isinstance(scope, Mapping)):
        raise ValueError('Plan input requires explicit valid read limits')
    for key in ('actors', 'roles', 'purposes'):
        if key in scope and (not isinstance(scope[key], list) or any(
                not isinstance(value, str) or not value.strip() for value in scope[key])):
            raise ValueError('Plan input scope restrictions must be string lists')
    for key in ('actor', 'role', 'purpose', 'visibility'):
        if key in scope and (not isinstance(scope[key], str) or not scope[key].strip()):
            raise ValueError('Plan input scope restrictions must be nonempty strings')
    return tuple(sorted(set(paths))), dict(scope)


def supplemental_plan_inputs(state_dir: Path, payload: Mapping[str, Any],
                             *, request_id: str) -> list[dict[str, Any]]:
    """Accept only existing, digest-bound sidecars on an authorized redrive.

    A recovery action supplies evidence, not authority to edit the requirement.
    Live project paths must first be snapshotted by the caller's sanctioned
    writer; this action never reads arbitrary host files or parses prose refs.
    """
    rows = payload.get('supplemental_input_refs', [])
    if not isinstance(rows, list):
        raise ValueError('supplemental_input_refs must be a list of sidecar descriptors')
    result = []
    for row in rows:
        if (not isinstance(row, Mapping)
                or not re.fullmatch(r'[0-9a-f]{64}', str(row.get('sha256') or ''))):
            raise ValueError('supplemental input requires an exact sha256 descriptor')
        verified = verify_sidecar_ref(state_dir, dict(row))['descriptor']
        _read_limits(verified)
        result.append({**verified, 'kind': 'plan_supplemental_input',
                       'source_event_id': request_id})
    return result
