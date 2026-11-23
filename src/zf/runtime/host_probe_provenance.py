"""Keep references to granted host facts on their authenticated receipt route."""
from __future__ import annotations

import posixpath
from pathlib import Path
from urllib.parse import unquote, urlsplit

from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _reference_keys(state_dir: Path, value: str) -> set[str]:
    value = value.strip().split('#', 1)[0]
    if value.startswith('file:'):
        uri = urlsplit(value)
        if uri.netloc not in {'', 'localhost'}:
            return {value}
        value = unquote(uri.path)
    elif ':' in value:
        return {value}  # Event IDs and other URI schemes are opaque identities.
    if not value:
        return set()
    # Lexical aliases only; never open or resolve an untrusted filesystem path.
    return {value, posixpath.normpath(posixpath.join(str(state_dir.absolute()), value))}


def reject_host_backed_probes(state_dir: Path, result: dict, request: dict) -> None:
    """A host fact may support acceptance, but cannot become a fresh probe."""
    probes = [p for p in result.get('probe_receipts', [])
              if isinstance(p, dict) and p.get('status') == 'passed']
    ref = request.get('candidate_host_evidence_ref')
    if not probes or not ref:
        return
    binding = hydrate_sidecar_ref(state_dir, ref).payload
    keys = _reference_keys(state_dir, ref['ref'])
    keys.update(_reference_keys(state_dir, 'candidate-host-execution'))
    for source in binding['input_refs']:
        for field in ('ref', 'source_id'):
            if source.get(field):
                keys.update(_reference_keys(state_dir, source[field]))
        if source.get('source_event_id'):
            keys.update({source['source_event_id'], 'event:' + source['source_event_id']})
    for receipt in binding['receipts']:
        keys.update({receipt['receipt_id'], 'event:' + receipt['receipt_id']})
    for probe in probes:
        if any(isinstance(value, str) and _reference_keys(state_dir, value) & keys
               for value in probe.get('evidence_refs', [])):
            raise ValueError(
                'probe_receipts cites bound host evidence; use host_execution_receipt_ids '
                'to validate the original receipt instead of relabelling it as a fresh probe'
            )
