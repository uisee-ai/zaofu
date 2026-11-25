"""Inspect existing read evidence without sealing ledgers or writing witnesses."""
from __future__ import annotations

import hashlib
import shlex
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.artifact_output_delivery import observe_artifact_delivery
from zf.runtime.artifact_read_ledger import active_read_ledger_path
from zf.runtime.artifact_read_ledger_validation import _parse_ledger, validate_read_rows
from zf.runtime.call_result_adapters import ControlResultAdapterRegistry
from zf.runtime.cli_command import zf_cli_cmd
from zf.runtime.input_delivery_contract import pinned_delivery_policy
from zf.runtime.task_input_failure import permits_incomplete_input


def result_readiness_preflight(state_dir: Path, event_log: Any, operation: dict,
                               request: Mapping[str, Any], semantic: dict,
                               registry: ControlResultAdapterRegistry) -> list[dict[str, str]]:
    try:
        policy = pinned_delivery_policy(state_dir, event_log.read_all(), operation, fallback=None)
        if not policy:
            return []
        issues = preflight_required_reads(state_dir, policy)
        missing = {"required_read_missing", "required_input_delivery_missing"}
        if not any(issue["code"] in missing for issue in issues):
            return issues

        # Only the final gate's normalized reader-failure disposition can omit reads.
        profile = registry.profile(request["output_profile_id"], request["output_profile_revision"])
        identity = {**(request.get("result_identity") or {}), "output_profile_id": profile.profile_id,
                    "output_profile_revision": profile.revision}
        failed = str(semantic.get("execution_status") or "").lower() == "failed"
        event = ZfEvent(type=request["canonical_failure_event" if failed else "canonical_success_event"],
                        actor=request.get("role_instance") or "",
                        payload={**identity, profile.semantic_field: {**semantic, **identity}})
        adapter = registry.adapter_for(event)
        if adapter is not None and adapter.adapter_id == profile.adapter_id:
            normalized, _ = adapter.normalize(event)
            if permits_incomplete_input(profile.schema_version, normalized):
                return [issue for issue in issues if issue["code"] not in missing]
        return issues
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [{"field": "input_consumption.delivery", "code": "input_delivery_unavailable",
                 "message": str(exc)}]


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
        issues = validate_read_rows(state_dir, policy=policy, rows=observed,
                                    allow_ephemeral_observations=True)
        for issue in issues:
            if issue["code"] == "required_read_missing":
                command = shlex.join(["artifact", "read", "--attempt", attempt, "--required"])
                issue["message"] += (
                    f". Consume the required inputs with {zf_cli_cmd()} {command}. "
                    "Follow --cursor <next_cursor> on that command until next_cursor is null, then revalidate."
                )
                break
        return issues
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [{"field": "input_consumption.delivery", "code": "input_delivery_unavailable", "message": str(exc)}]
