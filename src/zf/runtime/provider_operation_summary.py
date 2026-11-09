"""Immutable summary for one provider root operation and its native children."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref


PROVIDER_OPERATION_SUMMARY_SCHEMA = "provider-operation-summary.v1"
PROVIDER_OPERATION_SETTLEMENTS = frozenset({
    "running",
    "settled",
    "failed",
    "cancelled",
})
PROVIDER_CHILD_STATUSES = frozenset({
    "queued",
    "pending",
    "running",
    "completed",
    "failed",
    "cancelled",
})
TERMINAL_PROVIDER_OPERATION_SETTLEMENTS = frozenset({
    "settled",
    "failed",
    "cancelled",
})


def prepare_provider_operation_summary(
    *,
    state_dir: Path,
    source_payload: Mapping[str, Any],
    workflow_run_id: str,
    operation_id: str,
    max_parallel_agents: int = 6,
    budget_usd: float | None = None,
    source_event_id: str = "",
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Validate inline/ref input and return one immutable descriptor."""

    inline = source_payload.get("provider_operation_summary")
    ref = source_payload.get("provider_operation_summary_ref")
    if inline is None and ref is None:
        return None, []
    if inline is not None and ref is not None:
        return None, [_issue(
            "provider_operation_summary",
            "inline_and_ref_conflict",
        )]
    if inline is not None:
        if not isinstance(inline, Mapping):
            return None, [_issue(
                "provider_operation_summary",
                "missing_object",
            )]
        summary = dict(inline)
    else:
        if not isinstance(ref, Mapping):
            return None, [_issue(
                "provider_operation_summary_ref",
                "missing_object",
            )]
        try:
            hydrated = hydrate_sidecar_ref(state_dir, dict(ref))
        except SidecarRefError as exc:
            return None, [_issue(
                "provider_operation_summary_ref",
                "invalid_ref",
                str(exc),
            )]
        if not isinstance(hydrated.payload, dict):
            return None, [_issue(
                "provider_operation_summary_ref",
                "payload_not_object",
            )]
        summary = dict(hydrated.payload)

    issues = validate_provider_operation_summary(
        summary,
        workflow_run_id=workflow_run_id,
        operation_id=operation_id,
        max_parallel_agents=max_parallel_agents,
        budget_usd=budget_usd,
        require_terminal=True,
    )
    if issues:
        return None, issues
    descriptor = write_immutable_json_sidecar(
        state_dir,
        summary,
        root="provider-operations/summaries",
        kind="provider_operation_summary",
        schema_version=PROVIDER_OPERATION_SUMMARY_SCHEMA,
        created_by="call-result-admission",
        source_event_id=source_event_id,
    )
    return descriptor, []


