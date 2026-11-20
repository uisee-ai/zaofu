"""Stage-scoped canonical input policy for controlled artifact reads."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any


_READER_STAGE_MARKERS = ("discovery", "bridge", "parity")
_READER_DESCRIPTOR_FIELDS = (
    (
        "reader-plan-package",
        "plan_artifact_package_ref",
        "plan_artifact_package_digest",
        "plan_artifact_package",
    ),
    (
        "reader-goal-claim-set",
        "goal_claim_set_ref",
        "goal_claim_set_digest",
        "goal_claim_set",
    ),
    ("reader-task-map", "task_map_ref", "task_map_digest", "task_map"),
    (
        "reader-source-index",
        "source_index_ref",
        "source_index_digest",
        "source_index",
    ),
    (
        "reader-verification-result",
        "verification_result_ref",
        "verification_result_digest",
        "verification_result",
    ),
)


def reader_stage_id(manifest: Mapping[str, Any]) -> str:
    metadata = (
        manifest.get("metadata")
        if isinstance(manifest.get("metadata"), Mapping)
        else {}
    )
    raw_authority = manifest.get("handoff_authority_contract")
    if not isinstance(raw_authority, Mapping):
        raw_authority = metadata.get("handoff_authority_contract")
    authority = raw_authority if isinstance(raw_authority, Mapping) else {}
    return str(
        authority.get("stage_id")
        or manifest.get("stage_id")
        or metadata.get("stage_id")
        or ""
    ).strip()


def reader_stage_requires_inputs(stage_id: str) -> bool:
    normalized = str(stage_id or "").strip().lower().replace("_", "-")
    return any(marker in normalized for marker in _READER_STAGE_MARKERS)


def workflow_reader_required_source_paths(
    manifest: Mapping[str, Any],
    *,
    output_profile_id: str,
) -> tuple[dict[str, tuple[str, ...]], str]:
    """Return exact full-source reads and a missing-source stage identity."""

    if str(output_profile_id or "").strip() != "workflow-read":
        return {}, ""
    stage_id = reader_stage_id(manifest)
    if not reader_stage_requires_inputs(stage_id):
        return {}, ""
    sources = (
        manifest.get("sources")
        if isinstance(manifest.get("sources"), list)
        else []
    )
    exact_flow_present = any(isinstance(source, Mapping)
                             and source.get("source_id") == "critical-approved-flow" for source in sources)
    required = {
        str(source.get("source_id") or ""): ("$",)
        for source in sources
        if isinstance(source, Mapping) and str(source.get("source_id") or "")
        and not (exact_flow_present and source.get("source_id") == "approved-config-full")
    }
    return required, "" if required else stage_id


def reader_stage_input_refs(
    payload: Mapping[str, Any],
    *,
    stage_id: str,
) -> list[dict[str, Any]]:
    """Compile canonical Reader inputs already named by a trigger payload."""

    if not reader_stage_requires_inputs(stage_id):
        return []
    trigger = (
        payload.get("trigger_payload")
        if isinstance(payload.get("trigger_payload"), Mapping)
        else {}
    )
    refs: list[dict[str, Any]] = []
    for source_id, ref_key, digest_key, kind in _READER_DESCRIPTOR_FIELDS:
        ref = str(payload.get(ref_key) or trigger.get(ref_key) or "").strip()
        if not ref:
            continue
        descriptor = {
            "source_id": source_id,
            "artifact_id": Path(ref).name,
            "kind": kind,
            "ref": ref,
            "allowed_paths": ["$"],
        }
        digest = str(
            payload.get(digest_key) or trigger.get(digest_key) or ""
        ).strip()
        if digest:
            descriptor["sha256"] = digest
        refs.append(descriptor)
    for field, prefix, kind in (
        ("result_refs", "reader-result", "result"),
        ("evidence_refs", "reader-evidence", "evidence"),
    ):
        values = payload.get(field) or trigger.get(field)
        if not isinstance(values, list):
            continue
        for index, value in enumerate(values, start=1):
            descriptor = dict(value) if isinstance(value, Mapping) else {
                "ref": str(value or ""),
            }
            if not str(descriptor.get("ref") or "").strip():
                continue
            descriptor.setdefault("source_id", f"{prefix}-{index}")
            descriptor.setdefault(
                "artifact_id",
                Path(str(descriptor["ref"])).name,
            )
            descriptor.setdefault("kind", kind)
            descriptor.setdefault("allowed_paths", ["$"])
            refs.append(descriptor)
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for descriptor in refs:
        key = (
            str(descriptor.get("ref") or ""),
            str(descriptor.get("sha256") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(descriptor)
    return deduped


__all__ = [
    "reader_stage_id",
    "reader_stage_input_refs",
    "reader_stage_requires_inputs",
    "workflow_reader_required_source_paths",
]
