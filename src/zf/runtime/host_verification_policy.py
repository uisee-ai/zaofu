"""Compatibility for receipts predating configurable capture bounds."""
import base64
from dataclasses import asdict
from pathlib import Path

from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_host_manifest import digest


def policy_receipt_matches(state_dir: Path, policy, receipt: dict) -> bool:
    current = asdict(policy)
    if receipt.get('policy_digest') == digest(current):
        return True
    legacy = {k:v for k,v in current.items()
        if k not in {'max_artifact_bytes','max_total_evidence_bytes'}}
    if receipt.get('policy_digest') != digest(legacy):
        return False
    # Old capture had only a per-file bound. Prove actual retained bytes fit
    # today's policy; do not assume a historical aggregate size bound existed.
    try:
        hydrated = hydrate_sidecar_ref(state_dir, receipt['evidence_ref'])
        if not hydrated.ok or not isinstance(hydrated.payload.get('files'), list):
            return False
        total = 0
        for item in hydrated.payload['files']:
            text = item['base64']
            if len(text) > ((policy.max_artifact_bytes + 2) // 3) * 4:
                return False
            size = len(base64.b64decode(text, validate=True))
            total += size
            if size > policy.max_artifact_bytes or total > policy.max_total_evidence_bytes:
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
