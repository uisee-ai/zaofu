"""Read-only event metrics for Orchestrator Agent semantic checkpoints."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from zf.core.events.model import ZfEvent
from zf.core.cost.pricing import resolve_rate


_OPERATION_TERMINALS = frozenset({
    "workflow.operation.settled",
    "workflow.operation.failed",
    "workflow.operation.blocked",
    "workflow.operation.superseded",
    "workflow.operation.interrupted",
    "workflow.operation.cancelled",
})
_NORMAL_PROGRESS_EVENTS = frozenset({
    "dev.build.done",
    "fanout.child.completed",
    "lane.stage.completed",
    "verify.passed",
})
_STALE_REJECTION_EVENTS = frozenset({
    "orchestrator.semantic.decision.rejected",
    "orchestrator.semantic.rework.rejected",
    "owner.delivery.narrative.rejected",
})
_STALE_MARKERS = (
    "stale",
    "no longer current",
    "current package mismatch",
    "current target mismatch",
)
_DEFAULT_SLA_SECONDS = 300
_CHECKPOINT_SLA_SECONDS = {
    "semantic_failure": 180,
    "owner_delivery": 180,
}


def build_orchestrator_agent_metrics(
    events: Iterable[ZfEvent | tuple[int, ZfEvent]],
    *,
    observed_at: datetime | str | None = None,
) -> dict[str, Any]:
    """Project OA health without reading or mutating canonical state."""
    ordered = [item[1] if isinstance(item, tuple) else item for item in events]
    checkpoint_events: dict[str, ZfEvent] = {}
    for event in ordered:
        if event.type != "orchestrator.semantic.checkpoint.requested":
            continue
        operation_id = _text(event.payload, "operation_id")
        if operation_id:
            checkpoint_events[operation_id] = event

    operation_requests = [
        event
        for event in ordered
        if event.type == "workflow.operation.requested"
        and _text(event.payload, "operation_type")
        == "orchestrator_agent_semantic"
    ]
    operation_ids = {
        _text(event.payload, "operation_id") for event in operation_requests
    }
    operation_ids.discard("")
    skipped_events = [
        event
        for event in ordered
        if event.type == "orchestrator.semantic.checkpoint.skipped"
    ]
    terminals: dict[str, ZfEvent] = {}
    admitted_results: dict[str, ZfEvent] = {}
    for event in ordered:
        operation_id = _text(event.payload, "operation_id")
        if operation_id not in operation_ids:
            continue
        if event.type in _OPERATION_TERMINALS and operation_id not in terminals:
            terminals[operation_id] = event
        elif event.type == "workflow.call.result.admitted":
            admitted_results[operation_id] = event

    rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    checkpoint_groups: dict[str, list[float]] = {}
    normal_turn_operations: set[str] = set()
    normal_event_ids = {
        event.id for event in ordered if event.type in _NORMAL_PROGRESS_EVENTS
    }
    required_read_closed = 0
    usage_by_operation = _operation_usage(ordered, operation_ids)
    decisions = _operation_decisions(ordered, operation_ids)
    observed_time = _coerce_datetime(observed_at) or datetime.now(timezone.utc)
    for request in operation_requests:
        payload = _payload(request)
        operation_id = _text(payload, "operation_id")
        checkpoint_event = checkpoint_events.get(operation_id)
        checkpoint = _checkpoint_name(payload, checkpoint_event)
        terminal = terminals.get(operation_id)
        admitted = admitted_results.get(operation_id)
        ledger = _mapping(_payload(admitted).get("read_ledger_ref")) if admitted else {}
        read_closed = bool(ledger.get("ref") and ledger.get("sha256"))
        if admitted and read_closed:
            required_read_closed += 1
        latency = _seconds_between(request.ts, terminal.ts) if terminal else None
        age = latency
        if age is None:
            requested_at = _coerce_datetime(request.ts)
            age = (
                max(0.0, (observed_time - requested_at).total_seconds())
                if requested_at is not None
                else None
            )
        sla_threshold = _CHECKPOINT_SLA_SECONDS.get(
            checkpoint,
            _DEFAULT_SLA_SECONDS,
        )
        sla_breached = age is not None and age > sla_threshold
        if latency is not None:
            latencies.append(latency)
            checkpoint_groups.setdefault(checkpoint, []).append(latency)
        source_event_id = (
            _text(_payload(checkpoint_event), "source_event_id")
            if checkpoint_event is not None
            else ""
        ) or str(request.causation_id or "")
        if source_event_id in normal_event_ids:
            normal_turn_operations.add(operation_id)
        usage = usage_by_operation.get(operation_id, {})
        decision_projection = decisions.get(operation_id, {})
        rows.append({
            "operation_id": operation_id,
            "workflow_run_id": _text(payload, "workflow_run_id"),
            "checkpoint": checkpoint,
            "status": terminal.type.removeprefix("workflow.operation.") if terminal else "pending",
            "requested_event_id": request.id,
            "terminal_event_id": terminal.id if terminal else "",
            "source_event_id": source_event_id,
            "latency_seconds": _rounded(latency),
            "age_seconds": _rounded(age),
            "sla_threshold_seconds": sla_threshold,
            "sla_breached": sla_breached,
            "required_read_closed": read_closed if admitted else None,
            "usage_sample_count": int(usage.get("sample_count") or 0),
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
            **_usage_cost_summary([usage]),
            "decision": str(decision_projection.get("decision") or ""),
            "reason_codes": list(decision_projection.get("reason_codes") or []),
            "summary": str(decision_projection.get("summary") or ""),
            "explanation_status": str(
                decision_projection.get("explanation_status") or "degraded"
            ),
        })

    target_requests = [
        event
        for event in ordered
        if event.type == "orchestrator.semantic.rework.requested"
    ]
    target_matches = 0
    target_rejections = 0
    for request in target_requests:
        request_payload = _payload(request)
        matched = any(
            event.type == "task.rework.requested"
            and _text(event.payload, "trigger_event_id") == request.id
            and str(event.task_id or _text(event.payload, "task_id"))
            == str(request.task_id or _text(request_payload, "task_id"))
            and (
                not _text(request_payload, "target_role_instance")
                or _text(event.payload, "assignee")
                == _text(request_payload, "target_role_instance")
            )
            for event in ordered
        )
        rejected = any(
            event.type == "orchestrator.semantic.rework.rejected"
            and _text(event.payload, "source_event_id") == request.id
            for event in ordered
        )
        target_matches += int(matched)
        target_rejections += int(rejected and not matched)

    stale_rejections = [
        event
        for event in ordered
        if event.type in _STALE_REJECTION_EVENTS
        and _is_stale_reason(_text(event.payload, "reason"))
    ]
    fallback_events = [
        event
        for event in ordered
        if event.type == "owner.visible_message.requested"
        and _text(event.payload, "message_kind") == "run_terminal_delivery"
        and _text(event.payload, "narrative_status") == "degraded"
    ]
    reconstructible_fallbacks = sum(
        _fallback_reconstructible(event) for event in fallback_events
    )
    admitted_count = len(admitted_results)
    target_count = len(target_requests)
    normal_count = len(normal_event_ids)

    checkpoint_names = {
        row["checkpoint"] for row in rows
    } | {
        _text(event.payload, "checkpoint") or "unknown"
        for event in skipped_events
    }
    checkpoint_summary = {}
    for checkpoint in sorted(checkpoint_names):
        checkpoint_rows = [row for row in rows if row["checkpoint"] == checkpoint]
        checkpoint_skips = [
            event for event in skipped_events
            if (_text(event.payload, "checkpoint") or "unknown") == checkpoint
        ]
        values = checkpoint_groups.get(checkpoint, [])
        decision_counts: dict[str, int] = {}
        for row in checkpoint_rows:
            decision = str(row.get("decision") or "")
            if decision:
                decision_counts[decision] = decision_counts.get(decision, 0) + 1
        requested_count = len(checkpoint_rows) + len(checkpoint_skips)
        checkpoint_summary[checkpoint] = {
            "requested_count": requested_count,
            "operation_count": len(checkpoint_rows),
            "executed_count": len(checkpoint_rows),
            "skipped_count": len(checkpoint_skips),
            "execution_rate": _rate(len(checkpoint_rows), requested_count),
            "decision_counts": decision_counts,
            "latency_sample_count": len(values),
            "avg_operation_latency_seconds": _rounded(_average(values)),
            "max_operation_latency_seconds": _rounded(max(values)) if values else None,
            "sla_threshold_seconds": _CHECKPOINT_SLA_SECONDS.get(
                checkpoint,
                _DEFAULT_SLA_SECONDS,
            ),
            "sla_breach_count": sum(row["sla_breached"] for row in checkpoint_rows),
            "sla_breach_rate": _rate(
                sum(row["sla_breached"] for row in checkpoint_rows),
                len(checkpoint_rows),
            ),
            "input_tokens": sum(row["input_tokens"] for row in checkpoint_rows),
            "output_tokens": sum(row["output_tokens"] for row in checkpoint_rows),
            "total_tokens": sum(row["total_tokens"] for row in checkpoint_rows),
            **_usage_cost_summary(checkpoint_rows),
        }

    return {
        "schema_version": "orchestrator-agent-metrics.v1",
        "summary": {
            "operation_count": len(rows),
            "checkpoint_request_count": len(rows) + len(skipped_events),
            "checkpoint_executed_count": len(rows),
            "checkpoint_skipped_count": len(skipped_events),
            "checkpoint_execution_rate": _rate(
                len(rows),
                len(rows) + len(skipped_events),
            ),
            "settled_operation_count": sum(
                row["status"] == "settled" for row in rows
            ),
            "failed_operation_count": sum(
                row["status"] in {"failed", "blocked"} for row in rows
            ),
            "operation_latency_sample_count": len(latencies),
            "avg_operation_latency_seconds": _rounded(_average(latencies)),
            "p95_operation_latency_seconds": _rounded(_percentile_95(latencies)),
            "sla_breach_count": sum(row["sla_breached"] for row in rows),
            "sla_breach_rate": _rate(
                sum(row["sla_breached"] for row in rows),
                len(rows),
            ),
            "pending_sla_breach_count": sum(
                row["sla_breached"] and row["status"] == "pending"
                for row in rows
            ),
            "degraded_explanation_count": sum(
                row["explanation_status"] == "degraded" and bool(row["decision"])
                for row in rows
            ),
            "stale_reject_count": len(stale_rejections),
            "admitted_result_count": admitted_count,
            "required_read_closed_count": required_read_closed,
            "required_read_closure_rate": _rate(
                required_read_closed, admitted_count
            ),
            "targeted_rework_request_count": target_count,
            "target_match_count": target_matches,
            "target_reject_count": target_rejections,
            "target_pending_count": max(
                0, target_count - target_matches - target_rejections
            ),
            "target_match_rate": _rate(target_matches, target_count),
            "normal_path_event_count": normal_count,
            "normal_path_oa_turn_count": len(normal_turn_operations),
            "normal_path_oa_turn_rate": _rate(
                len(normal_turn_operations), normal_count
            ),
            "factual_fallback_count": len(fallback_events),
            "reconstructible_factual_fallback_count": reconstructible_fallbacks,
            "factual_fallback_reconstructible_rate": _rate(
                reconstructible_fallbacks, len(fallback_events)
            ),
            "narrative_admitted_count": sum(
                event.type == "owner.delivery.narrative.admitted"
                for event in ordered
            ),
            "narrative_degraded_count": sum(
                event.type == "owner.delivery.narrative.degraded"
                for event in ordered
            ),
            "input_tokens": sum(row["input_tokens"] for row in rows),
            "output_tokens": sum(row["output_tokens"] for row in rows),
            "total_tokens": sum(row["total_tokens"] for row in rows),
            **_usage_cost_summary(rows),
        },
        "checkpoints": checkpoint_summary,
        "stale_rejections": [
            {
                "event_id": event.id,
                "event_type": event.type,
                "operation_id": _text(event.payload, "operation_id"),
                "reason": _text(event.payload, "reason"),
            }
            for event in stale_rejections
        ],
        "operations": rows,
        "skipped_checkpoints": [
            {
                "operation_id": _text(event.payload, "operation_id"),
                "workflow_run_id": _text(event.payload, "workflow_run_id"),
                "checkpoint": _text(event.payload, "checkpoint"),
                "reason": _text(event.payload, "reason"),
                "sample_percent": _integer(event.payload, "sample_percent"),
                "sample_bucket": _integer(event.payload, "sample_bucket"),
            }
            for event in skipped_events
        ],
    }


def _operation_usage(
    events: list[ZfEvent],
    operation_ids: set[str],
) -> dict[str, dict[str, float | int]]:
    totals: dict[str, dict[str, float | int]] = {}
    seen: set[tuple[str, str]] = set()
    for event in events:
        if event.type != "agent.usage":
            continue
        payload = _payload(event)
        operation_id = _text(payload, "operation_id")
        if operation_id not in operation_ids:
            continue
        sample_id = _text(payload, "usage_sample_id") or event.id
        dedupe_key = (operation_id, sample_id)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        usage = _mapping(payload.get("usage"))
        input_tokens = _integer(usage, "input_tokens")
        output_tokens = _integer(usage, "output_tokens")
        backend = _text(payload, "backend")
        cache_creation = (
            0
            if backend == "codex"
            else _integer(usage, "cache_creation_input_tokens")
        )
        cache_read = (
            0
            if backend == "codex"
            else _integer(usage, "cache_read_input_tokens")
        )
        provider_cost = payload.get("total_cost_usd")
        if (
            isinstance(provider_cost, (int, float))
            and not isinstance(provider_cost, bool)
            and math.isfinite(provider_cost)
            and provider_cost >= 0
        ):
            cost = float(provider_cost)
        else:
            rate = resolve_rate(_text(payload, "model"))
            cost = (
                input_tokens * rate.input
                + output_tokens * rate.output
                + cache_creation * rate.cache_creation
                + cache_read * rate.cache_read
            ) / 1_000_000 if rate is not None else None
        row = totals.setdefault(operation_id, {
            "sample_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "unpriced_sample_count": 0,
        })
        row["sample_count"] += 1
        row["input_tokens"] += input_tokens
        row["output_tokens"] += output_tokens
        row["total_tokens"] += (
            input_tokens + output_tokens + cache_creation + cache_read
        )
        if cost is None:
            row["unpriced_sample_count"] += 1
        else:
            row["cost_usd"] += cost
    return totals


def _usage_cost_summary(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    known = 0.0
    unpriced = 0
    for row in rows:
        known += float(row.get("known_cost_usd", row.get("cost_usd")) or 0.0)
        unpriced += int(row.get("unpriced_sample_count") or 0)
    return {
        "cost_usd": None if unpriced else _rounded(known),
        "known_cost_usd": _rounded(known),
        "unpriced_sample_count": unpriced,
    }


def _operation_decisions(
    events: list[ZfEvent],
    operation_ids: set[str],
) -> dict[str, dict[str, Any]]:
    decisions: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.type not in {
            "orchestrator.semantic.decision.applied",
            "orchestrator.semantic.decision.observed",
        }:
            continue
        operation_id = _text(event.payload, "operation_id")
        decision = _text(event.payload, "decision")
        if operation_id in operation_ids and decision:
            payload = _payload(event)
            decisions[operation_id] = {
                "decision": decision,
                "reason_codes": [
                    str(item) for item in payload.get("reason_codes", [])
                    if str(item).strip()
                ] if isinstance(payload.get("reason_codes"), list) else [],
                "summary": _text(payload, "summary"),
                "explanation_status": (
                    _text(payload, "explanation_status")
                    or ("complete" if _text(payload, "summary") else "degraded")
                ),
            }
    return decisions


def _coerce_datetime(value: datetime | str | None) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _checkpoint_name(
    request: Mapping[str, Any],
    checkpoint_event: ZfEvent | None,
) -> str:
    if checkpoint_event is not None:
        value = _text(checkpoint_event.payload, "checkpoint")
        if value:
            return value
    stage = _text(request, "parent_stage_id")
    return stage.removeprefix("oa-") or "unknown"


def _fallback_reconstructible(event: ZfEvent) -> int:
    payload = _payload(event)
    required = (
        "terminal_event_id",
        "dossier_ref",
        "dossier_source_fingerprint",
        "owner_delivery_composite_ref",
    )
    if any(not _text(payload, key) for key in required):
        return 0
    if _text(payload, "terminal_event_type") == "run.goal.completed":
        if not _text(payload, "completion_receipt_ref"):
            return 0
        if not _text(payload, "completion_receipt_fingerprint"):
            return 0
    return 1


def _is_stale_reason(reason: str) -> bool:
    value = reason.lower()
    return any(marker in value for marker in _STALE_MARKERS)


def _seconds_between(start: str, end: str) -> float | None:
    first = _parse_timestamp(start)
    last = _parse_timestamp(end)
    if first is None or last is None:
        return None
    return max(0.0, (last - first).total_seconds())


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _percentile_95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def _average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _rounded(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def _payload(event: ZfEvent | None) -> dict[str, Any]:
    if event is None or not isinstance(event.payload, dict):
        return {}
    return event.payload


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: object, key: str) -> str:
    if not isinstance(value, Mapping):
        return ""
    return str(value.get(key) or "")


def _integer(value: object, key: str) -> int:
    if not isinstance(value, Mapping):
        return 0
    try:
        return int(value.get(key) or 0)
    except (TypeError, ValueError):
        return 0


__all__ = ["build_orchestrator_agent_metrics"]
