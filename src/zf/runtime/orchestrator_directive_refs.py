"""Frozen source-manifest authority for OA directive references."""
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
        if directives:
            allowed_refs = operation_source_descriptors(state_dir, request)
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
        "validation_scope": "authored_schema_and_frozen_directive_refs",
        "errors": errors,
    }
