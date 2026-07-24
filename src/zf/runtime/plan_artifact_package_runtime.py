"""Small caller adapters for plan artifact package admission."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.workflow.flow_metadata import flow_metadata_for
from zf.runtime.plan_admission import emit_plan_admission_cancel
from zf.runtime.plan_artifact_package import (
    admit_plan_artifact_package_for_payload,
)


def admit_task_map_trigger_package(
    runtime: Any,
    event: ZfEvent,
    stages: list[Any],
) -> ZfEvent | None:
    if event.type != "task_map.ready":
        return event
    payload = event.payload if isinstance(event.payload, dict) else {}
    try:
        metadata = flow_metadata_for(runtime.config, payload=payload)
        identity = _admit(
            runtime,
            event=event,
            payload=payload,
            metadata=metadata,
            producer_stage_id=str(payload.get("stage_id") or stages[0].id),
            goal_id=str(
                payload.get("goal_id")
                or payload.get("pdd_id")
                or payload.get("feature_id")
                or ""
            ),
        )
    except Exception as exc:
        emit_plan_admission_cancel(
            runtime,
            trigger_event=event,
            stage_id=stages[0].id,
            trace_id=str(event.correlation_id or event.id),
            pdd_id=runtime._fanout_pdd_id(event),
            feature_id=runtime._fanout_pdd_id(event),
            task_map_ref=str(payload.get("task_map_ref") or ""),
            reason=f"plan artifact package admission failed: {exc}",
        )
        return None
    return replace(event, payload={**payload, **identity}) if identity.get(
        "plan_artifact_package_ref"
    ) else event


def admit_synthesized_plan_package(
    runtime: Any,
    *,
    event: ZfEvent,
    manifest: dict[str, Any],
    stage_id: str,
    trace_id: str,
    success_event: str,
    final_status: str,
    recommendation: str,
    artifact_payload: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    if final_status != "completed" or success_event != "task_map.ready":
        return final_status, recommendation, artifact_payload
    payload = {**artifact_payload, "stage_id": stage_id}
    try:
        metadata = flow_metadata_for(runtime.config, payload=payload)
        identity = _admit(
            runtime,
            event=event,
            payload=payload,
            metadata=metadata,
            producer_stage_id=stage_id,
            goal_id=str(
                manifest.get("pdd_id")
                or manifest.get("feature_id")
                or payload.get("goal_id")
                or ""
            ),
            workflow_run_id=trace_id,
        )
        return final_status, recommendation, {**artifact_payload, **identity}
    except Exception as exc:
        return (
            "failed",
            "reject",
            runtime._contract_failure_payload(
                artifact_payload,
                f"plan artifact package admission failed: {exc}",
            ),
        )


def admit_product_delivery_package(
    runtime: Any,
    *,
    event: ZfEvent,
    task: Any,
    source_refs: dict[str, Any],
    task_map_ref: str,
    source_index_ref: str,
) -> str:
    payload = {
        **(event.payload if isinstance(event.payload, dict) else {}),
        **source_refs,
        "task_map_ref": task_map_ref,
        "source_index_ref": source_index_ref,
        "stage_id": "product-delivery",
    }
    try:
        metadata = flow_metadata_for(runtime.config, payload=payload)
        identity = _admit(
            runtime,
            event=event,
            payload=payload,
            metadata=metadata,
            producer_stage_id=str(
                payload.get("producer_stage_id") or "product-delivery"
            ),
            goal_id=str(
                payload.get("goal_id")
                or payload.get("pdd_id")
                or payload.get("feature_id")
                or task.id
            ),
        )
    except Exception as exc:
        return str(exc)
    source_refs.update({
        key: value for key, value in identity.items() if value not in ("", None)
    })
    return ""


def _admit(
    runtime: Any,
    *,
    event: ZfEvent,
    payload: dict[str, Any],
    metadata: dict[str, Any],
    producer_stage_id: str,
    goal_id: str,
    workflow_run_id: str = "",
) -> dict[str, Any]:
    run_id = str(
        workflow_run_id
        or payload.get("workflow_run_id")
        or payload.get("trace_id")
        or event.correlation_id
        or ""
    )
    return admit_plan_artifact_package_for_payload(
        state_dir=runtime.state_dir,
        project_root=runtime.project_root,
        event_writer=runtime.event_writer,
        events=runtime.event_log.read_all(),
        payload=payload,
        workflow_run_id=run_id,
        flow_kind=str(metadata.get("flow_kind") or ""),
        producer_stage_id=producer_stage_id,
        goal_id=goal_id,
        metadata=metadata,
        source_event_id=event.id,
        correlation_id=str(event.correlation_id or run_id),
    )


__all__ = [
    "admit_product_delivery_package",
    "admit_synthesized_plan_package",
    "admit_task_map_trigger_package",
]
