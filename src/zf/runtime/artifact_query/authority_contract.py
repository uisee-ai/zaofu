"""Immutable dispatch authority requirements for canonical handoffs."""

from __future__ import annotations

from typing import Any, Mapping

HANDOFF_AUTHORITY_SCHEMA = "handoff-authority-contract.v1"


def build_handoff_authority_contract(
    payload: Mapping[str, Any],
    *,
    output_profile_id: str,
    stage_id: str,
    operation_type: str,
) -> dict[str, Any]:
    trigger = (
        payload.get("trigger_payload")
        if isinstance(payload.get("trigger_payload"), Mapping)
        else {}
    )
    continuation_key = str(
        payload.get("rework_of")
        or payload.get("rework_of_attempt_id")
        or trigger.get("rework_of")
        or trigger.get("rework_of_attempt_id")
        or ""
    ).strip()
    attempt_domain = str(payload.get("attempt_domain") or "").strip()
    profile = str(output_profile_id or "").strip()
    return {
        "schema_version": HANDOFF_AUTHORITY_SCHEMA,
        "output_profile_id": profile,
        "stage_id": str(stage_id or ""),
        "operation_type": str(operation_type or ""),
        "attempt_domain": attempt_domain,
        "continuation_key": continuation_key,
        "requires_prior_task_ref": bool(
            profile == "implementation" and continuation_key
        ),
        "requires_candidate_lineage": profile == "candidate-verify",
    }
