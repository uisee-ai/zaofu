"""Web-edge helpers for provider-neutral Self-Issue actions."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.core.security.secret_provider import forge_credential_workspace_id
from zf.runtime.self_issue_service import SelfIssueService


_DRAFT_ACTIONS = frozenset({
    "self-issue-evidence-start",
    "self-issue-evidence-interrupt",
    "self-issue-evidence-resume",
    "self-issue-runtime-check",
    "self-issue-limited-continue",
    "self-issue-attachment-preview",
})
_INTAKE_ACTIONS = frozenset({
    "self-issue-intake-save", "self-issue-intake-submit",
    "self-issue-intake-dismiss", "self-issue-intake-attachment-add",
    "self-issue-intake-attachment-remove",
})


def prepare_self_issue_web_payload(
    payload: dict[str, Any],
    action: str,
    identity: str | None,
    project_id: str | None,
    workspace_root: Path,
    web_base_url: str = "",
) -> None:
    """Bind opaque Web and workspace identities without exposing raw values."""
    identity_material = identity or "trusted:local-user"
    payload["user_id"] = (
        "web-" + hashlib.sha256(identity_material.encode()).hexdigest()
    )
    payload["workspace_id"] = forge_credential_workspace_id(workspace_root)
    if action == "self-issue-capture":
        payload["reporter_context"] = {
            "discovered_by": "user",
            "reported_by": "user",
            "collected_by": "kernel",
            "assessed_by": "orchestrator",
            "role": "user",
            "browser_capture": {
                "requested": True,
                "target": "kanban_board",
                "base_url": str(web_base_url or "")[:500],
                "project_id": str(project_id or "")[:200],
            },
        }


def validate_self_issue_web_action(
    action: str,
    payload: dict[str, Any],
) -> str | None:
    """Return the mechanical Self-Issue Web payload error, if any."""
    if action in _DRAFT_ACTIONS and not str(payload.get("draft_id") or "").strip():
        return "draft_id is required"
    if action in _INTAKE_ACTIONS and not str(payload.get("intake_id") or "").strip():
        return "intake_id is required"
    return None


def build_self_issue_attachment_router(
    *, resolve_ctx: Callable[[str], Any],
) -> APIRouter:
    """Build the Draft-owned local attachment route outside oversized server.py."""
    router = APIRouter()

    @router.get("/api/projects/{project_id}/self-issue/attachments/{draft_id}/{digest}")
    def project_self_issue_attachment(
        project_id: str, draft_id: str, digest: str,
    ) -> FileResponse:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise HTTPException(404, "attachment not found")
        context = resolve_ctx(project_id)
        service = SelfIssueService(
            context.state_dir,
            EventWriter(event_log_from_project(context.state_dir, config=context.config)),
            project_root=context.project_root,
            policy=context.config.self_issue if context.config is not None else None,
        )
        try:
            path, content_type, filename = service.local_attachment_file(
                draft_id=draft_id, digest=digest,
            )
        except ValueError as exc:
            raise HTTPException(404, "attachment not found") from exc
        return FileResponse(
            path,
            media_type=content_type,
            filename=filename,
            content_disposition_type="inline",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    return router
