"""Small approval envelope; bodies remain explicitly deferred, never empty-complete."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.runtime.plan_preview_artifacts import preview_package_artifacts, read_plan_preview_bundle
from zf.runtime.plan_preview_sources import plan_source_payload, plan_authority_refs


def build_plan_preview_summary(state_dir: Path, events: list[Any], *, plan_id: str,
                               project_root: Path | None) -> dict:
    from zf.runtime.operator_plan_preview import (
        _plan_events, _payload, _event_id, _event_ts, _correlation_id, _int_or_none,
        PLAN_PREVIEW_SCHEMA_VERSION, PLAN_APPROVED,
    )

    requested, resolved = _plan_events(events, plan_id)
    if requested is None and resolved is None:
        return {"ok": False, "plan_id": plan_id, "reason": "plan_id not found"}
    source = requested or resolved
    payload, bound = plan_source_payload(state_dir, events, plan_id=plan_id,
        source_event=source, project_root=project_root)
    decision = _payload(resolved) if resolved is not None else {}
    status = "pending" if resolved is None else "approved" if (
        getattr(resolved, "type", None) == PLAN_APPROVED
        or (isinstance(resolved, dict) and resolved.get("type") == PLAN_APPROVED)
        or str(decision.get("resolution") or decision.get("status") or "") in {"approved", "approve"}
    ) else "rejected"
    ports, error = preview_package_artifacts(payload, state_dir=state_dir, project_root=project_root)
    bundle, bundle_error = read_plan_preview_bundle(payload, state_dir=state_dir, project_root=project_root)
    error = error or bundle_error
    plan_ref = str(payload.get("plan_artifact_ref") or payload.get("plan_ref") or "")
    digest = str(payload.get("plan_artifact_digest") or "")
    if plan_ref and not error and not any(p["name"] == "accepted_plan" for p in ports):
        ports.append({"name": "accepted_plan", "ref": plan_ref, "sha256": digest})
    authority = plan_authority_refs(state_dir, payload=payload, artifacts=ports,
        preview_bundle=bundle, project_root=project_root)
    for index, ref in enumerate(authority, 1):
        if not any(p["ref"] == ref for p in ports):
            ports.append({"name": f"requirement_{index}_{Path(ref).name}", "ref": ref, "sha256": ""})
    owner = payload.get("owner_route") == "plan_synth_owner_decision"
    return {
        "schema_version": PLAN_PREVIEW_SCHEMA_VERSION, "ok": True, "plan_id": plan_id,
        "status": status, "requested_event_id": _event_id(requested), "requested_ts": _event_ts(requested),
        "resolved_event_id": _event_id(resolved), "resolved_ts": _event_ts(resolved),
        "reject_reason": str(decision.get("reason") or ""),
        "stage_id": str(payload.get("stage_id") or ""),
        "trace_id": str(payload.get("trace_id") or _correlation_id(source) or ""),
        "pdd_id": str(payload.get("pdd_id") or ""), "task_count": _int_or_none(payload.get("task_count")),
        "source_event_id": _event_id(bound) or _event_id(source),
        "content_state": "deferred", "markdown": "",
        "content_profile": str(bundle.get("content_profile") or payload.get("plan_content_profile") or "unknown"),
        "artifacts": ports, "artifacts_error": error,
        "requirement_artifacts": [p for p in ports if p["name"].startswith(("requirement_", "authority_"))],
        "authority_refs": authority,
        "refs": {key: str(payload.get(key) or "") for key in (
            "digest_ref", "task_map_ref", "plan_artifact_ref", "plan_artifact_digest",
            "source_index_ref", "requirement_spec_ref", "plan_preview_bundle_ref", "plan_preview_bundle_digest")},
        "actions": {"approve": "replan-approve" if owner else "plan-approve",
                    "reject": "replan-reject" if owner else "plan-reject", "repair_chat": "chat-orchestrator"},
        "action_payload": {key: payload[key] for key in (
            "proposal_ref", "eval_ref", "candidate_task_map_ref", "approval_ref", "checkpoint_id",
            "workflow_run_id", "fanout_id", "source_event_id") if payload.get(key)},
    }
