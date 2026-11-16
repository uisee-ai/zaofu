"""Parent-owned identity projection for reader fanout aggregates."""

from __future__ import annotations

from typing import Any


PARENT_FLOW_IDENTITY_KEYS = (
    "workflow_run_id",
    "pdd_id",
    "feature_id",
    "goal_id",
    "flow_kind",
    "trace_id",
    "task_map_ref",
    "task_map_generation",
    "source_index_ref",
    "source_commit",
    "candidate_base_commit",
    "candidate_ref",
    "candidate_head_commit",
    "target_ref",
)


def parent_flow_identity(
    manifest: dict[str, Any],
    *,
    aggregate_payload: dict[str, Any],
    payloads: list[dict[str, Any]],
) -> tuple[dict[str, str], list[dict[str, object]]]:
    """Resolve immutable flow identity before consulting child results."""

    trigger = (
        manifest.get("trigger_payload")
        if isinstance(manifest.get("trigger_payload"), dict)
        else {}
    )
    identity: dict[str, str] = {}
    for key in PARENT_FLOW_IDENTITY_KEYS:
        value = trigger.get(key)
        if value in (None, ""):
            value = manifest.get(key)
        if value in (None, ""):
            value = aggregate_payload.get(key)
        if value not in (None, ""):
            identity[key] = str(value)

    parent_pdd_id = _first_nonempty(
        identity.get("pdd_id"),
        identity.get("feature_id"),
    )
    if parent_pdd_id:
        identity["pdd_id"] = parent_pdd_id
        identity["feature_id"] = _first_nonempty(
            identity.get("feature_id"),
            parent_pdd_id,
        )
        # Preserve an explicitly bound Goal; PDD is only the legacy fallback.
        identity["goal_id"] = _first_nonempty(
            trigger.get("goal_id"), manifest.get("goal_id"), parent_pdd_id,
        )

    conflicts: list[dict[str, object]] = []
    for key, parent_value in identity.items():
        child_values = sorted({
            str(value)
            for payload in payloads
            if (
                value := _direct_payload_or_report_value(payload, key)
            ) not in (None, "")
            and str(value) != parent_value
        })
        if child_values:
            conflicts.append({
                "key": key,
                "parent_value": parent_value,
                "child_values": child_values,
            })
    return identity, conflicts


def _direct_payload_or_report_value(payload: dict[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value not in (None, ""):
        return value
    report = payload.get("report")
    if isinstance(report, dict):
        value = report.get(key)
        if value not in (None, ""):
            return value
    return None


def _first_nonempty(*values: Any) -> str:
    return next(
        (str(value) for value in values if value not in (None, "")),
        "",
    )


__all__ = ["PARENT_FLOW_IDENTITY_KEYS", "parent_flow_identity"]
