"""Bounded read-only recovery view of the same frozen delivery requirement."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.runtime.call_result_envelope import canonical_json_sha256
from zf.runtime.artifact_output_delivery import observe_artifact_delivery
from zf.runtime.artifact_read_ledger_validation import required_read_satisfied
from zf.runtime.input_delivery_contract import pinned_delivery_policy
from zf.runtime.input_delivery_evidence import delivered_rows
from zf.runtime.workflow_operation import reduce_workflow_operations


def input_delivery_recovery(state_dir: Path, events: list, identity: dict[str, str],
                            attempt_id: str, reads: list[dict[str, Any]], *, cursor: str = "") -> dict[str, Any]:
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
        missing = [_next_gap(row, delivered) for row in missing]
        page = delivery_gap_page(missing, cursor=cursor, binding={
            "attempt": attempt_id, "actor": identity["actor"], "operation": operation["operation_id"],
            "evidence": policy["delivery_evidence"],
        })
        return {"mode": "observed", "status": "incomplete" if missing else "complete",
                **page, "scope": "historical_tool_output", "current_memory": "unknown"}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"status": "unavailable", "missing_count": None, "reason": str(exc)[:120]}

def _next_gap(required, delivered):
    matching = sorted((r["page_start"], r["page_end"], r["page_total"]) for r in delivered
                      if "page_start" in r and all(r.get(k) == required.get(k)
                          for k in ("source_id", "artifact_id", "artifact_sha256", "json_path")))
    if not matching:
        return required
    start = 0
    for first, end, total in matching:
        if start < first:
            return {**required, "next_missing_range": [start, first]}
        start = max(start, end)
    return {**required, "next_missing_range": [start, total]} if start < total else required


def delivery_gap_page(rows: list[dict], *, cursor: str, binding: dict) -> dict:
    digest = canonical_json_sha256([binding, rows])
    start = 0
    if cursor:
        key, sep, offset = cursor.partition(".")
        if (len(cursor) > 80 or key != digest or not sep or not offset.isascii()
                or not offset.isdecimal() or not 0 < int(offset) < len(rows)):
            raise ValueError("stale or invalid delivery gap cursor; restart this query")
        start = int(offset)
    end = min(start + 4, len(rows))
    return {"missing_count": len(rows), "missing_list_complete": start == 0 and end == len(rows),
            "missing": [{k: row[k] for k in ("source_id", "artifact_id", "json_path", "next_missing_range") if k in row}
                        for row in rows[start:end]],
            "next_gap_cursor": f"{digest}.{end}" if end < len(rows) else None}
