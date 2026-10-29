"""Read-only parent/child accounting over the event ledger and cost receipts.

Inclusive receipts name the exact usage samples they cover. Missing coverage,
identity or price is unknown, never a zero-cost completed operation.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.runtime.workflow_operation import reduce_workflow_operations

TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_creation_1h_tokens", "cache_read_tokens")


def operation_usage_snapshot(
    events: list[ZfEvent], records: Sequence[Mapping[str, Any]], root_id: str,
) -> dict[str, Any]:
    operations = reduce_workflow_operations(events)
    tree = {root_id}
    while True:
        children = {key for key, row in operations.items() if row.get("parent_operation_id") in tree}
        if children <= tree:
            break
        tree.update(children)
    issues: set[str] = set()
    if root_id not in operations:
        issues.add("parent_operation_missing")
    sources = {event.id: event for event in events if event.type == "agent.usage"}
    run_id = operations.get(root_id, {}).get("workflow_run_id")
    for event in sources.values():
        if run_id and (event.payload.get("workflow_run_id") or event.payload.get("run_id") or event.correlation_id) == run_id and not event.payload.get("operation_id"):
            issues.add("usage_operation_identity_missing:" + event.id)
    samples: dict[str, tuple[ZfEvent, Mapping[str, Any]]] = {}
    for record in records:
        event = sources.get(str(record.get("source_event_id") or ""))
        if event is None or event.payload.get("operation_id") not in tree:
            continue
        sample = str(event.payload.get("usage_sample_id") or event.id)
        if sample in samples:
            if dict(samples[sample][1]) != dict(record):
                issues.add("conflicting_usage_sample")
            continue
        samples[sample] = (event, record)
    observed = {event.payload.get("operation_id") for event, _ in samples.values()}
    for key in tree:
        row = operations.get(key, {})
        if row.get("role_instance") and row.get("dispatch_id") and key not in observed:
            issues.add("operation_usage_missing:" + key)
    for event in sources.values():
        if event.payload.get("operation_id") in tree:
            sample = str(event.payload.get("usage_sample_id") or event.id)
            if sample not in samples:
                issues.add("cost_receipt_missing:" + sample)
    covered: set[str] = set()
    for sample, (event, record) in samples.items():
        payload = event.payload
        scope = payload.get("usage_scope")
        if scope not in {"exclusive", "inclusive"}:
            issues.add("usage_scope_unknown:" + sample)
        if record.get("cost_status") in {"unpriced", "partial"}:
            issues.add("usage_price_unknown:" + sample)
        if scope != "inclusive":
            continue
        refs = payload.get("covered_usage_sample_ids")
        if not isinstance(refs, list) or not refs or payload.get("coverage_complete") is not True:
            issues.add("inclusive_coverage_unknown:" + sample)
            continue
        if any(not isinstance(ref, str) or ref == sample or ref not in samples for ref in refs):
            issues.add("inclusive_coverage_invalid:" + sample)
            continue
        if covered.intersection(refs) or len(set(refs)) != len(refs):
            issues.add("inclusive_coverage_overlapping:" + sample)
        for ref in refs:
            child_event = samples[ref][0]
            child = operations.get(str(child_event.payload.get("operation_id") or ""), {})
            if child.get("parent_operation_id") != payload.get("operation_id") or child_event.payload.get("usage_scope") != "exclusive":
                issues.add("inclusive_coverage_not_direct_child:" + sample)
        for metric in (*TOKEN_KEYS, "cost_usd"):
            if float(record.get(metric) or 0) < sum(float(samples[ref][1].get(metric) or 0) for ref in refs):
                issues.add("inclusive_total_below_children:" + sample)
        covered.update(refs)
    by_operation: dict[str, dict[str, Any]] = {}
    for sample, (event, record) in samples.items():
        cost = float(record.get("cost_usd") or 0)
        tokens = sum(int(record.get(key) or 0) for key in TOKEN_KEYS)
        if not math.isfinite(cost) or cost < 0 or tokens < 0:
            issues.add("invalid_usage:" + sample)
            continue
        row = by_operation.setdefault(str(event.payload["operation_id"]), {
            "reported_usd": 0.0, "reported_tokens": 0, "accounted_usd": 0.0, "accounted_tokens": 0,
            "parent_operation_id": operations.get(str(event.payload["operation_id"]), {}).get("parent_operation_id", ""),
        })
        row["reported_usd"] += cost
        row["reported_tokens"] += tokens
        if sample not in covered:
            row["accounted_usd"] += cost
            row["accounted_tokens"] += tokens
    return {
        "schema_version": "operation-usage-attribution.v1", "root_operation_id": root_id,
        "usage_known": not issues, "issues": sorted(issues), "operations": by_operation,
        "total_usd": sum(row["accounted_usd"] for row in by_operation.values()) if not issues else None,
        "total_tokens": sum(row["accounted_tokens"] for row in by_operation.values()) if not issues else None,
        "usage_sample_ids": sorted(samples),
    }


def apply_parent_budget(snapshot: dict, *, events: list[ZfEvent], records: Sequence[Mapping[str, Any]], root_id: str, state_dir: Path) -> dict:
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    usage = operation_usage_snapshot(events, records, root_id)
    snapshot["parent_usage"] = usage
    proposal = dict(snapshot["proposal"])
    for row in reduce_workflow_operations(events).values():
        if row.get("parent_operation_id") != root_id or row.get("operation_type") != "dynamic_read_only_workflow":
            continue
        request = hydrate_sidecar_ref(state_dir, row["request_ref"]).payload["request"]
        previous = request["budget_snapshot"]["proposal"]
        for key in ("max_cost_usd", "max_tokens"):
            if key in previous:
                proposal[key] = min(proposal.get(key, previous[key]), previous[key])
    snapshot["parent_caps"] = proposal
    for key, metric in (("max_cost_usd", "total_usd"), ("max_tokens", "total_tokens")):
        if key not in proposal:
            continue
        cap = proposal[key]
        if type(cap) not in {int, float} or not math.isfinite(cap) or cap <= 0 or not usage["usage_known"]:
            snapshot.update(usage_known=False, available=False)
        elif usage[metric] >= cap:
            snapshot["available"] = False
    return snapshot
