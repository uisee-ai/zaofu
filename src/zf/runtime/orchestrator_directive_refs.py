"""Frozen source-manifest authority for OA directives and context routes."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.runtime.orchestrator_agent_contracts import (
    OrchestratorAgentContractError,
    normalize_orchestration_decision,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def operation_source_descriptors(
    state_dir: Path, request: Mapping[str, Any],
) -> set[tuple[str, str]]:
    manifest_ref = request.get("source_manifest_ref")
    if not isinstance(manifest_ref, Mapping):
        return set()
    manifest = hydrate_sidecar_ref(state_dir, dict(manifest_ref)).payload
    sources = manifest.get("sources") if isinstance(manifest, Mapping) else None
    return {
        (str(item.get("ref") or ""), str(item.get("sha256") or ""))
        for item in sources if isinstance(item, Mapping)
    } if isinstance(sources, list) else set()


def directive_ref_rejection(
    directive: Mapping[str, Any], allowed_refs: set[tuple[str, str]],
) -> str:
    for field in ("basis_refs", "reuse_refs", "invalidate_refs"):
        values = directive.get(field)
        for descriptor in values if isinstance(values, list) else []:
            if not isinstance(descriptor, Mapping):
                continue
            key = (str(descriptor.get("ref") or ""), str(descriptor.get("sha256") or ""))
            if key not in allowed_refs:
                return f"directive_ref_outside_operation:{field}:{key[0]}"
    return ""


def context_route_ref_errors(
    run_plan: Mapping[str, Any], allowed_refs: set[tuple[str, str]],
) -> list[dict[str, str]]:
    errors = []
    for route_index, route in enumerate(run_plan.get("context_routes") or []):
        if not isinstance(route, Mapping):
            continue
        for source_index, descriptor in enumerate(route.get("required_sources") or []):
            if not isinstance(descriptor, Mapping):
                continue
            key = (str(descriptor.get("ref") or ""), str(descriptor.get("sha256") or ""))
            if key not in allowed_refs:
                errors.append({
                    "field": f"run_plan.context_routes[{route_index}].required_sources[{source_index}]",
                    "code": "context_ref_outside_operation",
                    "message": "context route source is outside the checkpoint manifest: " + key[0],
                    "work_unit_id": str(route.get("work_unit_id") or ""),
                    "required_change": (
                        "Use an exact ref/sha256 from this operation's source manifest. "
                        "Recheck copied routes against the current checkpoint; an older "
                        "plan's sources are not automatically authorized. Repair the same "
                        "scratch and rerun result validate/submit."
                    ),
                })
    return errors


def validate_orchestrator_submission(
    *, state_dir: Path, request: Mapping[str, Any], semantic: Mapping[str, Any],
) -> dict[str, Any]:
    """Check authored schema/refs only; live execution admission remains separate."""
    errors = []
    try:
        decision = normalize_orchestration_decision(semantic)
    except OrchestratorAgentContractError as exc:
        errors.append({"field": "control_result", "code": "schema_invalid", "message": str(exc)})
    else:
        delta = decision.get("delta") or {}
        directives = delta.get("directives") or []
        run_plan = decision.get("run_plan")
        run_plan = run_plan if isinstance(run_plan, Mapping) else {}
        if run_plan and decision.get("decision") == "adopt":
            from zf.runtime.oa_task_verify_assignment import validate_verify_units
            try:
                validate_verify_units(None, run_plan, request)
            except OrchestratorAgentContractError as exc:
                errors.append({"field": "run_plan", "code": "invalid_verification_team", "message": str(exc),
                               "required_change": "Correct the verification team against the bound checkpoint and resubmit."})
        if directives or run_plan:
            allowed_refs = operation_source_descriptors(state_dir, request)
            errors.extend(context_route_ref_errors(run_plan, allowed_refs))
            for directive in directives:
                reason = directive_ref_rejection(directive, allowed_refs)
                if reason:
                    errors.append({
                        "field": "delta.directives",
                        "code": "directive_ref_outside_operation",
                        "message": reason,
                        "directive_id": str(directive.get("directive_id") or ""),
                        "required_change": (
                            "Use an exact ref/sha256 from this operation's source manifest. "
                            "Nested links are not additional authority. Cite the bound parent "
                            "and explain the location, or request controlled input amendment. "
                            "Repair the same scratch and rerun result validate/submit."
                        ),
                    })
    return {
        "schema_version": "semantic-result-preflight.v1",
        "status": "failed" if errors else "passed",
        "profile_id": "orchestrator-semantic-decision",
        "profile_revision": str(request.get("output_profile_revision") or ""),
        "validation_scope": "authored_schema_and_frozen_directive_and_context_refs",
        "errors": errors,
    }
