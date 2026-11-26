"""Keep references to granted host facts on their authenticated receipt route."""
from __future__ import annotations

import posixpath
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _reference_parts(state_dir: Path, value: str) -> tuple[str, str]:
    if value.startswith('file:'):
        uri = urlsplit(value)
        if uri.netloc not in {'', 'localhost'}:
            return value, value
        value = unquote(uri.path)
    elif ':' in value:
        return value, value  # Other URI schemes are opaque identities.
    if not value:
        return "", ""
    # Lexical aliases only; never open or resolve an untrusted filesystem path.
    return value, posixpath.normpath(posixpath.join(str(state_dir.absolute()), value))


def _reference_keys(state_dir: Path, value: str) -> set[str]:
    return set(_reference_parts(state_dir, value.strip().partition('#')[0])) - {""}


def probe_execution_reference_keys(state_dir: Path, value: str) -> frozenset[str]:
    """Identify the cited execution, preserving exact locators within a shared report."""
    value, separator, locator = value.strip().partition("#")
    if value.startswith("evt-"):
        value = "event:" + value
    _, canonical = _reference_parts(state_dir, value)
    keys = {canonical + separator + locator}
    # Historical Codex receipts cite a bare six-hex chunk. It can also name
    # a local file. Explicit file paths and explicit exec-output IDs stay distinct.
    if not separator and re.fullmatch(r"[0-9a-f]{6}", value):
        keys.add("exec-output:" + value)
    return frozenset(keys)


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
