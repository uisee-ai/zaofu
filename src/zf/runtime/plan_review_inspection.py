"""Operator metadata inspection. Never issues credentials or read receipts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zf.runtime.artifact_input_index import load_attempt_source_manifest
from zf.runtime.artifact_read_ledger import active_read_ledger_path
from zf.runtime.artifact_read_ledger_validation import required_read_satisfied
from zf.runtime.artifact_read_requirements import required_reads_for_attempt
from zf.runtime.artifact_source_versions import read_source_version
from zf.runtime.plan_review_input_diagnostics import review_diagnostics
from zf.runtime.sidecar_refs import sidecar_path
from zf.runtime.workflow_operation import reduce_workflow_operations


def compare_task_scope(old: dict, new: dict, requested: list[str]) -> dict[str, Any]:
    def tasks(value):
        rows = value.get("tasks")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("task map tasks must be a list")
        result = {r.get("task_id") or r.get("id"): r for r in rows}
        if len(result) != len(rows) or any(not isinstance(k, str) or not k for k in result):
            raise ValueError("task identities missing or duplicated")
        return result

    before, after = tasks(old), tasks(new)
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    outside = sorted(set(changed) - set(requested))
    global_fields = sorted(k for k in old.keys() | new.keys() if k != "tasks" and old.get(k) != new.get(k))
    return {"status": "compared", "requested_task_count": len(requested),
            "changed_task_count": len(changed), "outside_requested_count": len(outside),
            "outside_requested_sample": outside[:20], "global_changed_fields": global_fields[:20],
            "global_changed_count": len(global_fields),
            "sample_truncated": len(outside) > 20 or len(global_fields) > 20,
            "scope_certificate": "not_established_by_structural_comparison"}


def review_scope_diagnostics(state_dir: Path, manifest: dict) -> dict[str, Any]:
    sources = manifest.get("sources", [])

    def read(predicate):
        matches = [s for s in sources if predicate(s)]
        if len(matches) != 1:
            raise ValueError("scope source missing or ambiguous")
        source = matches[0]
        raw, _ = read_source_version(state_dir, source, path=sidecar_path(state_dir, source["ref"]))
        return json.loads(raw)

    try:
        old = read(lambda s: s.get("source_id") == "baseline-plan-port-task_map")
        new = read(lambda s: str(s.get("source_id", "")).startswith("child-artifact-")
                   and s.get("artifact_id") == "task_map.json")
        context = read(lambda s: s.get("source_id") == "plan-rework-context")
        requested = context.get("task_ids")
        if not isinstance(requested, list) or not all(isinstance(x, str) for x in requested):
            raise ValueError("explicit task scope unavailable")
        return compare_task_scope(old, new, requested)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {"status": "unknown", "reason": "missing_invalid_or_ambiguous_pinned_scope"}


def inspect_review(state_dir: Path, attempt_id: str, events: list, *, config=None) -> dict[str, Any]:
    manifest = load_attempt_source_manifest(state_dir, attempt_id)
    operations = reduce_workflow_operations(events)
    candidates = [o for o in operations.values() if attempt_id in {
        o.get("active_attempt_id"), o.get("dispatch_id")}]
    operation = candidates[0] if len(candidates) == 1 else {}
    required = required_reads_for_attempt(events, attempt_id, state_dir=state_dir,
                                          operation_id=operation.get("operation_id", ""))
    ledger = active_read_ledger_path(state_dir, attempt_id)
    rows = []
    if ledger.exists():
        for line in ledger.read_text().splitlines():
            row = json.loads(line)
            if row.get("schema_version") != "artifact-read.v1" or row.get("attempt_id") != attempt_id:
                raise ValueError("read ledger identity mismatch")
            rows.append(row)
    satisfied = sum(required_read_satisfied(rows, r) for r in required)
    # Compare pinned rows, not a newly invented read policy.
    from zf.runtime.artifact_read_requirements import build_canonical_required_reads
    full = build_canonical_required_reads(manifest, output_profile_id="plan-synth")
    diagnostic = review_diagnostics(manifest, before=full, after=required, explicit=False)
    if config is not None:
        diagnostic["budget_config_basis"] = "current_operator_config"
        from zf.runtime.plan_review_input_budget import check_review_input_budget, PlanReviewInputBudgetExceeded
        try:
            check_review_input_budget(config, operation.get("parent_stage_id") or operation.get("stage_id", ""), diagnostic)
        except PlanReviewInputBudgetExceeded:
            pass
    diagnostic["scope"] = review_scope_diagnostics(state_dir, manifest)
    sources = sorted(manifest.get("sources", []), key=lambda s: sum(
        r.get("selected_bytes", 0) for r in s.get("read_selections", [])), reverse=True)
    available = {r["selected_sha256"]: r["selected_bytes"] for s in sources
                 for r in s.get("read_selections", [])}
    return {"schema_version": "plan-review-inspection.v1", "attempt_id": attempt_id,
            "operation_status": operation.get("status", "unknown"), "diagnostic": diagnostic,
            "available_distinct_selected_bytes": sum(available.values()),
            "largest_sources": [{"source_id": s["source_id"], "artifact_id": s["artifact_id"],
                "required": any(r.get("source_id") == s["source_id"] for r in required),
                "selected_bytes": sum(r.get("selected_bytes", 0) for r in s.get("read_selections", []))}
                for s in sources[:5]],
            "read_progress": {"required": len(required), "covered": satisfied,
                "last_read_at": max((r.get("read_at", "") for r in rows), default=""),
                "ledger_rows": len(rows), "proves_understanding": False},
            "read_only": True}
