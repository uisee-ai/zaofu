"""Read-only verifier schema, contract and Product Acceptance preflight."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_adapters import AdaptedControlResult, ControlResultAdapterRegistry
from zf.runtime.product_acceptance import bind_product_acceptance_report
from zf.runtime.typed_result_admission import typed_admission_issues_for


def validate_verification_submission(
    *,
    state_dir: Path,
    registry: ControlResultAdapterRegistry,
    event_type: str,
    operation: Mapping[str, Any],
    request: Mapping[str, Any],
    semantic: Mapping[str, Any],
    role_instance: str,
    profile_id: str,
    revision: str,
    delivery: Mapping[str, Any],
) -> dict[str, Any]:
    """Reuse admission validators without creating sidecars or consuming a lease.

    Passing this check is not result admission: the submit path still checks
    current execution authority, input consumption and all completion gates.
    """
    profile = registry.profile(profile_id, revision)
    identity = dict(request.get("result_identity") or {})
    for key in ("placement_epoch", "task_stage_session_binding"):
        if delivery.get(key) not in (None, ""):
            identity[key] = delivery[key]
    identity.update({
        "workflow_run_id": str(operation.get("workflow_run_id") or ""),
        "operation_id": str(operation.get("operation_id") or ""),
        "request_hash": str(operation.get("request_hash") or ""),
        "attempt_id": str(operation.get("active_attempt_id") or ""),
        "dispatch_id": str(operation.get("dispatch_id") or ""),
        "lease_id": str(operation.get("lease_id") or ""),
        "role_instance": role_instance,
        "output_profile_id": profile_id,
        "output_profile_revision": revision,
    })
    protected = {k: v for k, v in identity.items() if v not in (None, "")}
    result = {**dict(semantic), **protected}
    result.setdefault("schema_version", profile.schema_version)
    event = ZfEvent(
        type=event_type, actor=role_instance,
        payload={**protected, profile.semantic_field: result},
    )
    adapter = registry.adapter_for(event)
    if (
        adapter is None or adapter.adapter_id != profile.adapter_id
        or (profile.allowed_event_types and event_type not in profile.allowed_event_types)
    ):
        raise ValueError(f"event {event_type!r} has no adapter for profile {profile_id!r}")
    normalized, issues = adapter.normalize(event)
    adapted = AdaptedControlResult(
        adapter_id=adapter.adapter_id, schema_version=adapter.schema_version,
        payload=normalized, descriptor={}, issues=tuple(issues),
    )
    adapted = bind_product_acceptance_report(
        adapted, state_dir=state_dir,
        operation={**dict(operation), "result_identity": identity, "output_profile_id": profile_id},
        source_event_id="", persist=False,
    )
    errors = [dict(issue) for issue in adapted.issues]
    errors.extend(typed_admission_issues_for(state_dir, adapted, semantic_submit=True))
    return {
        "schema_version": "semantic-result-preflight.v1",
        "status": "failed" if errors else "passed",
        "profile_id": profile_id,
        "profile_revision": revision,
        "errors": errors,
    }
