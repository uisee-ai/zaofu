"""Inspect existing read evidence without sealing ledgers or writing witnesses."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from zf.runtime.artifact_output_delivery import observe_artifact_delivery
from zf.runtime.artifact_read_ledger import active_read_ledger_path
from zf.runtime.artifact_read_ledger_validation import _parse_ledger, validate_read_rows
from zf.runtime.input_delivery_contract import pinned_delivery_policy


def input_delivery_preflight(state_dir: Path, event_log: Any, operation: dict,
                              semantic: dict) -> list[dict[str, str]]:
    try:
        policy = pinned_delivery_policy(state_dir, event_log.read_all(), operation, {})
        if not policy or semantic.get("execution_status") == "failed":
            # Canonical admission still checks that this really is a bound
            # input failure, not a successful result disguised as one.
            return []
        return preflight_required_reads(state_dir, policy)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [{"field": "input_consumption.delivery", "code": "input_delivery_unavailable",
                 "message": str(exc)}]


def preflight_required_reads(state_dir: Path, policy: dict) -> list[dict[str, str]]:
    """Inspect the active attempt without sealing a ledger or consuming its submission."""
    try:
        attempt = policy["attempt_id"]
        active = active_read_ledger_path(state_dir, attempt)
        paths = sorted(active.parent.glob("read-ledger-*.jsonl"))
        if active.exists():
            paths.append(active)
        rows = []
        for path in paths:
            raw = path.read_bytes()
            if path != active and not path.stem.endswith(hashlib.sha256(raw).hexdigest()[:16]):
                raise ValueError("sealed input read ledger digest mismatch")
            rows.extend(_parse_ledger(raw, expected_attempt_id=attempt))
        observed = observe_artifact_delivery(state_dir, rows, persist_observations=False)
        return validate_read_rows(state_dir, policy=policy, rows=observed,
                                  allow_ephemeral_observations=True)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [{"field": "input_consumption.delivery", "code": "input_delivery_unavailable", "message": str(exc)}]
