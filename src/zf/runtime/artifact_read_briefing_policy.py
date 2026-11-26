"""Render controlled CLI navigation for required artifact reads."""
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref


def plan_read_page_bytes(profile: object, authority: object) -> int | None:
    if profile == "plan-synth":
        return 32768
    if (profile == "workflow-read" and isinstance(authority, Mapping)
            and authority.get("output_profile_id") == profile and authority.get("attempt_domain") == "plan"
            and authority.get("operation_type") == "fanout_reader_child"):
        return 16384
    return None


def required_cli_entry(payload: Mapping[str, Any], command: str, *, native: bool) -> list[str]:
    label = "CLI fallback" if native else "Preferred deduplicated read"
    page_bytes = plan_read_page_bytes(payload.get("output_profile_id"), payload.get("handoff_authority_contract"))
    if not page_bytes:
        return [f"- {label}: `{command}`."]
    return [
        f"- {label} with an adjustable tool output budget: "
        f"`{command} --format text --page-bytes {page_bytes}`. "
        "Reserve at least 16384 native tool output tokens (e.g. max_output_tokens=16384), including wrapper overhead. "
        "Read the complete original text through ZF_ARTIFACT_TEXT_PAGE_END, then keep the same flags "
        "and append the returned --cursor for the next page.",
        f"- For unknown or smaller capacity, start at 8192 or 2048 bytes: `{command} --page-bytes 2048`. "
        "If clipped, retry the same input cursor (omit it for page one) at the smaller size before advancing. "
        "Keep raw evidence in the authorized session and cite its locator in shared reports. "
        "Exact source reads also accept --format text; their --page-size limit is 8192 characters.",
    ]


def verified_plan_read_policy(payload: Mapping[str, Any], state_dir: Path | None) -> Mapping[str, Any] | None:
    """Resolve the digest-bound policy only if its run, attempt and selections match."""
    if state_dir is None or payload.get("output_profile_id") != "plan-synth":
        return None
    attempt_id = payload.get("attempt_id") or payload.get("run_id")
    if not attempt_id or not payload.get("workflow_run_id") or not payload.get("required_reads"):
        return None
    descriptor = payload.get("input_consumption_policy_ref")
    if not isinstance(descriptor, Mapping) or not descriptor.get("sha256"):
        return None
    if payload.get("input_consumption_policy_digest") != descriptor["sha256"]:
        return None
    try:
        body = hydrate_sidecar_ref(state_dir, dict(descriptor)).payload
    except (SidecarRefError, ValueError, OSError):
        return None
    if (not isinstance(body, Mapping) or body.get("schema_version") != "input-consumption-policy.v1"
            or body.get("attempt_id") != attempt_id
            or body.get("workflow_run_id") != payload.get("workflow_run_id")
            or body.get("required_reads") != payload.get("required_reads")):
        return None
    return body


def bound_read_policy_locator(payload: Mapping[str, Any], state_dir: Path | None) -> str:
    """A locator replaces command repetition, never source bodies or obligations."""
    body = verified_plan_read_policy(payload, state_dir)
    if body is None:
        return ""
    descriptor = payload["input_consumption_policy_ref"]
    return (
        f"- Complete required-read policy: `{descriptor['ref']}` (sha256 `{descriptor['sha256']}`); "
        f"{len(body['required_reads'])} required selections. The required-read command above "
        "consumes this exact policy; individual commands and authorized paths remain available through "
        "artifact index. This locator is not source content or read evidence."
    )
