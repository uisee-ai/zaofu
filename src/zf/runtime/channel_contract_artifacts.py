"""Versioned sidecars for typed Channel contribution and synthesis bodies."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

from zf.core.security.redaction import redact_obj
from zf.runtime.sidecar_refs import write_sidecar_json


CONTRIBUTION_SCHEMA_VERSION = "channel.contribution.v2"
SYNTHESIS_SCHEMA_VERSION = "channel.synthesis.v2"
CHANNEL_PRD_SCHEMA_VERSION = "channel-prd.v1"
CHANNEL_PRD_READINESS_SCHEMA_VERSION = "channel-prd-readiness.v1"
CHANNEL_CONCLUSION_SCHEMA_VERSION = "channel-conclusion.v1"
CHANNEL_SEMANTIC_COVERAGE_SCHEMA_VERSION = "channel.semantic_coverage.v1"


def validate_channel_contract(
    body: dict[str, Any],
    *,
    kind: str,
) -> str:
    summary = body.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return "summary must be a non-empty string"
    list_fields = (
        (
            "questions",
            "open_questions",
            "findings",
            "contradictions",
            "risks",
            "source_refs",
            "evidence_refs",
            "consumed_message_digests",
        )
        if kind == "contribution"
        else (
            "decisions",
            "assumptions",
            "out_of_scope",
            "acceptance_criteria",
            "verification_commands",
            "open_questions",
            "risks",
            "source_refs",
            "evidence_refs",
            "consumed_contribution_refs",
            "consumed_contribution_digests",
            "consumed_message_digests",
            "dissent",
        )
    )
    for field in list_fields:
        value = body.get(field)
        if value is not None and not isinstance(value, list):
            return f"{field} must be a list"
    if kind == "contribution":
        freeze = body.get("freeze")
        if freeze is not None and not isinstance(freeze, bool):
            return "freeze must be a boolean"
    else:
        _, next_round_error = normalize_synthesis_next_round(
            body.get("next_round")
        )
        if next_round_error:
            return next_round_error
        workflow = body.get("recommended_workflow")
        if workflow is not None and not isinstance(workflow, dict):
            return "recommended_workflow must be an object"
        classification = body.get("classification")
        if classification is not None and not isinstance(
            classification,
            dict,
        ):
            return "classification must be an object"
        readiness = body.get("readiness")
        if readiness is not None and not isinstance(readiness, dict):
            return "readiness must be an object"
        if isinstance(readiness, dict):
            verdict = str(readiness.get("verdict") or "").strip()
            if verdict and verdict not in {
                "ready",
                "needs_owner",
                "needs_multi_lens",
            }:
                return (
                    "readiness.verdict must be ready, needs_owner, or "
                    "needs_multi_lens"
                )
            implementation_start = readiness.get("implementation_start")
            if (
                implementation_start is not None
                and not isinstance(implementation_start, bool)
            ):
                return "readiness.implementation_start must be a boolean"
            for field in ("gaps", "risks", "evidence_refs"):
                value = readiness.get(field)
                if value is not None and not isinstance(value, list):
                    return f"readiness.{field} must be a list"
            if (
                implementation_start is True
                and str(readiness.get("verdict") or "").strip() != "ready"
            ):
                return "readiness.implementation_start requires verdict=ready"
    return ""


def normalize_synthesis_next_round(
    value: object,
) -> tuple[dict[str, Any], str]:
    """Normalize the optional semantic request for one selective next pass."""

    default = {
        "action": "finalize",
        "reason": "",
        "objective": "",
        "target_member_ids": [],
    }
    if value in (None, ""):
        return default, ""
    if not isinstance(value, dict):
        return {}, "next_round must be an object"
    unknown = sorted(set(value) - set(default))
    if unknown:
        return {}, "next_round has unsupported field(s): " + ", ".join(unknown)
    action = str(value.get("action") or "").strip().lower()
    if action not in {"finalize", "continue"}:
        return {}, "next_round.action must be finalize or continue"
    reason = str(value.get("reason") or "").strip()
    objective = str(value.get("objective") or "").strip()
    raw_targets = value.get("target_member_ids")
    if raw_targets is None:
        raw_targets = []
    if not isinstance(raw_targets, list):
        return {}, "next_round.target_member_ids must be a list"
    targets = [str(item).strip() for item in raw_targets if str(item).strip()]
    if len(targets) != len(set(targets)):
        return {}, "next_round.target_member_ids must not contain duplicates"
    if action == "finalize":
        if reason or objective or targets:
            return {}, "next_round.finalize must not include reason, objective, or targets"
        return default, ""
    if not reason:
        return {}, "next_round.reason is required when action=continue"
    if not objective:
        return {}, "next_round.objective is required when action=continue"
    if not targets:
        return {}, "next_round.target_member_ids is required when action=continue"
    if len(reason) > 2000 or len(objective) > 4000:
        return {}, "next_round reason or objective exceeds the bounded contract"
    if len(targets) > 32:
        return {}, "next_round.target_member_ids exceeds 32"
    return {
        "action": "continue",
        "reason": reason,
        "objective": objective,
        "target_member_ids": targets,
    }, ""


def persist_channel_contract(
    state_dir: Path,
    *,
    channel_id: str,
    thread_id: str,
    identity: str,
    kind: str,
    body: dict[str, Any],
    created_by: str,
    source_event_id: str,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    schema_version = (
        CONTRIBUTION_SCHEMA_VERSION
        if kind == "contribution"
        else SYNTHESIS_SCHEMA_VERSION
    )
    safe_channel = _safe_segment(channel_id)
    safe_identity = _safe_segment(identity)
    payload = redact_obj({
        "schema_version": schema_version,
        "kind": kind,
        "channel_id": channel_id,
        "thread_id": thread_id,
        "identity": identity,
        "body": body,
        "provenance": provenance or {},
    })
    return write_sidecar_json(
        Path(state_dir),
        (
            PurePosixPath("channels")
            / safe_channel
            / "contracts"
            / kind
            / f"{safe_identity}.json"
        ),
        payload,
        kind=f"channel_{kind}",
        schema_version=schema_version,
        created_by=created_by,
        source_event_id=source_event_id,
        access_scope={
            "visibility": "project",
            "channel_id": channel_id,
            "thread_id": thread_id,
        },
        retention={"class": "audit_required"},
        required=True,
        preview=str(body.get("summary") or "")[:240],
    )


def persist_channel_source_manifest(
    state_dir: Path,
    *,
    channel_id: str,
    thread_id: str,
    identity: str,
    source_refs: list[str],
    evidence_refs: list[str],
    created_by: str,
    source_event_id: str,
) -> dict[str, Any]:
    payload = {
        "schema_version": "channel.source_manifest.v1",
        "channel_id": channel_id,
        "thread_id": thread_id,
        "identity": identity,
        "source_refs": source_refs,
        "evidence_refs": evidence_refs,
    }
    return write_sidecar_json(
        Path(state_dir),
        (
            PurePosixPath("channels")
            / _safe_segment(channel_id)
            / "source-manifests"
            / f"{_safe_segment(identity)}.json"
        ),
        payload,
        kind="channel_source_manifest",
        schema_version="channel.source_manifest.v1",
        created_by=created_by,
        source_event_id=source_event_id,
        access_scope={
            "visibility": "project",
            "channel_id": channel_id,
            "thread_id": thread_id,
        },
        retention={"class": "audit_required"},
        required=True,
        preview=f"{len(source_refs)} sources, {len(evidence_refs)} evidence refs",
    )


def persist_channel_semantic_coverage(
    state_dir: Path,
    *,
    channel_id: str,
    thread_id: str,
    identity: str,
    coverage: dict[str, Any],
    created_by: str,
    source_event_id: str,
) -> dict[str, Any]:
    """Persist the exact member/round/message coverage behind a conclusion."""

    payload = redact_obj({
        "schema_version": CHANNEL_SEMANTIC_COVERAGE_SCHEMA_VERSION,
        "channel_id": channel_id,
        "thread_id": thread_id,
        "identity": identity,
        "status": str(coverage.get("status") or ""),
        "manifest_digest": str(coverage.get("manifest_digest") or ""),
        "required_message_digests": coverage.get(
            "required_message_digests"
        ) if isinstance(coverage.get("required_message_digests"), list) else [],
        "consumed_message_digests": coverage.get(
            "consumed_message_digests"
        ) if isinstance(coverage.get("consumed_message_digests"), list) else [],
        "sources": coverage.get("sources")
        if isinstance(coverage.get("sources"), list)
        else [],
    })
    return write_sidecar_json(
        Path(state_dir),
        (
            PurePosixPath("channels")
            / _safe_segment(channel_id)
            / "semantic-coverage"
            / f"{_safe_segment(identity)}.json"
        ),
        payload,
        kind="channel_semantic_coverage",
        schema_version=CHANNEL_SEMANTIC_COVERAGE_SCHEMA_VERSION,
        created_by=created_by,
        source_event_id=source_event_id,
        access_scope={
            "visibility": "project",
            "channel_id": channel_id,
            "thread_id": thread_id,
        },
        retention={"class": "audit_required"},
        required=True,
        preview=(
            f"{len(payload['consumed_message_digests'])}/"
            f"{len(payload['required_message_digests'])} sources consumed"
        ),
    )


def persist_channel_prd_readiness(
    state_dir: Path,
    *,
    channel_id: str,
    thread_id: str,
    revision: int,
    body: dict[str, Any],
    created_by: str,
    source_event_id: str,
) -> dict[str, Any]:
    payload = redact_obj({
        "schema_version": CHANNEL_PRD_READINESS_SCHEMA_VERSION,
        "channel_id": channel_id,
        "thread_id": thread_id,
        "revision": revision,
        "verdict": str(body.get("verdict") or "unassessed"),
        "implementation_start": body.get("implementation_start") is True,
        "gaps": body.get("gaps") if isinstance(body.get("gaps"), list) else [],
        "risks": body.get("risks") if isinstance(body.get("risks"), list) else [],
        "evidence_refs": (
            body.get("evidence_refs")
            if isinstance(body.get("evidence_refs"), list)
            else []
        ),
        "reason": str(body.get("reason") or ""),
    })
    return write_sidecar_json(
        Path(state_dir),
        (
            PurePosixPath("channels")
            / _safe_segment(channel_id)
            / "prd"
            / f"r{revision}-readiness.json"
        ),
        payload,
        kind="channel_prd_readiness",
        schema_version=CHANNEL_PRD_READINESS_SCHEMA_VERSION,
        created_by=created_by,
        source_event_id=source_event_id,
        access_scope={
            "visibility": "project",
            "channel_id": channel_id,
            "thread_id": thread_id,
        },
        retention={"class": "audit_required"},
        required=True,
        preview=str(payload["verdict"]),
    )


def persist_channel_prd(
    state_dir: Path,
    *,
    channel_id: str,
    thread_id: str,
    revision: int,
    previous_ref: str,
    previous_digest: str,
    body: dict[str, Any],
    readiness_descriptor: dict[str, Any],
    created_by: str,
    source_event_id: str,
) -> dict[str, Any]:
    payload = redact_obj({
        "schema_version": CHANNEL_PRD_SCHEMA_VERSION,
        "channel_id": channel_id,
        "thread_id": thread_id,
        "revision": revision,
        "previous_ref": previous_ref,
        "previous_digest": previous_digest,
        "readiness_ref": str(readiness_descriptor.get("ref") or ""),
        "readiness_digest": str(
            readiness_descriptor.get("sha256") or ""
        ),
        "body": body,
    })
    return write_sidecar_json(
        Path(state_dir),
        (
            PurePosixPath("channels")
            / _safe_segment(channel_id)
            / "prd"
            / f"r{revision}.json"
        ),
        payload,
        kind="channel_prd",
        schema_version=CHANNEL_PRD_SCHEMA_VERSION,
        created_by=created_by,
        source_event_id=source_event_id,
        access_scope={
            "visibility": "project",
            "channel_id": channel_id,
            "thread_id": thread_id,
        },
        retention={"class": "audit_required"},
        required=True,
        preview=str(body.get("summary") or "")[:240],
    )


def persist_channel_conclusion(
    state_dir: Path,
    *,
    channel_id: str,
    thread_id: str,
    revision: int,
    prd_descriptor: dict[str, Any],
    readiness_descriptor: dict[str, Any],
    summary: str,
    source_refs: list[str],
    created_by: str,
    source_event_id: str,
) -> dict[str, Any]:
    payload = {
        "schema_version": CHANNEL_CONCLUSION_SCHEMA_VERSION,
        "channel_id": channel_id,
        "thread_id": thread_id,
        "revision": revision,
        "prd_ref": str(prd_descriptor.get("ref") or ""),
        "prd_digest": str(prd_descriptor.get("sha256") or ""),
        "readiness_ref": str(readiness_descriptor.get("ref") or ""),
        "readiness_digest": str(
            readiness_descriptor.get("sha256") or ""
        ),
        "summary": summary,
        "source_refs": source_refs,
    }
    return write_sidecar_json(
        Path(state_dir),
        (
            PurePosixPath("channels")
            / _safe_segment(channel_id)
            / "conclusions"
            / f"r{revision}.json"
        ),
        payload,
        kind="channel_conclusion",
        schema_version=CHANNEL_CONCLUSION_SCHEMA_VERSION,
        created_by=created_by,
        source_event_id=source_event_id,
        access_scope={
            "visibility": "project",
            "channel_id": channel_id,
            "thread_id": thread_id,
        },
        retention={"class": "audit_required"},
        required=True,
        preview=summary[:240],
    )


def typed_items(value: object) -> list[Any]:
    if not isinstance(value, list):
        return []
    return [redact_obj(item) for item in value[:64]]


def string_refs(value: object, *, limit: int = 64) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(
        str(item).strip()
        for item in value
        if isinstance(item, str) and str(item).strip()
    ))[:limit]


def channel_template_binding(channel: dict[str, Any]) -> dict[str, str]:
    scope = (
        channel.get("scope")
        if isinstance(channel.get("scope"), dict)
        else {}
    )
    template = (
        scope.get("template")
        if isinstance(scope.get("template"), dict)
        else {}
    )
    return {
        "id": str(template.get("id") or ""),
        "version": str(template.get("version") or ""),
        "digest": str(template.get("digest") or ""),
        "materialization_digest": str(
            template.get("materialization_digest") or ""
        ),
    }


def _safe_segment(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "")).strip(".-")
    return safe or "unknown"


__all__ = [
    "CONTRIBUTION_SCHEMA_VERSION",
    "SYNTHESIS_SCHEMA_VERSION",
    "CHANNEL_CONCLUSION_SCHEMA_VERSION",
    "CHANNEL_PRD_READINESS_SCHEMA_VERSION",
    "CHANNEL_PRD_SCHEMA_VERSION",
    "CHANNEL_SEMANTIC_COVERAGE_SCHEMA_VERSION",
    "channel_template_binding",
    "normalize_synthesis_next_round",
    "persist_channel_conclusion",
    "persist_channel_contract",
    "persist_channel_prd",
    "persist_channel_prd_readiness",
    "persist_channel_semantic_coverage",
    "persist_channel_source_manifest",
    "string_refs",
    "typed_items",
    "validate_channel_contract",
]
