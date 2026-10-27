"""Read-only Channel delivery evidence and digest-bound PRD document views."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from zf.core.config.project_context import ProjectContext
from zf.core.config.schema import ZfConfig
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.runtime.channel_projection import project_channel
from zf.runtime.kanban_plan_requests import pending_kanban_plan_requests
from zf.runtime.kanban_proposals import pending_kanban_proposals
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref
from zf.web.projections.workspace import _resolve_api_project


def _digest(value: object) -> str:
    return str(value or "").removeprefix("sha256:")


def _matches(row: object, channel_id: str, thread_id: str, prd: dict[str, Any]) -> bool:
    return (
        isinstance(row, dict) and bool(prd.get("artifact_ref") and prd.get("artifact_digest"))
        and row.get("channel_id") == channel_id
        and str(row.get("thread_id") or "main") == thread_id
        and row.get("source_ref") == prd.get("artifact_ref")
        and _digest(row.get("source_digest")) == _digest(prd.get("artifact_digest"))
        and row.get("prd_revision") == prd.get("prd_revision")
    )


def delivery_evidence(events: list[ZfEvent], channel: dict[str, Any], thread_id: str, prd: dict[str, Any]) -> dict[str, Any]:
    """Join only this PRD's proven Tasks, existing Plan inbox and Run projection."""
    tasks = {
        str(row["task_id"]): row
        for row in channel.get("result_receipts", [])
        if row.get("receipt_kind") == "task_created"
        and str(row.get("thread_id") or "main") == thread_id
        and row.get("artifact_ref") == prd.get("artifact_ref")
        and _digest(row.get("artifact_digest")) == _digest(prd.get("artifact_digest"))
        and row.get("revision") == prd.get("prd_revision")
        and row.get("task_id")
    }
    plans = [
        {"request_id": row.get("request_id"), "subject_type": row.get("subject_type"),
         "task_id": row.get("task_id"), "status": "awaiting_approval"}
        for row in pending_kanban_plan_requests(events)
        if _matches(row.get("workflow_parameters") or {}, channel["channel_id"], thread_id, prd)
        and row.get("subject_type") in {"task_create", "task_workflow"}
    ]
    for proposal in pending_kanban_proposals(events):
        payload = proposal["payload"]
        action = proposal["action"]
        authority = payload.get("channel_authority") or payload.get("parameters") or {}
        if action not in {"create-task", "workflow-start", "task-workflow-start"}:
            continue
        if not _matches(authority, channel["channel_id"], thread_id, prd):
            continue
        plans.append({"request_id": proposal["proposal_id"], "task_id": payload.get("task_id") or "",
                      "subject_type": "task_create" if action == "create-task" else "task_workflow",
                      "status": "awaiting_approval" if proposal["valid"] else "needs_revision"})
    projection = build_run_admission_projection(events)
    runs = [
        {"task_id": entry.task_id, "workflow_run_id": entry.run_id,
         "status": entry.status, "event_id": entry.event_ids[-1],
         "reason": entry.blocker, "queue_position": projection.queue_position(entry.run_id)}
        for entry in projection.runs.values()
        if entry.task_id in tasks and entry.event_ids
    ]
    # A real planning turn is not a Workflow run. Follow its causation chain,
    # never a global latest Agent run or an unrelated conversation's Plan.
    planning: dict[str, Any] = {}
    descendants: set[str] = set()
    for event in events:
        payload = event.payload or {}
        request = payload.get("request") or {}
        if (event.type == "web.action.requested"
                and request.get("source") == "web-channel-workflow-plan"
                and _matches(request.get("workflow_context") or {}, channel["channel_id"], thread_id, prd)):
            descendants = {event.id}
            planning = {"status": "requested", "event_id": event.id,
                        "task_id": request.get("task_id") or ""}
        elif event.causation_id in descendants:
            descendants.add(event.id)
            status = {
                "kanban.agent.turn.started": "planning",
                "kanban.agent.turn.completed": "unknown",
                "kanban.agent.turn.failed": "failed",
                "runtime.action.rejected": "failed",
                "web.action.failed": "failed",
            }.get(event.type)
            if status:
                planning.update(status=status, event_id=event.id)
    return {"plans": plans, "runs": runs, "planning": planning}


