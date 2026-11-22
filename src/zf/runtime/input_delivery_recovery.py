"""Bounded read-only recovery view of the same frozen delivery requirement."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.runtime.artifact_output_delivery import observe_artifact_delivery
from zf.runtime.artifact_read_ledger_validation import required_read_satisfied
from zf.runtime.input_delivery_contract import pinned_delivery_policy
from zf.runtime.input_delivery_evidence import delivered_rows
from zf.runtime.workflow_operation import reduce_workflow_operations


def input_delivery_recovery(state_dir: Path, events: list, identity: dict[str, str],
                            attempt_id: str, reads: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        operation = reduce_workflow_operations(events).get(identity.get("operation_id"), {})
        policy = pinned_delivery_policy(state_dir, events, operation, {})
        if not policy:
            return {}
        if (operation.get("role_instance") != identity.get("actor")
                or policy.get("attempt_id") != attempt_id):
            raise ValueError("recovery consumer does not match the delivery policy")
        observed = observe_artifact_delivery(state_dir, reads, persist_observations=False)
        delivered = delivered_rows(state_dir, policy, observed, allow_ephemeral_observations=True)
        missing = [row for row in policy["required_reads"] if not required_read_satisfied(delivered, row)]
        return {"mode": "observed", "status": "incomplete" if missing else "complete",
                "missing_count": len(missing), "missing_list_complete": len(missing) <= 4,
                "missing": [{k: row[k] for k in ("source_id", "artifact_id", "json_path")} for row in missing[:4]],
                "scope": "historical_tool_output", "current_memory": "unknown"}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"status": "unavailable", "missing_count": None, "reason": str(exc)[:120]}