def validate_provider_operation_summary(
    summary: Mapping[str, Any],
    *,
    workflow_run_id: str = "",
    operation_id: str = "",
    max_parallel_agents: int = 6,
    budget_usd: float | None = None,
    require_terminal: bool = False,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if str(summary.get("schema_version") or "") != PROVIDER_OPERATION_SUMMARY_SCHEMA:
        issues.append(_issue("provider_operation_summary.schema_version", "unsupported_schema"))
    for field in ("workflow_run_id", "operation_id", "provider_session_id"):
        if not str(summary.get(field) or "").strip():
            issues.append(_issue(
                f"provider_operation_summary.{field}",
                "missing_required",
            ))
    if (
        workflow_run_id
        and str(summary.get("workflow_run_id") or "") != workflow_run_id
    ):
        issues.append(_issue(
            "provider_operation_summary.workflow_run_id",
            "identity_mismatch",
        ))
    if operation_id and str(summary.get("operation_id") or "") != operation_id:
        issues.append(_issue(
            "provider_operation_summary.operation_id",
            "identity_mismatch",
        ))

    settlement = str(summary.get("settlement") or "")
    if settlement not in PROVIDER_OPERATION_SETTLEMENTS:
        issues.append(_issue(
            "provider_operation_summary.settlement",
            "enum_mismatch",
        ))
    elif require_terminal and settlement not in TERMINAL_PROVIDER_OPERATION_SETTLEMENTS:
        issues.append(_issue(
            "provider_operation_summary.settlement",
            "operation_not_terminal",
        ))

    child_count = _nonnegative_int(
        summary.get("child_count"),
        field="provider_operation_summary.child_count",
        issues=issues,
    )
    active_count = _nonnegative_int(
        summary.get("active_child_count"),
        field="provider_operation_summary.active_child_count",
        issues=issues,
    )
    peak_parallel = _nonnegative_int(
        summary.get("peak_parallel_agents"),
        field="provider_operation_summary.peak_parallel_agents",
        issues=issues,
    )
    counts_raw = summary.get("child_status_counts")
    counts: dict[str, int] = {}
    if not isinstance(counts_raw, Mapping):
        issues.append(_issue(
            "provider_operation_summary.child_status_counts",
            "missing_object",
        ))
    else:
        for key, value in counts_raw.items():
            status = str(key)
            if status not in PROVIDER_CHILD_STATUSES:
                issues.append(_issue(
                    f"provider_operation_summary.child_status_counts.{status}",
                    "unknown_status",
                ))
                continue
            counts[status] = _nonnegative_int(
                value,
                field=f"provider_operation_summary.child_status_counts.{status}",
                issues=issues,
            )
        if child_count is not None and sum(counts.values()) != child_count:
            issues.append(_issue(
                "provider_operation_summary.child_status_counts",
                "count_mismatch",
            ))
        expected_active = sum(
            counts.get(status, 0) for status in ("queued", "pending", "running")
        )
        if active_count is not None and expected_active != active_count:
            issues.append(_issue(
                "provider_operation_summary.active_child_count",
                "count_mismatch",
            ))
    if (
        settlement in TERMINAL_PROVIDER_OPERATION_SETTLEMENTS
        and active_count not in (None, 0)
    ):
        issues.append(_issue(
            "provider_operation_summary.active_child_count",
            "terminal_has_active_children",
        ))
    ceiling = 0 if max_parallel_agents == 0 else max(1, min(6, int(max_parallel_agents)))
    if ceiling and peak_parallel is not None and peak_parallel > ceiling:
        issues.append(_issue(
            "provider_operation_summary.peak_parallel_agents",
            "concurrency_ceiling_exceeded",
            f"peak {peak_parallel} exceeds {ceiling}",
        ))

    usage = summary.get("usage")
    if not isinstance(usage, Mapping):
        issues.append(_issue("provider_operation_summary.usage", "missing_object"))
    else:
        for key, value in usage.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                issues.append(_issue(
                    f"provider_operation_summary.usage.{key}",
                    "nonnegative_number_required",
                ))
    cost = summary.get("cost_usd")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
        issues.append(_issue(
            "provider_operation_summary.cost_usd",
            "nonnegative_number_required",
        ))
    elif budget_usd is not None and float(cost) > float(budget_usd):
        issues.append(_issue(
            "provider_operation_summary.cost_usd",
            "budget_ceiling_exceeded",
        ))
    return issues


def _nonnegative_int(
    value: Any,
    *,
    field: str,
    issues: list[dict[str, str]],
) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        issues.append(_issue(field, "nonnegative_integer_required"))
        return None
    return value


def _issue(field: str, code: str, message: str = "") -> dict[str, str]:
    issue = {"field": field, "code": code}
    if message:
        issue["message"] = message
    return issue


__all__ = [
    "PROVIDER_CHILD_STATUSES",
    "PROVIDER_OPERATION_SETTLEMENTS",
    "PROVIDER_OPERATION_SUMMARY_SCHEMA",
    "TERMINAL_PROVIDER_OPERATION_SETTLEMENTS",
    "prepare_provider_operation_summary",
    "validate_provider_operation_summary",
]
