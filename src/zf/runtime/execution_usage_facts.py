"""Read-only usage evidence for semantic execution planning; never a budget gate."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from zf.core.cost.tracker import CostTracker
from zf.core.events.factory import event_log_from_project

TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens",
                "cache_creation_tokens", "cache_creation_1h_tokens")


def execution_usage_facts(state_dir: Path, *, workflow_run_id: str = "") -> dict[str, Any]:
    """Use canonical deltas, never sum cumulative provider transcript samples.

    Unbound receipts remain visible. Role names and timestamps cannot prove a
    run, stage or retry, so missing attribution is never guessed.
    """
    events = {event.id: event for event in event_log_from_project(state_dir).read_all()}
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    unbound = 0
    for entry in CostTracker(state_dir / "cost.jsonl").usage_records():
        event = events.get(str(entry.get("source_event_id") or ""))
        identity = event.payload if event is not None else {}
        run_id = str(identity.get("workflow_run_id") or identity.get("run_id") or "")
        if not run_id:
            unbound += 1
        if workflow_run_id and workflow_run_id != run_id:
            continue
        key = (run_id, str(identity.get("stage_id") or ""),
               str(identity.get("operation_id") or ""),
               str(identity.get("active_attempt_id") or identity.get("attempt_id") or ""),
               str(entry.get("instance_id") or entry.get("role") or ""))
        groups.setdefault(key, []).append(entry)
    rows = []
    for key, entries in sorted(groups.items()):
        totals = {name: sum(int(e.get(name) or 0) for e in entries) for name in TOKEN_FIELDS}
        rows.append({
            **dict(zip(("workflow_run_id", "stage_id", "operation_id", "attempt_id", "role_instance"), key)),
            **totals, "total_tokens": sum(totals.values()), "sample_count": len(entries),
            "estimated_usd": _money(entries, "estimated_cost_usd"),
            "provider_reported_usd": _money(entries, "provider_reported_cost_usd"),
            "billed_usd": _money(entries, "billed_cost_usd"),
            "unpriced_samples": sum(e.get("cost_status") in {"unpriced", "partial"} for e in entries),
            "source_event_ids": list(dict.fromkeys(str(e.get("source_event_id") or "") for e in entries)),
        })
    return {
        "schema_version": "execution-usage-facts.v1",
        "scope": "run" if workflow_run_id else "project_history",
        "workflow_run_id": workflow_run_id, "available": bool(rows),
        "unattributed_project_samples": unbound, "groups": rows,
        "limitations": [
            "Only admitted cost deltas are included; missing provider telemetry is not zero usage.",
            "Missing stage/attempt identity remains unknown; retries are not inferred from role names.",
            "Null money means incomplete evidence, not free execution; estimates are not invoices.",
            "Historical consumption does not predict remaining work or authorize budget changes.",
        ],
    }


def _money(entries: list[dict[str, Any]], field: str) -> float | None:
    if any(entry.get(field) is None for entry in entries):
        return None
    return float(sum((Decimal(str(entry[field])) for entry in entries), Decimal(0)))
