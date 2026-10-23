"""Admission fence between an approved config snapshot and its actual consumer."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from zf.core.config.render import redact_config, renderable_config_to_primitive
from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref


def approved_config_consumer_error(
    state_dir: Path, config: Any, payload: Mapping[str, Any],
) -> str:
    """Check new admission only; never replace a running consumer's config.

    Legacy invokes without a proposal retain their existing admission contract.
    Proposal integrity and approval must be checked by the caller first.
    """
    if not payload.get("workflow_proposal_ref"):
        return ""
    descriptor = payload.get("effective_config_ref")
    if not isinstance(descriptor, dict):
        return "consumer_config_snapshot_missing"
    try:
        hydrated = hydrate_sidecar_ref(state_dir, descriptor)
    except (SidecarRefError, OSError):
        return "consumer_config_snapshot_invalid"
    snapshot = hydrated.payload
    if not hydrated.ok or not isinstance(snapshot, dict):
        return "consumer_config_snapshot_invalid"
    expected = snapshot.get("config")
    if (snapshot.get("schema_version") != "effective-config-snapshot.v1"
            or not isinstance(expected, dict)):
        return "consumer_config_snapshot_invalid"
    current = redact_config(renderable_config_to_primitive(config))
    if _execution_fields(current) != _execution_fields(expected):
        return "consumer_config_stale: load the approved execution policy before new admission"
    return ""


def _execution_fields(config: Mapping[str, Any]) -> dict[str, Any]:
    # Both operands use the same rendered, redacted snapshot schema. UI and
    # telemetry changes cannot require another product-delivery approval.
    from zf.runtime.workflow_route_policy import EXECUTION_POLICY_FIELDS, EXECUTION_RUNTIME_FIELDS

    result = {name: config.get(name) for name in EXECUTION_POLICY_FIELDS}
    runtime = config.get("runtime") or {}
    result["runtime"] = {name: runtime.get(name) for name in EXECUTION_RUNTIME_FIELDS}
    return result
