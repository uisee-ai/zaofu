"""Render controlled CLI navigation for required artifact reads."""
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref


def plan_cli_page_guidance(payload: Mapping[str, Any]) -> str:
    profile = payload.get("output_profile_id")
    authority = payload.get("handoff_authority_contract")
    if profile == "plan-synth":
        page_bytes = 32768
    elif (profile == "workflow-read" and isinstance(authority, Mapping)
          and authority.get("output_profile_id") == profile and authority.get("attempt_domain") == "plan"
          and authority.get("operation_type") == "fanout_reader_child"):
        page_bytes = 16384
    else:
        return ""
    return (
        "- When using the controlled CLI with a tool that can return a complete wide page, add "
        f"`--format text --page-bytes {page_bytes}` to the required-read command. "
        "Reserve at least 16384 native tool output tokens when adjustable, including wrapper overhead. "
        "Use one page per call and check the full original text and its next_cursor. "
        "Text mode preserves source quoting; do not infer command syntax from display escaping. "
        "For unknown or smaller capacity, start at 8192 or 2048 bytes. If clipped, retry the same input cursor "
        "(omit it for page one) at the smaller size before advancing. Keep raw evidence in the authorized session "
        "and cite its locator in shared reports. Exact source reads also accept --format text; "
        "their --page-size limit is 8192 characters."
    )


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
        f"{len(body['required_reads'])} required selections. The required-read command above "
        "consumes this exact policy; individual commands and authorized paths remain available through "
        "artifact index. This locator is not source content or read evidence."
    )
