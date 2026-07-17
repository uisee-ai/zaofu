"""Controlled Project Request creation and explicit workflow ignition."""

from __future__ import annotations

import hashlib
from pathlib import Path

from zf.core.events import ZfEvent
from zf.runtime.control_actions_helpers import _required_text, _string_list


class WorkflowRequestActionsMixin:
    def _workflow_request(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        if self.project_root is None or self.config is None:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="initialized project context is required",
                status_code=409,
                status="project_initialization_required",
            )
        config_ref = Path(
            _required_text(payload, "config_ref") or self.project_root / "zf.yaml"
        ).expanduser()
        if not config_ref.is_absolute():
            config_ref = self.project_root / config_ref
        if not config_ref.exists():
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason=f"workflow config does not exist: {config_ref}",
                status_code=409,
                status="project_initialization_required",
            )

        objective = (
            _required_text(payload, "objective")
            or _required_text(payload, "message")
            or _required_text(payload, "title")
        )
        request_id = _required_text(payload, "request_id") or _stable_request_id(
            self.project_root,
            requested.id,
            objective,
        )
        source_ref = _project_ref(
            self.project_root,
            self.state_dir,
            _required_text(payload, "source_ref") or _required_text(payload, "artifact_ref"),
        )
        from zf.cli.flow import (
            build_flow_intake,
            build_flow_submit_preview,
        )

        intake = build_flow_intake(
            kind=_required_text(payload, "kind") or "auto",
            source_ref=source_ref,
            objective=objective,
            source_root=_required_text(payload, "source_root"),
            target_root=_required_text(payload, "target_root") or _required_text(payload, "target"),
            backend=_required_text(payload, "backend"),
            lanes=int(payload.get("lanes") or payload.get("requested_lanes") or 0),
            project_id=_required_text(payload, "project_id") or self.config.project.name,
            project_name=self.config.project.name,
            strictness=_required_text(payload, "strictness") or "standard",
            acceptance=tuple(_string_list(payload.get("acceptance"))),
            constraints=tuple(_string_list(payload.get("constraints"))),
            open_questions=tuple(_string_list(payload.get("open_questions"))),
            request_id=request_id,
            source=self.surface,
            created_by=self.actor,
            channel_id=_required_text(payload, "channel_id"),
            thread_id=_required_text(payload, "thread_id"),
            output=self.project_root / "docs" / "intake" / f"{request_id}.md",
        )
        preview = build_flow_submit_preview(
            config_path=config_ref,
            intake_path=Path(str(intake["intake_ref"])),
            flow_kind=_required_text(payload, "kind"),
            task_id=_required_text(payload, "task_id"),
            pattern_id=_required_text(payload, "pattern_id"),
            requested_by=self.actor,
            reason=_required_text(payload, "reason") or "workflow request proposal",
            allow_missing_env=bool(payload.get("allow_missing_env")),
        )
        ready = preview.get("status") != "STOP"
        return {
            "_status_code": 202 if ready else 409,
            "ok": ready,
            "status": "proposal_ready" if ready else "clarification_required",
            "action": action,
            "requested_action": requested_action,
            "request_id": request_id,
            "intake_ref": str(intake["intake_ref"]),
            "workflow_input_manifest_ref": str(intake["workflow_input_manifest_ref"]),
            "request_projection_ref": str(intake.get("request_projection_ref") or ""),
            "submit_preview_ref": str(preview.get("submit_preview_ref") or ""),
            "blockers": list(preview.get("blockers") or []),
        }

    def _workflow_submit(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        if self.project_root is None or self.config is None:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="initialized project context is required",
                status_code=409,
                status="project_initialization_required",
            )
        intake_ref = _required_text(payload, "intake_ref") or _required_text(payload, "intake")
        if not intake_ref or intake_ref == "<created-intake-ref>":
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="intake_ref is required",
                status_code=422,
                status="invalid_payload",
            )
        intake_path = Path(intake_ref).expanduser()
        if not intake_path.is_absolute():
            intake_path = self.project_root / intake_path
        config_ref = Path(
            _required_text(payload, "config_ref") or self.project_root / "zf.yaml"
        ).expanduser()
        if not config_ref.is_absolute():
            config_ref = self.project_root / config_ref
        from zf.cli.flow import apply_flow_submit

        result = apply_flow_submit(
            config_path=config_ref,
            intake_path=intake_path,
            flow_kind=_required_text(payload, "kind"),
            task_id=_required_text(payload, "task_id"),
            pattern_id=_required_text(payload, "pattern_id"),
            requested_by=self.actor,
            reason=_required_text(payload, "reason") or "approved workflow request",
            allow_missing_env=bool(payload.get("allow_missing_env")),
        )
        accepted = result.get("status") != "STOP"
        return {
            "_status_code": 202 if accepted else 409,
            "ok": accepted,
            "status": str(result.get("status") or "STOP"),
            "action": action,
            "requested_action": requested_action,
            "request_id": str((result.get("payload") or {}).get("request_id") or ""),
            "workflow_invoke_event_id": str(result.get("workflow_invoke_event_id") or ""),
            "event_ids": list(result.get("event_ids") or []),
            "blockers": list(result.get("blockers") or []),
        }


def _stable_request_id(project_root: Path, requested_event_id: str, objective: str) -> str:
    digest = hashlib.sha256(
        f"{project_root.resolve()}\0{requested_event_id}\0{objective}".encode("utf-8")
    ).hexdigest()[:16]
    return f"workflow-{digest}"


def _project_ref(project_root: Path, state_dir: Path, raw: str) -> str:
    if not raw:
        return ""
    path = Path(raw).expanduser()
    if path.is_absolute():
        return str(path)
    for candidate in (project_root / path, state_dir / path):
        if candidate.exists():
            return str(candidate)
    return raw
