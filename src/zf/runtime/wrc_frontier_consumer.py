"""WRC owner facade for the normal Task Pipeline frontier.

The facade is intentionally small.  Reconciliation, leases, capacity and
provider delivery remain in the existing deterministic Task Pipeline runtime;
this module makes the owner boundary explicit and prevents the legacy
dispatcher from acquiring the same frontier when WRC is configured.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from zf.core.events.model import ZfEvent
from zf.runtime.task_pipeline_contexts import task_pipeline_managed_task_ids
from zf.runtime.task_pipeline_reconciler import task_pipeline_any_blocking
from zf.runtime.workflow_frontier_owner import normal_frontier_owner


WRC_FRONTIER_RECONCILED_EVENT = "workflow.normal_frontier.wrc.reconciled"
WRC_FRONTIER_OWNER = "wrc"


def consume_normal_frontier(
    runtime: Any,
    *,
    candidate_task_ids: Iterable[str] = (),
) -> tuple[list[Any], set[str]]:
    """Consume one WRC-owned ready frontier through the existing reconciler.

    The returned set is the legacy-dispatch fence.  It deliberately includes
    every candidate when WRC owns the frontier, including candidates that have
    not reached generation admission yet; such candidates are diagnosed and
    remain idle until the canonical admission event exists.
    """

    if normal_frontier_owner(runtime.config) != WRC_FRONTIER_OWNER:
        return [], set()

    candidates = {
        str(task_id).strip()
        for task_id in candidate_task_ids
        if str(task_id).strip()
    }
    managed = task_pipeline_managed_task_ids(runtime)
    managed.update(candidates)
    if not task_pipeline_any_blocking(runtime.config):
        _emit_owner_blocked_once(
            runtime,
            reason="wrc_requires_blocking_task_pipeline",
            task_ids=sorted(candidates),
        )
        return [], managed

    admitted = task_pipeline_managed_task_ids(runtime)
    unadmitted = sorted(candidates - admitted)
    if unadmitted:
        _emit_owner_blocked_once(
            runtime,
            reason="task_pipeline_generation_not_admitted",
            task_ids=unadmitted,
        )

    # Import lazily to keep the owner facade below the runtime implementation
    # and avoid a module import cycle.
    from zf.runtime.task_pipeline_runtime import reconcile_task_pipeline_runtime

    decisions = reconcile_task_pipeline_runtime(runtime)
    _emit_reconciled(runtime, candidates=candidates, managed=managed, decisions=decisions)
    return decisions, managed


def _emit_reconciled(
    runtime: Any,
    *,
    candidates: set[str],
    managed: set[str],
    decisions: list[Any],
) -> None:
    payload = {
        "schema_version": "wrc-frontier-consumer.v1",
        "owner": WRC_FRONTIER_OWNER,
        "candidate_task_ids": sorted(candidates),
        "managed_task_ids": sorted(managed),
        "decision_count": len(decisions),
    }
    payload["input_digest"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    for event in reversed(runtime.event_log.read_all()):
        if event.type != WRC_FRONTIER_RECONCILED_EVENT:
            continue
        existing = event.payload if isinstance(event.payload, dict) else {}
        if existing.get("input_digest") == payload["input_digest"]:
            return
        break
    runtime.event_writer.append(ZfEvent(
        type=WRC_FRONTIER_RECONCILED_EVENT,
        actor="wrc",
        origin="kernel",
        payload=payload,
    ))


def _emit_owner_blocked_once(
    runtime: Any,
    *,
    reason: str,
    task_ids: list[str],
) -> None:
    payload = {
        "owner": WRC_FRONTIER_OWNER,
        "reason": reason,
        "task_ids": list(task_ids),
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    payload["fingerprint"] = fingerprint
    for event in reversed(runtime.event_log.read_all()):
        if event.type != "workflow.normal_frontier.owner_blocked":
            continue
        existing = event.payload if isinstance(event.payload, dict) else {}
        if existing.get("fingerprint") == fingerprint:
            return
        break
    runtime.event_writer.append(ZfEvent(
        type="workflow.normal_frontier.owner_blocked",
        actor="wrc",
        origin="kernel",
        payload=payload,
    ))


__all__ = [
    "WRC_FRONTIER_OWNER",
    "WRC_FRONTIER_RECONCILED_EVENT",
    "consume_normal_frontier",
]
