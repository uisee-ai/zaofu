"""Preserve execution facts across an authorized allowance increase."""
import base64
from dataclasses import asdict
from pathlib import Path

from zf.core.config.host_verification import parse_host_verification
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_host_manifest import digest


def policy_receipt_matches(state_dir: Path, policy, receipt: dict) -> bool:
    current = asdict(policy)
    if 'policy_snapshot' in receipt:
        previous = receipt['policy_snapshot']
        try:
            if not isinstance(previous, dict) or digest(previous) != receipt.get('policy_digest'):
                return False
            parsed = asdict(parse_host_verification(previous))
            return (parsed == {**previous, 'recollect_commands': previous.get('recollect_commands', [])}
                and 0 < previous['max_executions_per_run'] <= current['max_executions_per_run']
                and previous['max_artifact_bytes'] <= current['max_artifact_bytes']
                and previous['max_total_evidence_bytes'] <= current['max_total_evidence_bytes']
                and {k:v for k,v in previous.items() if k not in {
                    'max_executions_per_run', 'recollect_commands', 'max_artifact_bytes', 'max_total_evidence_bytes'}}
                    == {k:v for k,v in current.items() if k not in {
                        'max_executions_per_run', 'recollect_commands', 'max_artifact_bytes', 'max_total_evidence_bytes'}})
        except (ValueError, TypeError, KeyError):
            return False
    if receipt.get('policy_digest') == digest(current):
        return True
    # The prior parser admitted at most 100 executions. Match those finite
    # historical digests exactly; never infer a previous arbitrary policy.
    legacy_match = False
    for limit in range(1, min(current['max_executions_per_run'], 100) + 1):
        previous = {**current, 'max_executions_per_run': limit}
        if receipt.get('policy_digest') == digest(previous):
            return True
        previous.pop('recollect_commands', None)
        if receipt.get('policy_digest') == digest(previous):
            return True
        legacy = {k:v for k,v in previous.items()
                  if k not in {'max_artifact_bytes','max_total_evidence_bytes'}}
        legacy_match = legacy_match or receipt.get('policy_digest') == digest(legacy)
    if not legacy_match:
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
