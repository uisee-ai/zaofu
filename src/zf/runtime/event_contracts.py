"""Workflow event producer/consumer contract diagnostics.

This module is intentionally read-only. It audits the event types that a
config can produce and checks whether actionable events have a deterministic
consumer contract in ``event_problem_registry``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from zf.core.events.model import ZfEvent
from zf.runtime.event_problem_registry import (
    event_consumer_contract_gaps,
    looks_actionable_event,
    spec_for_event,
)
from zf.runtime.wake_patterns import compute_effective_wake_patterns


@dataclass(frozen=True)
class EventProducer:
    event_type: str
    producer_kind: str
    owner: str
    field: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class EventContractDiagnostic:
    severity: str
    kind: str
    event_type: str
    message: str
    producer: EventProducer | None = None
    event_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.producer is not None:
            payload["producer"] = self.producer.to_dict()
        else:
            payload["producer"] = None
        return payload


def collect_config_event_producers(config: Any) -> list[EventProducer]:
    producers: list[EventProducer] = []
    for role in list(getattr(config, "roles", []) or []):
        role_name = str(
            getattr(role, "instance_id", "")
            or getattr(role, "name", "")
            or "role"
        )
        for event_type in _event_list(getattr(role, "publishes", []) or []):
            producers.append(EventProducer(
                event_type=event_type,
                producer_kind="role_publish",
                owner=role_name,
                field="role.publishes",
            ))

    workflow = getattr(config, "workflow", None)
    for stage in list(getattr(workflow, "stages", []) or []):
        stage_id = str(getattr(stage, "id", "") or "stage")
        aggregate = getattr(stage, "aggregate", None)
        if aggregate is None:
            continue
        for field in (
            "success_event",
            "failure_event",
            "child_success_event",
            "child_failure_event",
        ):
            event_type = str(getattr(aggregate, field, "") or "").strip()
            if not event_type:
                continue
            producers.append(EventProducer(
                event_type=event_type,
                producer_kind=f"stage_aggregate_{field}",
                owner=stage_id,
                field=f"workflow.stages[].aggregate.{field}",
            ))

    for pipeline in list(getattr(workflow, "pipelines", []) or []):
        pipeline_id = str(getattr(pipeline, "pipeline_id", "") or "pipeline")
        for stage in list(getattr(pipeline, "stages", ()) or ()):
            stage_id = str(getattr(stage, "stage_id", "") or "stage")
            for field in ("success_event", "failure_event"):
                event_type = str(getattr(stage, field, "") or "").strip()
                if not event_type:
                    continue
                producers.append(EventProducer(
                    event_type=event_type,
                    producer_kind="lane_pipeline_terminal",
                    owner=f"{pipeline_id}:{stage_id}",
                    field=f"workflow.pipelines[].stages[].{field}",
                ))
        for field in ("final_success", "final_failure"):
            event_type = str(getattr(pipeline, field, "") or "").strip()
            if not event_type:
                continue
            producers.append(EventProducer(
                event_type=event_type,
                producer_kind="lane_pipeline_final",
                owner=pipeline_id,
                field=f"workflow.pipelines[].{field}",
            ))

    return _dedupe_producers(producers)


def build_event_contract_report(
    config: Any,
    *,
    events: Iterable[ZfEvent] | None = None,
    include_known_workflow_events: bool = True,
) -> dict[str, Any]:
    producers = collect_config_event_producers(config)
    producer_event_types = {producer.event_type for producer in producers}
    diagnostics: list[EventContractDiagnostic] = []

    if include_known_workflow_events:
        producer_event_types.update(_known_workflow_actionable_events())

    gap_types = set(event_consumer_contract_gaps(producer_event_types))
    producer_by_event: dict[str, EventProducer] = {
        producer.event_type: producer for producer in producers
    }
    for event_type in sorted(gap_types):
        diagnostics.append(EventContractDiagnostic(
            severity="error",
            kind="missing_consumer_contract",
            event_type=event_type,
            producer=producer_by_event.get(event_type),
            message=(
                f"Actionable event {event_type!r} has no event/problem "
                "registry consumer contract"
            ),
        ))

    diagnostics.extend(_child_boundary_diagnostics(config, producers))
    if events is not None:
        diagnostics.extend(event_scope_contract_diagnostics(events))
    from zf.runtime.run_manager_router import recovery_closeout_contract_report

    recovery_report = recovery_closeout_contract_report(
        event_types=producer_event_types if not include_known_workflow_events else None,
    )
    for item in recovery_report.get("errors", []):
        if not isinstance(item, dict):
            continue
        diagnostics.append(EventContractDiagnostic(
            severity="error",
            kind=str(item.get("kind") or "recovery_closeout_contract_error"),
            event_type=str(item.get("event_type") or ""),
            message=str(item.get("message") or item),
        ))

    errors = [item.to_dict() for item in diagnostics if item.severity == "error"]
    warnings = [item.to_dict() for item in diagnostics if item.severity != "error"]
    return {
        "schema_version": "event-contract-report.v1",
        "ok": not errors,
        "summary": {
            "producers": len(producers),
            "producer_event_types": len({p.event_type for p in producers}),
            "errors": len(errors),
            "warnings": len(warnings),
        },
        "producers": [producer.to_dict() for producer in producers],
        "recovery_closeout": recovery_report,
        "errors": errors,
        "warnings": warnings,
    }


def event_scope_contract_diagnostics(
    events: Iterable[ZfEvent],
) -> list[EventContractDiagnostic]:
    diagnostics: list[EventContractDiagnostic] = []
    for event in events:
        event_type = str(getattr(event, "type", "") or "").strip()
        if not looks_actionable_event(event_type):
            continue
        spec = spec_for_event(event_type)
        if spec is None:
            diagnostics.append(EventContractDiagnostic(
                severity="error",
                kind="unknown_actionable_runtime_event",
                event_type=event_type,
                event_id=str(getattr(event, "id", "") or ""),
                message=(
                    f"Runtime actionable event {event_type!r} has no "
                    "event/problem registry entry"
                ),
            ))
            continue
        required_any = tuple(getattr(spec, "required_scope_any", ()) or ())
        if not required_any:
            continue
        if _event_has_any_scope(event, required_any):
            continue
        diagnostics.append(EventContractDiagnostic(
            severity="warning",
            kind="missing_actionable_scope",
            event_type=event_type,
            event_id=str(getattr(event, "id", "") or ""),
            message=(
                f"Runtime event {event_type!r} lacks any required scope key "
                f"from {list(required_any)!r}; recovery de-dup/resume may be weak"
            ),
        ))
    return diagnostics


def event_contract_errors(config: Any) -> list[str]:
    report = build_event_contract_report(config)
    return [
        str(item.get("message") or item.get("kind") or item.get("event_type"))
        for item in report.get("errors", [])
        if isinstance(item, dict)
    ]


def _child_boundary_diagnostics(
    config: Any,
    producers: list[EventProducer],
) -> list[EventContractDiagnostic]:
    diagnostics: list[EventContractDiagnostic] = []
    effective_wake = compute_effective_wake_patterns(config)
    for producer in producers:
        if not producer.producer_kind.startswith("stage_aggregate_child_"):
            continue
        spec = spec_for_event(producer.event_type)
        if spec is not None and spec.owner_route != "kernel_aggregate":
            diagnostics.append(EventContractDiagnostic(
                severity="error",
                kind="child_result_owner_boundary_violation",
                event_type=producer.event_type,
                producer=producer,
                message=(
                    f"Fanout child result {producer.event_type!r} must be "
                    "owned by kernel_aggregate before Run Manager/Autoresearch "
                    "fallback can diagnose it"
                ),
            ))
        if producer.event_type not in effective_wake:
            diagnostics.append(EventContractDiagnostic(
                severity="error",
                kind="stage_child_result_not_woken",
                event_type=producer.event_type,
                producer=producer,
                message=(
                    f"Fanout child result {producer.event_type!r} is produced "
                    "by a workflow stage but is not in effective wake patterns"
                ),
            ))
    return diagnostics


def _event_has_any_scope(event: ZfEvent, keys: tuple[str, ...]) -> bool:
    payload = event.payload if isinstance(event.payload, dict) else {}
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return True
        if key == "task_id" and getattr(event, "task_id", None):
            return True
        if key == "trace_id" and getattr(event, "correlation_id", None):
            return True
    return False


def _known_workflow_actionable_events() -> set[str]:
    from zf.core.events.known_types import KNOWN_EVENT_TYPES

    prefixes = (
        "flow.",
        "goal.",
        "module.parity.",
        "cangjie.module.parity.",
        "issue.",
        "prd.",
        "zaofu.refactor.",
        "task_map.",
        "product_delivery.task_map.",
        "workflow.stage.",
    )
    return {
        event_type
        for event_type in KNOWN_EVENT_TYPES
        if event_type.startswith(prefixes) and looks_actionable_event(event_type)
    }


def _event_list(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for value in values:
        event_type = str(value or "").strip()
        if event_type:
            out.append(event_type)
    return out


def _dedupe_producers(producers: list[EventProducer]) -> list[EventProducer]:
    out: list[EventProducer] = []
    seen: set[tuple[str, str, str, str]] = set()
    for producer in producers:
        key = (
            producer.event_type,
            producer.producer_kind,
            producer.owner,
            producer.field,
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(producer)
    return out


__all__ = [
    "EventContractDiagnostic",
    "EventProducer",
    "build_event_contract_report",
    "collect_config_event_producers",
    "event_contract_errors",
    "event_scope_contract_diagnostics",
]
