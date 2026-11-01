"""Project scoped approval/dispatch ledger facts into the Judge's read set."""

from __future__ import annotations

from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.run_scope import run_aliases


def workflow_lifecycle_evidence(
    events: list[ZfEvent], *, workflow_run_id: str,
) -> list[dict[str, Any]]:
    """Bind observed approval to the invoked Request revision, never infer it."""
    aliases = run_aliases(events)
    run = aliases.get(workflow_run_id, workflow_run_id)

    def scoped(body: Mapping[str, Any], *, required: bool = False) -> bool:
        values = [aliases.get(str(body[key]), str(body[key])) for key in (
            "workflow_run_id", "run_id", "trace_id",
        ) if body.get(key)]
        return (bool(values) or not required) and all(value == run for value in values)

    invocation = next((
        (position, event) for position, event in enumerate(events)
        if event.type == "workflow.invoke.requested" and scoped(event.payload, required=True)
    ), None)
    if invocation is None:
        return []
    position, invoked = invocation
    binding = invoked.payload
    if not all(binding.get(key) for key in (
        "request_id", "request_revision", "requirement_spec_digest",
    )):
        return []
    rows = []
    for index, event in enumerate(events[:position + 1]):
        body = event.payload
        if event.type not in {
            "workflow.request.approved", "workflow.submit.requested", "workflow.invoke.requested",
        } or not scoped(body):
            continue
        revision_key = "revision" if event.type == "workflow.request.approved" else "request_revision"
        if (body.get("request_id") != binding["request_id"]
                or body.get(revision_key) != binding["request_revision"]
                or body.get("requirement_spec_digest") != binding["requirement_spec_digest"]):
            continue
        selected = {key: body[key] for key in (
            "request_id", "workflow_run_id", "run_id", "revision", "request_revision", "status",
            "requirement_spec_ref", "requirement_spec_digest", "workflow_proposal_ref",
            "workflow_proposal_digest", "effective_config_ref", "effective_config_digest",
            "requested_by", "origin_binding",
        ) if key in body}
        rows.append({
            "event_id": event.id, "event_type": event.type, "ledger_position": index,
            "ts": event.ts, "actor": event.actor, "origin": event.origin,
            "causation_id": event.causation_id, "payload": selected,
        })
    return rows


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
