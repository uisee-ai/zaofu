"""Translate admitted exact-target OA rework into the existing v4 retry input."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from zf.core.events.model import ZfEvent


FAULT = "task_pipeline_oa_rework"


def oa_impl_rework_requests(
    *,
    events: Sequence[ZfEvent],
    operations: Mapping[str, Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Only applied OA directives for a settled current Impl may create a retry.

    Active successors and exhausted generation budgets remain WRC concerns;
    an OA event never directly sends a legacy worker briefing.
    """
    applied = {
        str(event_id)
        for event in events
        if event.type == "orchestrator.semantic.decision.applied"
        and event.origin == "kernel"
        and event.payload.get("applied") is True
        for event_id in event.payload.get("semantic_rework_event_ids", [])
    }
    requests = {}
    for event in events:
        if (
            event.type != "orchestrator.semantic.rework.requested"
            or event.id not in applied
        ):
            continue
        p = event.payload
        task_id = str(event.task_id or "")
        context = contexts.get(task_id)
        if context is None or p.get("semantic_action") != "rework":
            continue
        if any(
            str(p.get(k) or "") != str(context.get(k) or "")
            for k in (
                "workflow_run_id",
                "task_map_generation",
                "plan_artifact_package_ref",
                "plan_artifact_package_digest",
            )
        ):
            continue
        rows = [
            row
            for row in operations.values()
            if row.get("task_id") == task_id
            and all(
                row.get(k) == context.get(k)
                for k in ("workflow_run_id", "task_map_generation")
            )
        ]
        if any(
            row.get("status") in {"requested", "reserved", "running", "suspended"}
            for row in rows
        ):
            continue
        impls = [row for row in rows if row.get("task_pipeline_stage") == "impl"]
        if not impls:
            continue
        impl = max(impls, key=lambda row: int(row.get("operation_generation") or 0))
        if (
            impl.get("status") != "settled"
            or p.get("target_stage_id") != "impl"
            or p.get("target_role_instance") != impl.get("role_instance")
            or not p.get("target_attempt_id")
            or p.get("target_attempt_id")
            not in {impl.get("dispatch_id"), impl.get("active_attempt_id")}
        ):
            continue
        requests[task_id] = {
            "fault": FAULT,
            "event_id": event.id,
            "source_event_id": str(event.causation_id or ""),
            "operation_id": impl["operation_id"],
            "operation_generation": int(impl["operation_generation"]),
            "reason": str(
                p.get("reason") or "OA requested targeted implementation rework"
            ),
            "expected_action": "; ".join(
                str(item) for item in p.get("required_actions", [])
            ),
        }
    return requests
