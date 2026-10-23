"""Mechanical quality contract shared by PRD publication and evaluation."""

from __future__ import annotations

from typing import Any, Iterable

from zf.core.events.model import ZfEvent

PRD_QUALITY_CONTRACT = "channel-prd-quality.v1"


def required_prd_quality_contract(events: Iterable[ZfEvent], *, channel_id: str,
                                  thread_id: str, request_id: str) -> str:
    """The durable synthesis request owns the version, including repair turns."""
    for event in events:
        payload = event.payload or {}
        if (event.type == "channel.synthesis.requested"
                and payload.get("channel_id") == channel_id
                and str(payload.get("thread_id") or "main") == thread_id
                and payload.get("request_id") == request_id):
            return str(payload.get("prd_quality_contract") or "")
    return ""


_COMPLETE_SOURCE_STATES = {"complete", "full", "verified", "complete_verified"}
_INCOMPLETE_ARTIFACT_STATES = {
    "",
    "partial",
    "incomplete",
    "unavailable",
    "unread",
    "context_pack_artifact_refs_empty",
}


def quality_contract_enabled(body: dict[str, Any]) -> bool:
    if body.get("quality_contract") == PRD_QUALITY_CONTRACT:
        return True
    classification = body.get("classification")
    return isinstance(classification, dict) and any(
        key in classification
        for key in ("source_read_status", "artifact_readability_status")
    )


def validate_prd_quality_contract(body: dict[str, Any], *, required_version: str = "") -> str:
    """Return the first cross-layer inconsistency in a quality-enabled PRD."""
    if required_version:
        if required_version != PRD_QUALITY_CONTRACT:
            return f"unsupported required quality_contract: {required_version}"
        if body.get("quality_contract") != required_version:
            return f"quality_contract must be {required_version} as bound by the synthesis request"
    if not quality_contract_enabled(body):
        return ""
    classification = body.get("classification")
    if not isinstance(classification, dict):
        return "quality-enabled PRD requires classification"

    attestation = body.get("source_attestation")
    if attestation is not None and not isinstance(attestation, dict):
        return "source_attestation must be an object"
    if isinstance(attestation, dict) and attestation.get("all_required_sources_read") is True:
        source_status = str(classification.get("source_read_status") or "").strip().lower()
        artifact_status = str(
            classification.get("artifact_readability_status") or ""
        ).strip().lower()
        if source_status not in _COMPLETE_SOURCE_STATES:
            return (
                "source_attestation.all_required_sources_read=true conflicts with "
                f"classification.source_read_status={source_status or 'missing'}"
            )
        if artifact_status in _INCOMPLETE_ARTIFACT_STATES:
            return (
                "source_attestation.all_required_sources_read=true conflicts with "
                f"classification.artifact_readability_status={artifact_status or 'missing'}"
            )

    rows = body.get("requirement_coverage")
    if not isinstance(rows, list) or not rows:
        return "quality-enabled PRD requires requirement_coverage"
    for row in rows:
        if not isinstance(row, dict):
            continue
        evidence_refs = row.get("evidence_refs")
        if not isinstance(evidence_refs, list) or not any(
            isinstance(ref, str) and ref.strip() for ref in evidence_refs
        ):
            return f"requirement_coverage {row.get('id') or '<unknown>'} requires direct evidence_refs"
        identifier = str(row.get("id") or "").upper()
        kind = str(row.get("kind") or "").lower()
        if identifier.startswith("RES-") or kind == "research":
            if not str(row.get("adopted_conclusion") or "").strip():
                return f"research requirement {row.get('id') or '<unknown>'} requires adopted_conclusion"
            if not str(row.get("applicability_limit") or "").strip():
                return f"research requirement {row.get('id') or '<unknown>'} requires applicability_limit"

    consumed = _string_list(body.get("consumed_contribution_refs"))
    canonical = _string_list(body.get("canonical_cross_review_refs"))
    supporting = _string_list(body.get("supporting_contribution_refs"))
    if consumed or canonical or supporting:
        if set(canonical) & set(supporting):
            return "canonical_cross_review_refs and supporting_contribution_refs must be disjoint"
        if set(consumed) != set(canonical) | set(supporting):
            return "contribution ledgers must partition consumed_contribution_refs"

    permission = body.get("permission_audit_contract")
    if not isinstance(permission, dict):
        return "quality-enabled PRD requires permission_audit_contract"
    if permission.get("deny_by_default") is not True:
        return "permission_audit_contract.deny_by_default must be true"
    for field in ("event_fields", "query_fields"):
        values = permission.get(field)
        if not isinstance(values, list) or not any(
            isinstance(value, str) and value.strip() for value in values
        ):
            return f"permission_audit_contract.{field} must be a non-empty list"
    for field in ("retention_policy", "owner"):
        if not str(permission.get(field) or "").strip():
            return f"permission_audit_contract.{field} must be non-empty"
    return ""


def contribution_ledgers(
    pack: dict[str, Any], consumed_refs: list[str]
) -> dict[str, list[str]]:
    """Split consumed artifacts into canonical cross-review and support ledgers."""
    canonical_refs = {
        str(row.get("artifact_ref"))
        for row in pack.get("cross_review_index") or []
        if isinstance(row, dict)
        and row.get("status") == "completed"
        and row.get("artifact_ref")
    }
    supporting_refs = {
        str(row.get("artifact_ref"))
        for row in pack.get("contribution_index") or []
        if isinstance(row, dict)
        and row.get("contract_status") == "structured"
        and row.get("artifact_ref")
    }
    consumed = list(dict.fromkeys(str(ref) for ref in consumed_refs if str(ref).strip()))
    return {
        "canonical_cross_review_refs": [ref for ref in consumed if ref in canonical_refs],
        "supporting_contribution_refs": [ref for ref in consumed if ref in supporting_refs],
    }


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(
        str(item).strip() for item in value if isinstance(item, str) and item.strip()
    ))
