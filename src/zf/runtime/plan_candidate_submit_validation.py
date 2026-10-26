"""Pinned Plan candidate validation shared by validate and submit."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_adapters import ControlResultAdapterRegistry


def validate_plan_candidate_semantic(
    *,
    state_dir: Path,
    request: Mapping[str, Any],
    semantic: Mapping[str, Any],
    profile_id: str,
    project_root: Path,
) -> dict[str, Any] | None:
    validation = request.get("plan_candidate_validation")
    if not isinstance(validation, Mapping):
        return None
    if profile_id != "workflow-read":
        _raise_submit_error(
            "plan_candidate_profile_invalid",
            "Plan candidate validation requires the workflow-read profile",
        )
    manifest = validation.get("manifest")
    if not isinstance(manifest, Mapping):
        _raise_submit_error(
            "plan_candidate_context_invalid",
            "operation has no pinned Plan candidate manifest",
        )
    metadata = validation.get("metadata")
    if metadata is not None and not isinstance(metadata, Mapping):
        _raise_submit_error(
            "plan_candidate_context_invalid",
            "operation Plan candidate metadata must be an object",
        )
    writer_policy = validation.get("writer_policy")
    if writer_policy is not None and not isinstance(writer_policy, Mapping):
        _raise_submit_error(
            "plan_candidate_context_invalid",
            "operation Plan candidate writer policy must be an object",
        )
    from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight

    return evaluate_plan_candidate_preflight(
        state_dir=Path(state_dir),
        project_root=Path(project_root),
        reports=[{"report": semantic}],
        manifest=dict(manifest),
        metadata=dict(metadata) if isinstance(metadata, Mapping) else None,
        writer_policy=(
            dict(writer_policy) if isinstance(writer_policy, Mapping) else None
        ),
    )


def validate_plan_synth_result(
    *,
    registry: ControlResultAdapterRegistry,
    event_type: str,
    operation: Mapping[str, Any],
    request: Mapping[str, Any],
    semantic: Mapping[str, Any],
    operation_id: str,
    role_instance: str,
    profile_id: str,
    revision: str,
) -> dict[str, Any]:
    """Run the Plan Synth typed-result checks without writing sidecars."""

    profile = registry.profile(profile_id, revision)
    identity = dict(request.get("result_identity") or {})
    identity.update({
        "workflow_run_id": str(operation.get("workflow_run_id") or ""),
        "operation_id": operation_id,
        "request_hash": str(operation.get("request_hash") or ""),
        "attempt_id": str(operation.get("active_attempt_id") or ""),
        "dispatch_id": str(operation.get("dispatch_id") or ""),
        "lease_id": str(operation.get("lease_id") or ""),
        "role_instance": role_instance,
        "output_profile_id": profile_id,
        "output_profile_revision": revision,
    })
    protected = {
        key: value for key, value in identity.items()
        if value not in (None, "")
    }
    event = ZfEvent(
        type=event_type,
        actor=role_instance,
        payload={
            **protected,
            profile.semantic_field: {**dict(semantic), **protected},
        },
    )
    adapter = registry.adapter_for(event)
    if adapter is None or adapter.adapter_id != profile.adapter_id:
        _raise_submit_error(
            "profile_adapter_invalid",
            f"no {profile.adapter_id!r} adapter for {event_type!r}",
        )
    _normalized, adapter_issues = adapter.normalize(event)
    errors = [dict(item) for item in adapter_issues]

    execution_status = str(semantic.get("status") or "completed").lower()
    if execution_status not in {"completed", "failed", "failure"}:
        errors.append({
            "field": "status",
            "code": "plan_synth_status_invalid",
            "message": (
                "Plan Synth status must be completed or failed; "
                f"got {execution_status!r}"
            ),
        })
    recommendation = str(semantic.get("recommendation") or "").lower()
    if recommendation and recommendation not in {
        "approve", "reject", "needs_rework", "abstain",
    }:
        errors.append({
            "field": "recommendation",
            "code": "plan_synth_recommendation_invalid",
            "message": (
                "Plan Synth recommendation must be approve, reject, "
                "needs_rework, or abstain; "
                f"got {recommendation!r}"
            ),
        })

    report = semantic.get("report")
    if not isinstance(report, Mapping):
        errors.append({
            "field": "report",
            "code": "plan_synth_report_invalid",
            "message": "Plan Synth result requires a report object",
        })
    else:
        from zf.runtime.fanout import validate_fanout_report

        errors.extend(plan_synth_verdict_errors(semantic))
        report_validation = validate_fanout_report(
            dict(report),
            child_id="synth",
        )
        errors.extend({
            "field": "report",
            "code": "plan_synth_report_invalid",
            "message": diagnostic,
        } for diagnostic in report_validation.diagnostics)
        if semantic.get("findings") not in (None, []):
            top_level_report = dict(report)
            top_level_report["findings"] = semantic.get("findings")
            top_level_validation = validate_fanout_report(
                top_level_report,
                child_id="synth",
            )
            errors.extend({
                "field": "findings",
                "code": "plan_synth_report_invalid",
                "message": diagnostic,
            } for diagnostic in top_level_validation.diagnostics)

    return {
        "schema_version": "semantic-result-preflight.v1",
        "status": "failed" if errors else "passed",
        "profile_id": profile_id,
        "profile_revision": revision,
        "errors": errors,
    }


def plan_synth_verdict_errors(semantic: Mapping[str, Any]) -> list[dict[str, str]]:
    """Require authored review fields before normalization can apply defaults."""
    report = semantic.get("report")
    report = report if isinstance(report, Mapping) else {}
    return [
        {
            "field": f"report.{field}",
            "code": "plan_synth_verdict_required",
            "message": f"Plan Synth requires an explicit non-empty report.{field}",
        }
        for field in ("status", "recommendation", "summary")
        if not isinstance(report.get(field), str) or not report[field].strip()
    ]


def _raise_submit_error(code: str, message: str) -> None:
    # Imported lazily so result_submit can delegate here without import cycles.
    from zf.runtime.result_submit import ResultSubmitError

    raise ResultSubmitError(code, message)


__all__ = [
    "plan_synth_verdict_errors",
    "validate_plan_candidate_semantic",
    "validate_plan_synth_result",
]
