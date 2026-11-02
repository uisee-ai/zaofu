"""Input freshness from the existing Plan Package reducer, never a second owner."""
from __future__ import annotations
from typing import Any, Mapping, Sequence

from zf.runtime.plan_artifact_package import reduce_plan_artifact_packages


def input_lifecycle(manifest: Mapping[str, Any], events: Sequence[Any]) -> dict[str, Any]:
    ref = str(manifest.get("plan_artifact_package_ref") or "")
    digest = str(manifest.get("plan_artifact_package_digest") or "")
    relevant = [event for event in events if event.type in {
        "plan.artifact_package.admitted", "plan.artifact_package.rejected", "plan.artifact_package.superseded", "fanout.cancelled"
    } and event.payload.get("workflow_run_id") == manifest.get("workflow_run_id")]
    result = {"status": "pinned", "authorizes_rebind": False, "read_binding": "immutable_attempt",
              "source_watermark": str(relevant[-1].id) if relevant else "", "requires_rebind": False}
    if not ref or not digest:
        return {**result, "reason": "no_plan_package_binding"}
    try:
        reduced = reduce_plan_artifact_packages(events, workflow_run_id=str(manifest.get("workflow_run_id") or ""))
    except ValueError:
        return {**result, "status": "unknown", "reason": "package_projection_conflict", "requires_rebind": True}
    def matches(row):
        return row.get("package_ref") == ref and (row.get("package_digest") or row.get("package_sha256")) == digest
    current = reduced.get("current") or {}
    if matches(current):
        return {**result, "status": "current"}
    if any(matches(row) for row in reduced.get("history", [])):
        return {**result, "status": "superseded", "requires_rebind": True}
    if any(matches(row) for row in reduced.get("rejected", [])):
        return {**result, "status": "rejected", "requires_rebind": True}
    return {**result, "status": "unadmitted", "requires_rebind": True}
