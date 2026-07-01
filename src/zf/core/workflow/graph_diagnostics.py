"""Diagnostics for compiled workflow graph configs."""

from __future__ import annotations

from typing import Any

from zf.core.workflow.topology import (
    EXTERNAL_EVENTS,
    KERNEL_SWEPT_FAILURE_EVENTS,
    derive_kernel_swept_events,
)
from zf.core.workflow.lane_pipeline import lane_pipeline_rework_events


REWORK_SUFFIXES: frozenset[str] = frozenset({"failed", "rejected"})


def build_workflow_graph_diagnostics(
    *,
    nodes: list[Any],
    external_triggers: Any = (),
    pipelines: Any = (),
    stages: list[object],
    event_producers: dict[str, list[str]],
    event_consumers: dict[str, list[str]],
    rework_routes: tuple[Any, ...],
    terminal_policy: object,
) -> list[dict[str, str]]:
    diagnostics: list[dict[str, str]] = []
    route_events = {
        str(getattr(route, "event", ""))
        for route in rework_routes
    } | set(lane_pipeline_rework_events(pipelines))
    role_refs: set[str] = set()
    for node in nodes:
        if str(getattr(node, "type", "")) != "role_stage":
            continue
        role_refs.add(str(getattr(node, "stage_id", "")))
        metadata = getattr(node, "metadata", {}) or {}
        if isinstance(metadata, dict):
            role_refs.add(str(metadata.get("name", "") or ""))
    produced_events = set(event_producers)
    produced_events.update(_node_events(nodes))
    terminal_events = (
        set(getattr(terminal_policy, "success_events", frozenset()))
        | set(getattr(terminal_policy, "failure_events", frozenset()))
    )

    for stage in stages:
        stage_id = str(getattr(stage, "id", "") or "")
        trigger = str(getattr(stage, "trigger", "") or "")
        if (
            trigger
            and trigger not in produced_events
            and trigger not in EXTERNAL_EVENTS
            and trigger not in set(external_triggers or ())
        ):
            diagnostics.append({
                "kind": "trigger_without_producer",
                "stage_id": stage_id,
                "event": trigger,
            })
        _append_aggregate_diagnostics(
            diagnostics=diagnostics,
            stage=stage,
            stage_id=stage_id,
            event_consumers=event_consumers,
            route_events=route_events,
            terminal_events=terminal_events,
        )

    for route in rework_routes:
        target = str(getattr(route, "target_role", "") or "")
        if target and target not in role_refs:
            diagnostics.append({
                "kind": "invalid_rework_target",
                "event": str(getattr(route, "event", "") or ""),
                "target_role": target,
            })

    failure_events: set[str] = set()
    for node in nodes:
        # failure_event may be a comma-joined list (lane pipeline stages
        # declare e.g. "dev.blocked,dev.failed"); route lookup is per event.
        for part in str(getattr(node, "failure_event", "")).split(","):
            part = part.strip()
            if part and _event_tail(part) in REWORK_SUFFIXES and part not in terminal_events:
                failure_events.add(part)
    swept = derive_kernel_swept_events(stages, pipelines)
    for event in sorted(failure_events):
        if event not in route_events and event not in swept:
            diagnostics.append({
                "kind": "missing_rework_route",
                "event": event,
                "stage_id": "",
            })
    return diagnostics


def _append_aggregate_diagnostics(
    *,
    diagnostics: list[dict[str, str]],
    stage: object,
    stage_id: str,
    event_consumers: dict[str, list[str]],
    route_events: set[str],
    terminal_events: set[str],
) -> None:
    aggregate = getattr(stage, "aggregate", None)
    topology = str(getattr(stage, "topology", "") or "")
    for key in ("success_event", "failure_event"):
        event = str(getattr(aggregate, key, "") or "")
        if topology.startswith("fanout") and not event:
            diagnostics.append({
                "kind": f"missing_aggregate_{key}",
                "stage_id": stage_id,
                "field": key,
            })
        if event and event not in event_consumers and event not in route_events and event not in terminal_events:
            diagnostics.append({
                "kind": "event_without_consumer",
                "stage_id": stage_id,
                "event": event,
                "field": key,
            })


def _node_events(nodes: list[Any]) -> set[str]:
    events: set[str] = set()
    for node in nodes:
        for attr in ("success_event", "failure_event", "skipped_event"):
            event = str(getattr(node, attr, "") or "")
            if event:
                events.add(event)
    return events


def _event_tail(event: str) -> str:
    return event.rsplit(".", 1)[-1]
