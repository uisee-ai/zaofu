"""Mechanical Plan approval waits shared by idle and recovery consumers."""

from collections.abc import Iterable, Mapping
import hashlib
import json
from typing import Any

from zf.core.events.model import ZfEvent


def plan_approval_fingerprint(
    *, stage_id: str, pdd_id: str, trace_id: str,
    payload: Mapping[str, Any], task_items: list[dict[str, Any]],
) -> str:
    """Deduplicate one bound candidate, never just its unchanged task IDs.

    The version prefix deliberately does not match legacy task-ID-only hashes:
    those events cannot prove that the same candidate was already reviewed.
    """
    binding = {
        key: payload[key] for key in (
            "plan_artifact_package_digest", "task_map_generation", "task_map_digest",
            "requirement_spec_digest", "effective_config_digest",
            "workflow_proposal_digest", "run_contract_digest", "plan_artifact_digest",
        ) if payload.get(key)
    }
    identity = {
        "stage_id": stage_id,
        "pdd_id": pdd_id,
        "workflow_run_id": str(payload.get("workflow_run_id") or payload.get("run_id") or trace_id),
        "binding": binding,
        "tasks": sorted(task_items, key=lambda item: str(item.get("task_id") or "")),
    }
    raw = json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "plan-approval.v2:" + hashlib.sha256(raw.encode()).hexdigest()


def has_pending_plan_approval(
    events: Iterable[ZfEvent], *, workflow_run_id: str = "",
) -> bool:
    pending: dict[str, set[str]] = {}
    for event in events:
        if event.type not in {"plan.approval.requested", "plan.approved", "plan.rejected"}:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        plan_id = str(payload.get("plan_id") or "")
        if not plan_id:
            continue
        if event.type != "plan.approval.requested":
            pending.pop(plan_id, None)
            continue
        pending[plan_id] = {
            str(payload.get("workflow_run_id") or ""),
            str(payload.get("run_id") or ""),
            str(payload.get("trace_id") or ""),
            str(event.correlation_id or ""),
        } - {""}
    return any(not workflow_run_id or workflow_run_id in scope for scope in pending.values())
