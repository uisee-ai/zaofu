"""Controlled actions for External Issue clarification."""

from __future__ import annotations

from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.control_actions_helpers import _task_id_from_payload
from zf.runtime.external_issue_clarification_service import (
    ExternalIssueClarificationService,
)


EXTERNAL_ISSUE_ACTIONS = frozenset({
    "external-issue-triage-start",
    "external-issue-needs-info-submit",
    "external-issue-needs-info-github-device-start",
    "external-issue-needs-info-github-device-poll",
    "external-issue-triage-queued-cancel",
    "external-issue-fix-queued-cancel",
    "external-issue-candidate-review",
    "external-issue-candidate-prepare",
    "external-issue-candidate-record-pr",
    "external-issue-candidate-refresh-pr",
})


class ExternalIssueActionsMixin:
    def _cancelled_label_projection(
        self, event: ZfEvent, *, defer: bool,
    ) -> dict[str, Any]:
        """Keep older adapter/test overrides compatible with deferred sync."""
        try:
            return self._sync_cancelled_issue_label(event, defer=defer)
        except TypeError as exc:
            if defer and "unexpected keyword argument 'defer'" in str(exc):
                return self._sync_cancelled_issue_label(event)
            raise

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
        if action == "external-issue-triage-start":
            from zf.runtime.external_issue_ingress import (
                ExternalIssueIngressService,
                build_github_lifecycle_label_writer,
            )

            try:
                issue_number = int(payload.get("issue_number") or 0)
                if issue_number < 1:
                    raise ValueError("issue_number is required")
                ingress = ExternalIssueIngressService(
                    self.state_dir,
                    self.config,
                    project_root=self.project_root or self.state_dir.parent,
                    lifecycle_label_writer=build_github_lifecycle_label_writer(),
                )
                # The Feishu Issue-only button already carries the mirrored
                # source revision. Admit the durable local queue entry without
                # refreshing GitHub or downloading attachments in the request
                # path; the normal ingress reconciliation performs those
                # projections afterwards.
                outcome = ingress.start_manual_triage(
                    issue_number,
                    actor=self.actor,
                    expected_source_revision=str(
                        payload.get("source_revision") or ""
                    ),
                    refresh=not bool(str(payload.get("source_revision") or "").strip()),
                    defer_remote_sync=True,
                    defer_attachments=True,
                    lock_timeout_seconds=0.1,
                )
                result = {
                    "ok": bool(outcome.ok),
                    "status": outcome.status,
                    "issue_number": outcome.issue_number,
                    "queued": outcome.queued,
                    "task_id": outcome.task_id,
                    "workflow_run_id": outcome.workflow_run_id,
                    "source_revision": outcome.source_revision,
                    "workflow_project_id": outcome.workflow_project_id,
                    "workflow_project_name": outcome.workflow_project_name,
                    "execution_project_root": outcome.execution_project_root,
                    "reason": outcome.error,
                }
            except (TypeError, ValueError, OSError) as exc:
                result = {"ok": False, "status": "invalid_external_issue_request", "reason": str(exc)}
            result.setdefault("action", action)
            result.setdefault("requested_action", requested_action)
            return result
        if action.startswith("external-issue-candidate-"):
            from zf.core.issue_triage.store import IssueMirrorStore
            from zf.runtime.issue_candidate_delivery import IssueCandidateDeliveryService
            from zf.runtime.external_issue_ingress import build_github_lifecycle_label_writer

            issue_number = int(payload.get("issue_number") or 0)
            issue = IssueMirrorStore(self.state_dir).get(issue_number)
            if issue is None:
                return self._failed(requested=requested, action=action, requested_action=requested_action,
                                    task_id=_task_id_from_payload(payload), reason="GitHub Issue is not mirrored",
                                    status_code=404, status="not_found")
            delivery = IssueCandidateDeliveryService(
                state_dir=self.state_dir,
                project_root=self.project_root or self.state_dir.parent,
                config=self.config,
                actor=self.actor,
                lifecycle_label_writer=build_github_lifecycle_label_writer(),
                # Feishu button actions should acknowledge the durable local
                # handoff immediately. Lifecycle labels are a remote
                # projection and are reconciled by the normal ingress/label
                # sync loop after the action has returned.
                defer_remote_sync=True,
            )
            try:
                if action == "external-issue-candidate-review":
                    result = delivery.review(issue, verdict=str(payload.get("verdict") or ""),
                                             expected_candidate_sha=str(payload.get("candidate_sha") or ""),
                                             expected_source_revision=str(payload.get("source_revision") or ""),
                                             reason=str(payload.get("reason") or ""))
                elif action == "external-issue-candidate-prepare":
                    result = delivery.prepare(issue, expected_candidate_sha=str(payload.get("candidate_sha") or ""),
                                              expected_source_revision=str(payload.get("source_revision") or ""))
                elif action == "external-issue-candidate-record-pr":
                    result = delivery.record_pr(issue, url=str(payload.get("url") or ""))
                else:
                    result = delivery.refresh_pr(issue)
            except (ValueError, PermissionError) as exc:
                return self._failed(requested=requested, action=action, requested_action=requested_action,
                                    task_id=_task_id_from_payload(payload), reason=str(exc),
                                    status_code=422, status="invalid_external_issue_delivery")
            result.setdefault("action", action)
            result.setdefault("requested_action", requested_action)
            return result
        if action in {"external-issue-triage-queued-cancel", "external-issue-fix-queued-cancel"}:
            return self._cancel_queued_triage(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
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

    def _cancel_queued_triage(
        self, *, requested: ZfEvent, action: str, requested_action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        triage_id = str(payload.get("triage_id") or payload.get("workflow_run_id") or "").strip()
        if not triage_id or len(triage_id) > 200:
            return self._failed(requested=requested, action=action, requested_action=requested_action,
                                task_id=_task_id_from_payload(payload), reason="triage_id is required",
                                status_code=422, status="invalid_request")
        events = self.writer.event_log.read_all()
        is_fix = action.endswith("fix-queued-cancel")
        queued = next((event for event in reversed(events)
                       if ((event.type == "workflow.invoke.requested" and is_fix
                            and str(event.payload.get("flow_kind") or "") == "issue")
                           or (event.type == "external_issue.triage.queued" and not is_fix))
                       and str(event.payload.get("workflow_run_id") or "") == triage_id), None)
        if queued is None:
            return self._failed(requested=requested, action=action, requested_action=requested_action,
                                task_id=_task_id_from_payload(payload), reason=f"Queued Triage not found: {triage_id}",
                                status_code=404, status="not_found")
        attempt = int(queued.payload.get("triage_attempt") or 1)
        expected = payload.get("expected_revision")
        if expected is not None and int(expected) != attempt:
            return self._failed(requested=requested, action=action, requested_action=requested_action,
                                task_id=queued.task_id, reason="queued Triage revision is stale",
                                status_code=409, status="revision_conflict",
                                extra={"triage_id": triage_id, "current_revision": attempt})
        cancel_event_type = "external_issue.fix.queued_cancelled" if action.endswith("fix-queued-cancel") else "external_issue.triage.queued_cancelled"
        terminal = any(event.type in {
            "external_issue.triage.queued_cancelled", "external_issue.fix.queued_cancelled", "run.cancelled", "run.failed", "run.abandoned",
            "run.goal.blocked", "run.goal.completed", "workflow.invoke.accepted",
        } and str(event.payload.get("workflow_run_id") or event.payload.get("run_id") or event.correlation_id or "") == triage_id
                       for event in events)
        if terminal:
            existing = next((event for event in reversed(events)
                             if event.type == cancel_event_type
                             and str(event.payload.get("workflow_run_id") or "") == triage_id), None)
            if existing:
                return {"_status_code": 200, "ok": True, "status": "cancelled", "action": action,
                        "requested_action": requested_action, "triage_id": triage_id,
                        "event_id": existing.id, "idempotent_replay": True,
                        "lifecycle_label_sync": self._cancelled_label_projection(
                            existing,
                            defer=bool(payload.get("defer_lifecycle_label_sync")),
                        )}
            return self._failed(requested=requested, action=action, requested_action=requested_action,
                                task_id=queued.task_id, reason="queued Triage is no longer cancellable",
                                status_code=409, status="invalid_transition")
        event = self.writer.emit(
            cancel_event_type, actor=self.actor, task_id=queued.task_id,
            causation_id=requested.id, correlation_id=triage_id,
            payload={"schema_version": "external-issue-triage-queue.v1", "workflow_run_id": triage_id,
                     "triage_id": triage_id, "source_key": queued.payload.get("source_key", ""),
                     "source_revision": queued.payload.get("source_revision", ""),
                     "revision": attempt, "reason": str(payload.get("reason") or "operator cancelled queued triage")},
        )
        self._completed(requested=requested, event=event, action=action, requested_action=requested_action,
                        status="cancelled", task_id=queued.task_id,
                        extra={"triage_id": triage_id, "idempotent_replay": False})
        return {"_status_code": 200, "ok": True, "status": "cancelled", "action": action,
                "requested_action": requested_action, "triage_id": triage_id, "event_id": event.id,
                "idempotent_replay": False,
                "lifecycle_label_sync": self._cancelled_label_projection(
                    event,
                    defer=bool(payload.get("defer_lifecycle_label_sync")),
                )}

    def _sync_cancelled_issue_label(
        self,
        event: ZfEvent,
        *,
        defer: bool = False,
    ) -> dict[str, Any]:
        if defer:
            return {"status": "pending", "reason": "scheduled_after_response"}
        from zf.runtime.external_issue_ingress import (
            ExternalIssueIngressService, build_github_lifecycle_label_writer,
        )

        try:
            service = ExternalIssueIngressService(
                self.state_dir, self.config,
                project_root=self.project_root or self.state_dir.parent,
                lifecycle_label_writer=build_github_lifecycle_label_writer(),
            )
            return dict(service.reconcile_queue_cancellation(event))
        except (ValueError, OSError) as exc:
            # Cancellation is already durable; never report it as failed just
            # because the remote projection could not be synchronized.
            return {"status": "outcome_unknown", "reason": type(exc).__name__}


__all__ = ["EXTERNAL_ISSUE_ACTIONS", "ExternalIssueActionsMixin"]