def build_channel_delivery_router(
    *, default_project_id: str, default_state_dir: Path,
    default_config: ZfConfig | None, default_project_root: Path,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/projects/{project_id}/channels/{channel_id}/file-preview")
    def channel_file(project_id: str, channel_id: str, thread_id: str, ref: str) -> dict[str, Any]:
        from zf.web.channel_file_preview import channel_file_preview

        context = _resolve_api_project(
            project_id, default_project_id=default_project_id,
            default_state_dir=default_state_dir, default_config=default_config,
            default_project_root=default_project_root,
        )
        channel = project_channel(context.state_dir, channel_id, include_linked_events=False)
        if channel is None:
            raise HTTPException(404, "Channel not found")
        return channel_file_preview(project_root=context.project_root, state_dir=context.state_dir,
                                    channel=channel, thread_id=thread_id, ref=ref)

    def selection(project_id: str, channel_id: str, thread_id: str, revision: int, digest: str) -> tuple[ProjectContext, list[ZfEvent], dict[str, Any], dict[str, Any]]:
        context = _resolve_api_project(
            project_id, default_project_id=default_project_id,
            default_state_dir=default_state_dir, default_config=default_config,
            default_project_root=default_project_root,
        )
        events = event_log_from_project(context.state_dir, config=context.config).read_all()
        channel = project_channel(context.state_dir, channel_id, events=events, include_linked_events=False)
        if channel is None:
            raise HTTPException(404, "Channel not found")
        prd = next((row for row in reversed(channel.get("syntheses", []))
                    if str(row.get("thread_id") or "main") == thread_id), None)
        if not prd or not digest or not prd.get("artifact_ref") or prd.get("prd_revision") != revision or _digest(prd.get("artifact_digest")) != _digest(digest):
            raise HTTPException(409, "PRD version changed or is unavailable. Refresh the Channel.")
        return context, events, channel, prd

    @router.get("/api/projects/{project_id}/channels/{channel_id}/delivery")
    def channel_delivery(project_id: str, channel_id: str, thread_id: str, revision: int, digest: str) -> dict[str, Any]:
        _, events, channel, prd = selection(project_id, channel_id, thread_id, revision, digest)
        return {"schema_version": "channel-delivery-view.v1", "revision": revision,
                "artifact_digest": prd["artifact_digest"], **delivery_evidence(events, channel, thread_id, prd)}

    @router.get("/api/projects/{project_id}/channels/{channel_id}/prd-evaluation")
    def channel_prd_evaluation(project_id: str, channel_id: str, thread_id: str,
                               revision: int, digest: str) -> dict[str, Any]:
        context, events, _, prd = selection(project_id, channel_id, thread_id, revision, digest)
        rows: dict[str, dict[str, Any]] = {}
        for event in events:
            if event.type != "channel.prd_evaluation.requested":
                continue
            payload = event.payload or {}
            if (payload.get("channel_id") != channel_id or
                str(payload.get("thread_id") or "main") != thread_id or
                payload.get("prd_revision") != revision or
                payload.get("artifact_ref") != prd.get("artifact_ref") or
                _digest(payload.get("artifact_digest")) != _digest(digest)):
                continue
            role = str(payload.get("role") or "full")
            rows[role] = {"role": role, "status": "requested", "verdict": "",
                          "request_id": payload.get("request_id"), "findings": []}
        terminals = {str((event.payload or {}).get("request_id") or ""): event
                     for event in events if event.type in {
                         "output.evaluation.completed", "output.evaluation.failed"}}
        for row in rows.values():
            feedback = next((event for event in reversed(events)
                             if event.type.startswith("channel.prd_evaluation.feedback.")
                             and (event.payload or {}).get("request_id") == row["request_id"]), None)
            if feedback:
                row["feedback"] = {"status": feedback.type.rsplit(".", 1)[-1],
                                   "reason": str((feedback.payload or {}).get("reason") or ""),
                                   "review": (feedback.payload or {}).get("review")}
            terminal = terminals.get(str(row["request_id"]))
            if terminal is None:
                continue
            payload = terminal.payload or {}
            row.update(status="completed" if terminal.type.endswith("completed") else "failed",
                       verdict=str(payload.get("verdict") or "INDETERMINATE"),
                       reason=str(payload.get("reason") or ""),
                       report_ref=str(payload.get("report_ref") or ""),
                       report_digest=str(payload.get("report_digest") or ""))
            if row["report_ref"] and row["report_digest"]:
                try:
                    report_doc = hydrate_sidecar_ref(
                        Path(context.state_dir),
                        {"ref": row["report_ref"], "sha256": row["report_digest"],
                         "content_type": "application/json"},
                        purpose="channel-prd-evaluation-view", actor="web")
                    report = report_doc.payload
                    if (not isinstance(report, dict) or report.get("verdict") != row["verdict"]
                        or report.get("request_id") != row["request_id"]
                        or report.get("manifest_ref") != payload.get("manifest_ref")
                        or _digest(report.get("manifest_digest")) != _digest(payload.get("manifest_digest"))):
                        raise ValueError("report identity mismatch")
                    manifest = hydrate_sidecar_ref(
                        Path(context.state_dir),
                        {"ref": payload.get("manifest_ref"), "sha256": payload.get("manifest_digest"),
                         "content_type": "application/json"},
                        purpose="channel-prd-evaluation-manifest", actor="web").payload
                    if (not isinstance(manifest, dict)
                        or (manifest.get("context") or {}).get("channel_id") != channel_id
                        or (manifest.get("context") or {}).get("thread_id") != thread_id
                        or (manifest.get("target") or {}).get("ref") != prd.get("artifact_ref")
                        or (manifest.get("target") or {}).get("revision") != revision):
                        raise ValueError("evaluation target identity mismatch")
                    row["findings"] = list(report.get("findings") or [])[:20]
                    row["coverage"] = report.get("coverage") or {}
                    row["score"] = report.get("score")
                except (SidecarRefError, ValueError):
                    row.update(status="invalid_evidence", verdict="INDETERMINATE",
                               reason="report sidecar cannot be verified")
        return {"schema_version": "channel-prd-evaluation-view.v1",
                "revision": revision, "artifact_digest": prd["artifact_digest"],
                "evaluations": [rows[role] for role in ("full", "focused") if role in rows]}

    @router.get("/api/projects/{project_id}/channels/{channel_id}/prd-preview")
    def channel_prd_preview(project_id: str, channel_id: str, thread_id: str, revision: int, digest: str) -> dict[str, Any]:
        context, events, channel, prd = selection(project_id, channel_id, thread_id, revision, digest)
        try:
            doc = hydrate_sidecar_ref(
                Path(context.state_dir),
                {"ref": prd["artifact_ref"], "sha256": prd["artifact_digest"], "content_type": "application/json"},
                purpose="channel-prd-preview", actor="web",
            )
        except SidecarRefError as exc:
            code = 413 if exc.code == "payload_too_large" else 409
            raise HTTPException(code, f"PRD cannot be verified ({exc.code}). No partial document is shown.") from exc
        payload = doc.payload
        if not isinstance(payload, dict) or (
            payload.get("schema_version") != "channel-prd.v1"
            or payload.get("channel_id") != channel["channel_id"]
            or payload.get("thread_id") != thread_id or payload.get("revision") != revision
        ):
            raise HTTPException(409, "PRD identity mismatch")
        body = payload.get("body") or {}
        markdown = body.get("markdown") if isinstance(body, dict) else None
        if not isinstance(markdown, str) or not markdown.strip():
            raise HTTPException(409, "PRD Markdown body is missing; summary is not a full preview")
        readiness = None
        readiness_ref = str(payload.get("readiness_ref") or "")
        readiness_digest = str(payload.get("readiness_digest") or "")
        if readiness_ref or readiness_digest or prd.get("readiness_ref") or prd.get("readiness_digest"):
            if (not readiness_ref or not readiness_digest
                    or readiness_ref != prd.get("readiness_ref")
                    or _digest(readiness_digest) != _digest(prd.get("readiness_digest"))):
                raise HTTPException(409, "PRD readiness binding mismatch")
            try:
                ready_doc = hydrate_sidecar_ref(Path(context.state_dir),
                    {"ref": readiness_ref, "sha256": readiness_digest, "content_type": "application/json"},
                    purpose="channel-prd-readiness-preview", actor="web")
            except SidecarRefError as exc:
                raise HTTPException(413 if exc.code == "payload_too_large" else 409,
                                    f"PRD readiness cannot be verified ({exc.code})") from exc
            ready = ready_doc.payload
            if not isinstance(ready, dict) or (ready.get("schema_version") != "channel-prd-readiness.v1"
                    or ready.get("channel_id") != channel["channel_id"]
                    or ready.get("thread_id") != thread_id or ready.get("revision") != revision):
                raise HTTPException(409, "PRD readiness identity mismatch")
            readiness = {"ref": ready_doc.ref, "digest": ready_doc.sha256,
                         **{key: ready.get(key) for key in ("verdict", "implementation_start", "reason", "gaps", "risks")}}
        from zf.web.channel_prd_sources import prd_source_view

        synthesis = body.get("synthesis") if isinstance(body.get("synthesis"), dict) else {}
        return {"schema_version": "channel-prd-preview.v1", "channel_id": channel["channel_id"],
                "thread_id": thread_id, "revision": revision, "artifact_ref": doc.ref,
                "artifact_digest": doc.sha256, "title": str(body.get("title") or "PRD"),
                "markdown": markdown, "byte_count": doc.byte_count, "readiness": readiness,
                "sources": prd_source_view(Path(context.state_dir), channel, events, thread_id, synthesis),
                "requirement_coverage": synthesis.get("requirement_coverage") if isinstance(synthesis.get("requirement_coverage"), list) else []}

    return router
