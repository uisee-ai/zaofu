"""Render a compact locator only when the complete read policy is pinned."""
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref


def bound_read_policy_locator(payload: Mapping[str, Any], state_dir: Path | None) -> str:
    """A locator replaces command repetition, never source bodies or obligations."""
    if state_dir is None or payload.get("output_profile_id") != "plan-synth":
        return ""
    attempt_id = payload.get("attempt_id") or payload.get("run_id")
    if not attempt_id or not payload.get("workflow_run_id") or not payload.get("required_reads"):
        return ""
    descriptor = payload.get("input_consumption_policy_ref")
    if not isinstance(descriptor, Mapping) or not descriptor.get("sha256"):
        return ""
    if payload.get("input_consumption_policy_digest") != descriptor["sha256"]:
        return ""
    try:
        body = hydrate_sidecar_ref(state_dir, dict(descriptor)).payload
    except (SidecarRefError, ValueError, OSError):
        return ""
    if (not isinstance(body, Mapping) or body.get("schema_version") != "input-consumption-policy.v1"
            or body.get("attempt_id") != attempt_id
            or body.get("workflow_run_id") != payload.get("workflow_run_id")
            or body.get("required_reads") != payload.get("required_reads")):
        return ""
    return (
        f"- Complete required-read policy: `{descriptor['ref']}` (sha256 `{descriptor['sha256']}`); "
        f"{len(body['required_reads'])} required selections. The preferred required-read command above "
        "consumes this exact policy; individual commands and authorized paths remain available through "
        "artifact index. This locator is not source content or read evidence."
    )
