"""Bounded canonical Channel PRD context for project agents."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from zf.runtime.channel_projection import project_channel, project_channels
from zf.runtime.channel_readiness import (
    channel_prd_planning_authorized,
    owner_readiness_risk_accepted,
)


def canonical_channel_prd_context(
    state_dir: Path,
    *,
    limit: int = 8,
) -> dict[str, Any]:
    """Project consensus-backed PRD identities without hydrating their bodies."""
    items: list[dict[str, Any]] = []
    for channel in project_channels(Path(state_dir)).get("channels") or []:
        if not isinstance(channel, dict):
            continue
        syntheses = [
            item
            for item in channel.get("syntheses") or []
            if isinstance(item, dict)
        ]
        consensus_by_thread = channel.get("consensus")
        if not isinstance(consensus_by_thread, dict):
            continue
        for thread_id, consensus in consensus_by_thread.items():
            if not isinstance(consensus, dict):
                continue
            artifact_ref = str(consensus.get("artifact_ref") or "").strip()
            artifact_digest = str(
                consensus.get("artifact_digest") or ""
            ).strip()
            reached_event_id = str(
                consensus.get("reached_event_id") or ""
            ).strip()
            if not artifact_ref or not artifact_digest or not reached_event_id:
                continue
            synthesis = next(
                (
                    item
                    for item in reversed(syntheses)
                    if str(item.get("thread_id") or "main")
                    == str(thread_id or "main")
                    and str(item.get("artifact_ref") or "") == artifact_ref
                    and _bare_digest(item.get("artifact_digest"))
                    == _bare_digest(artifact_digest)
                ),
                None,
            )
            if synthesis is None:
                continue
            readiness_verdict = str(
                synthesis.get("readiness_verdict")
                or consensus.get("readiness_verdict")
                or "unassessed"
            ).strip()
            implementation_start = synthesis.get(
                "implementation_start",
                consensus.get("implementation_start"),
            )
            readiness_ref = str(synthesis.get("readiness_ref") or "")
            readiness_digest = str(
                synthesis.get("readiness_digest") or ""
            )
            risk_accepted = owner_readiness_risk_accepted(
                consensus,
                readiness_ref=readiness_ref,
                readiness_digest=readiness_digest,
            )
            if not channel_prd_planning_authorized(
                readiness_verdict=readiness_verdict,
                risk_accepted=risk_accepted,
            ):
                continue
            source_refs = list(dict.fromkeys([
                *(
                    str(item).strip()
                    for item in synthesis.get("source_refs") or []
                    if str(item).strip()
                ),
                *(
                    str(item).strip()
                    for item in consensus.get("source_refs") or []
                    if str(item).strip()
                ),
            ]))
            channel_id = str(channel.get("channel_id") or "")
            normalized_thread_id = str(thread_id or "main")
            items.append({
                "channel_id": channel_id,
                "channel_name": str(channel.get("name") or ""),
                "thread_id": normalized_thread_id,
                "channel_member_id": str(
                    channel.get("leader_member_id") or ""
                ),
                "leader_revision": int(
                    channel.get("leader_revision") or 0
                ),
                "prd_revision": int(
                    consensus.get("prd_revision")
                    or synthesis.get("prd_revision")
                    or 0
                ),
                "artifact_ref": artifact_ref,
                "artifact_digest": artifact_digest,
                "source_ref": (
                    f"channel:{channel_id}/{normalized_thread_id}"
                ),
                "synthesis_event_id": str(
                    synthesis.get("event_id") or ""
                ),
                "consensus_event_id": reached_event_id,
                "readiness_ref": readiness_ref,
                "readiness_digest": readiness_digest,
                "readiness_verdict": readiness_verdict,
                "implementation_start": implementation_start is True,
                "declared_implementation_start": (
                    implementation_start is True
                ),
                "risk_accepted": risk_accepted,
                "confirmed_by": str(
                    consensus.get("human_confirmed_by") or ""
                ),
                "source_refs": source_refs,
                "updated_at": str(channel.get("updated_at") or ""),
            })
    bounded_limit = min(20, max(1, int(limit)))
    items.sort(
        key=lambda item: (
            str(item.get("updated_at") or ""),
            str(item.get("consensus_event_id") or ""),
        ),
        reverse=True,
    )
    return {
        "schema_version": "channel-prd-context.v1",
        "is_derived_projection": True,
        "items": items[:bounded_limit],
    }


def canonical_channel_prd_authority(
    state_dir: Path,
    *,
    channel_id: str,
    thread_id: str = "main",
) -> dict[str, Any]:
    """Return the exact mechanical authority for one ready canonical PRD."""

    normalized_thread = str(thread_id or "main")
    ready = canonical_channel_prd_context(Path(state_dir), limit=20)
    item = next(
        (
            candidate
            for candidate in ready.get("items") or []
            if isinstance(candidate, dict)
            and str(candidate.get("channel_id") or "") == channel_id
            and str(candidate.get("thread_id") or "main") == normalized_thread
        ),
        None,
    )
    channel = project_channel(Path(state_dir), channel_id) or {}
    consensus = (
        channel.get("consensus", {}).get(normalized_thread)
        if isinstance(channel.get("consensus"), dict)
        else None
    )
    if item is None or not isinstance(consensus, dict):
        return {}
    try:
        leader_revision = int(channel.get("leader_revision") or 0)
        prd_revision = int(consensus.get("prd_revision") or 0)
    except (TypeError, ValueError):
        return {}
    authority = {
        "channel_id": channel_id,
        "thread_id": normalized_thread,
        "channel_member_id": str(channel.get("leader_member_id") or ""),
        "leader_revision": leader_revision,
        "prd_revision": prd_revision,
        "source_ref": str(item.get("artifact_ref") or ""),
        "source_digest": str(item.get("artifact_digest") or ""),
    }
    if any(value in (None, "", 0) for value in authority.values()):
        return {}
    return authority


def _bare_digest(value: object) -> str:
    return str(value or "").strip().removeprefix("sha256:")


def workflow_context_from_payload(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    value = payload.get("workflow_context")
    return dict(value) if isinstance(value, dict) else {}


def workflow_context_for_project(
    payload: Mapping[str, Any],
    project_root: Path,
) -> dict[str, Any]:
    """Add only deterministic project-local defaults to Agent Plan context."""

    context = workflow_context_from_payload(payload)
    if not str(context.get("target_root") or "").strip():
        context["target_root"] = str(Path(project_root).resolve())
    return context


__all__ = [
    "canonical_channel_prd_authority",
    "canonical_channel_prd_context",
    "workflow_context_for_project",
    "workflow_context_from_payload",
]
