"""Project scoped approval/dispatch ledger facts into the Judge's read set."""

from __future__ import annotations

from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.run_scope import run_aliases


def plan_lifecycle_evidence(
    events: list[ZfEvent],
    *,
    workflow_run_id: str,
    task_map_generation: str,
    package_identity: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Preserve observed facts and ledger order, without deciding acceptance.

    Operator decisions may carry only a plan_id; the preceding package-bound
    approval request provides their scope. OA adoption is not owner approval.
    Missing evidence stays missing, including legacy plans without a package.
    """
    digest = str(package_identity.get("plan_artifact_package_digest") or "")
    if not digest:
        return []
    aliases = run_aliases(events)
    run = aliases.get(workflow_run_id, workflow_run_id)
    plans: set[str] = set()
    rows: list[dict[str, Any]] = []
    for position, event in enumerate(events):
        body = event.payload if isinstance(event.payload, dict) else {}
        identities = [str(body.get(key) or "") for key in (
            "workflow_run_id", "run_id", "trace_id",
        )]
        if not any(identities):
            identities = [str(event.correlation_id or "")]
        identities = [aliases.get(value, value) for value in identities if value]
        plan_id = str(body.get("plan_id") or "")
        if event.type in {"plan.approved", "plan.rejected"}:
            if (
                plan_id not in plans
                or body.get("semantic_control")
                or any(value != run for value in identities)
            ):
                continue
            selected = dict(body)
        else:
            if not identities or any(value != run for value in identities):
                continue
            if str(body.get("plan_artifact_package_digest") or "") != digest:
                continue
            if event.type == "plan.approval.requested" and plan_id:
                plans.add(plan_id)
                selected = dict(body)
            elif event.type == "task.pipeline.stage.dispatched":
                if str(body.get("task_map_generation") or "") != task_map_generation:
                    continue
                selected = {key: body[key] for key in (
                    "workflow_run_id", "task_id", "task_map_generation",
                    "plan_artifact_package_id", "plan_artifact_package_digest",
                    "task_pipeline_stage", "stage_id", "role_instance",
                    "attempt_id", "dispatch_id", "operation_id",
                ) if key in body}
            else:
                continue
        rows.append({
            "event_id": event.id,
            "event_type": event.type,
            "ledger_position": position,
            "ts": event.ts,
            "actor": event.actor,
            "origin": event.origin,
            "causation_id": event.causation_id,
            "payload": selected,
        })
    return rows
