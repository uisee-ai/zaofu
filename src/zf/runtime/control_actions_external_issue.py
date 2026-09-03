"""Controlled actions for External Issue clarification."""

from __future__ import annotations

from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.control_actions_helpers import _task_id_from_payload
from zf.runtime.external_issue_clarification_service import (
    ExternalIssueClarificationService,
)


EXTERNAL_ISSUE_ACTIONS = frozenset({
    "external-issue-needs-info-submit",
    "external-issue-needs-info-github-device-start",
    "external-issue-needs-info-github-device-poll",
})


class ExternalIssueActionsMixin:
    def _external_issue_action(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if self.config is None:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="External Issue clarification is not configured",
                status_code=409,
                status="unconfigured",
            )
        service = ExternalIssueClarificationService(
            self.state_dir,
            self.config,
            project_root=self.project_root or self.state_dir.parent,
            writer=self.writer,
        )
        operation = {
            "external-issue-needs-info-submit": service.submit,
            "external-issue-needs-info-github-device-start": service.github_device_start,
            "external-issue-needs-info-github-device-poll": service.github_device_poll,
        }[action]
        try:
            result = operation(payload, causation_id=requested.id)
        except (ValueError, PermissionError) as exc:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason=str(exc),
                status_code=422,
                status="invalid_external_issue_clarification",
            )
        result.setdefault("action", action)
        result.setdefault("requested_action", requested_action)
        return result


__all__ = ["EXTERNAL_ISSUE_ACTIONS", "ExternalIssueActionsMixin"]
