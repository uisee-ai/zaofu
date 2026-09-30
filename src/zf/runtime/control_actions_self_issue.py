"""Controlled actions for the Self-Issue report-only lifecycle."""

from __future__ import annotations

from pathlib import Path
from dataclasses import replace
from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.control_actions_helpers import _task_id_from_payload
from zf.runtime.self_issue_service import SelfIssueService
from zf.runtime.self_issue_structure_agent import (
    HeadlessIssueStructureAgent,
    configured_structure_backend,
)
from zf.runtime.self_issue_session_host import HeadlessIssueSessionAgent, SelfIssueSessionHost
from zf.runtime.self_issue_target import resolve_self_issue_target_root, source_binding_for
from zf.runtime.self_issue_source_settings import current_source_policy, source_settings, save_source_settings, inspect_source

SELF_ISSUE_ACTIONS = frozenset({
    "self-issue-source-get", "self-issue-source-inspect", "self-issue-source-save",
    "self-issue-capture", "self-issue-update", "self-issue-understanding-retry",
    "self-issue-session-answer", "self-issue-session-confirm",
    "self-issue-session-reanalyze",
    "self-issue-session-materialize",
    "self-issue-session-cancel", "self-issue-session-get", "self-issue-session-retry",
    "self-issue-session-pause",
    "self-issue-session-activity-result",
    "self-issue-session-save-draft", "self-issue-session-draft-version",
    "self-issue-session-recover",
    "self-issue-preview",
    "self-issue-confirm", "self-issue-publish", "self-issue-recover",
    "self-issue-oauth-start", "self-issue-oauth-callback",
    "self-issue-oauth-disconnect", "self-issue-resolve-unknown",
    "self-issue-github-device-start", "self-issue-github-device-poll",
    "self-issue-get", "self-issue-dismiss",
    "self-issue-restore-dismissed", "self-issue-privacy-erase",
    "self-issue-suggestion-dismiss", "self-issue-suggestion-resolve",
    "self-issue-conflict-resolve",
    "self-issue-intake-get", "self-issue-intake-save",
    "self-issue-intake-start", "self-issue-intake-submit",
    "self-issue-intake-dismiss",
    "self-issue-intake-attachment-add", "self-issue-intake-attachment-remove",
    "self-issue-draft-attachment-add", "self-issue-draft-attachment-remove",
    "self-issue-evidence-start", "self-issue-evidence-interrupt",
    "self-issue-evaluation-start", "self-issue-evaluation-cancel",
    "self-issue-evidence-resume", "self-issue-evidence-apply",
    "self-issue-evidence-fail", "self-issue-runtime-check",
    "self-issue-limited-continue",
    "self-issue-attachment-preview", "self-issue-attachment-confirm",
    "self-issue-attachment-prepare", "self-issue-attachment-resolve-unknown",
})

class SelfIssueActionsMixin:
    def _self_issue_action(self, *, requested: ZfEvent, action: str,
                           requested_action: str, payload: dict[str, Any]) -> dict[str, Any]:
        supplied_project_root = self.project_root or self.state_dir.parent
        issue_policy = self.config.self_issue if self.config is not None else None
        issue_policy = current_source_policy(supplied_project_root, issue_policy)
        from zf.runtime.issue_settings import current_issue_policy
        issue_policy = current_issue_policy(supplied_project_root, issue_policy)
        # A disabled Self-Issue policy is a hard gate for every new report
        # entrypoint, including the legacy `/issue+` action.  Reads and
        # session recovery remain available through their dedicated paths.
        if action in {"self-issue-capture", "self-issue-intake-start", "self-issue-intake-submit"} and not issue_policy.enabled:
            return self._failed(
                requested=requested, action=action,
                requested_action=requested_action, task_id=None,
                reason="Self-Issue reporting is disabled in project settings.",
                status_code=409, status="self_issue_disabled",
            )
        if action in {"self-issue-source-get", "self-issue-source-inspect", "self-issue-source-save"}:
            try:
                if action == "self-issue-source-save":
                    result = save_source_settings(supplied_project_root, issue_policy, payload)
                else:
                    result = source_settings(supplied_project_root, issue_policy, payload if action.endswith("inspect") else None)
                if not result.get("ok"):
                    return self._failed(requested=requested, action=action, requested_action=requested_action,
                                        task_id=None, reason=result["reason"], status=result["status"])
                self._completed(requested=requested, event=requested, action=action,
                                requested_action=requested_action, task_id=None,
                                status=str(result.get("status") or "read"),
                                extra={"applies_to": "new_sessions_only", "source_mode": result["source_mode"]})
                return {**result, "action": action}
            except (ValueError, OSError) as exc:
                return self._failed(requested=requested, action=action, requested_action=requested_action,
                                    task_id=None, reason=str(exc))
        requested_source_root = payload.get("source_root") or payload.get("workspace_root")
        requested_source_mode = payload.get("source_mode")
        issue_target_root = resolve_self_issue_target_root(
            supplied_project_root,
            issue_policy,
            source_root=Path(str(requested_source_root)).resolve() if requested_source_root else None,
            source_mode=str(requested_source_mode) if requested_source_mode else None,
        )
        # Settings affect new sessions only. Resume/reanalysis must not silently
        # move an existing investigation to a different checkout.
        if action.startswith("self-issue-session-") and payload.get("session_id"):
            from issue_core import JsonIssueSessionStore
            saved = JsonIssueSessionStore(self.state_dir / "self-issues/portable-sessions").load_session(str(payload["session_id"]))
            pinned = str(saved.source_binding.get("repository_root") or "")
            if pinned:
                issue_target_root = Path(pinned)
                issue_policy = replace(issue_policy, source_mode="explicit_target", source_root=pinned)
        source_binding = source_binding_for(
            issue_target_root,
            mode=str(requested_source_mode or getattr(issue_policy, "source_mode", "current_project")),
        )
        source_binding["workspace_root"] = str(supplied_project_root.resolve())
        if action == "self-issue-capture":
            try:
                source_binding.update(inspect_source(issue_target_root))
            except ValueError as exc:
                return self._failed(requested=requested, action=action, requested_action=requested_action,
                                    task_id=None, reason=str(exc), status="invalid_issue_source")
        structure_backend = (
            configured_structure_backend(self.config)
            if action in {
                "self-issue-capture", "self-issue-understanding-retry",
                "self-issue-session-answer", "self-issue-session-get", "self-issue-session-retry",
                "self-issue-session-recover",
                "self-issue-session-reanalyze",
            }
            and self.config is not None
            else ""
        )
        issue_understander = (
            HeadlessIssueStructureAgent(
                state_dir=self.state_dir,
                project_root=issue_target_root,
                backend=structure_backend,
            )
            if structure_backend else None
        )
        service = SelfIssueService(
            self.state_dir, self.writer,
            project_root=issue_target_root,
            policy=issue_policy,
            issue_understander=issue_understander,
        )
        session_host = SelfIssueSessionHost(
            self.state_dir, issue_target_root,
            agent=(HeadlessIssueSessionAgent(
                state_dir=self.state_dir,
                project_root=issue_target_root,
                backend=structure_backend,
            ) if structure_backend else None),
            policy=issue_policy,
            execution_owner="feishu" if self.source in {"feishu", "feishu-lifecycle"} else self.surface,
        )
        # `/issue` is the portable continuous flow.  Keep accepting requests
        # from an older cached frontend that only sent `direct_report`; those
        # requests must not fall back to the legacy Draft/questionnaire page.
        if action == "self-issue-capture" and (
            bool(payload.get("portable_session")) or bool(payload.get("direct_report"))
        ):
            try:
                target_binding = service._target_binding(payload.get("target_binding") or {})
            except (ValueError, PermissionError) as exc:
                return self._failed(
                    requested=requested, action=action, requested_action=requested_action,
                    task_id=_task_id_from_payload(payload), reason=str(exc),
                    status_code=422, status="invalid_self_issue_request",
                )
            return {
                **session_host.create_and_schedule(
                    str(payload.get("description") or ""),
                    attachments=payload.get("attachments") if isinstance(payload.get("attachments"), list) else None,
                    target_binding=target_binding,
                    source_binding=source_binding,
                ),
                "ok": True,
                "action": action,
                "requested_action": requested_action,
            }
        operation = {
            "self-issue-capture": service.capture,
            "self-issue-update": service.update,
            "self-issue-understanding-retry": service.retry_understanding,
            "self-issue-session-answer": lambda value, **_: {
                "ok": True,
                **session_host.answer_and_schedule(
                    str(value.get("session_id") or ""), str(value.get("answer") or ""),
                    attachments=value.get("attachments")
                    if isinstance(value.get("attachments"), list) else None,
                ),
            },
            "self-issue-session-reanalyze": lambda value, **_: {
                "ok": True,
                **session_host.reanalyze_and_schedule(
                    str(value.get("session_id") or ""),
                    value.get("selected_fields") if isinstance(value.get("selected_fields"), list) else (),
                    str(value.get("comment") or ""),
                ),
            },
            "self-issue-session-confirm": lambda value, **_: {
                "ok": True,
                **session_host.confirm(
                    str(value.get("session_id") or ""),
                    confirmed_by=str(value.get("confirmed_by") or "user"),
                    provider=str(value.get("provider") or ""),
                    target=str(value.get("target") or ""),
                    disclosure_policy=str(value.get("disclosure_policy") or "handoff_private"),
                ),
            },
            "self-issue-session-materialize": lambda value, **_: {
                "ok": True,
                **session_host.materialize_publication_draft(
                    str(value.get("session_id") or ""),
                    report_index=int(value.get("report_index", -1)),
                ),
            },
            "self-issue-session-cancel": lambda value, **_: {
                "ok": True,
                **session_host.cancel(str(value.get("session_id") or "")),
            },
            "self-issue-session-pause": lambda value, **_: {
                "ok": True,
                **session_host.pause_and_resume_later(
                    str(value.get("session_id") or ""),
                    host_shutdown=self.source == "feishu-lifecycle",
                ),
            },
            "self-issue-session-activity-result": lambda value, **_: {
                "ok": True,
                **session_host.activity_result(str(value.get("session_id") or ""), str(value.get("activity_id") or "")),
            },
            "self-issue-session-save-draft": lambda value, **_: session_host.save_editor(
                str(value.get("session_id") or ""), revision=int(value.get("revision") or 0),
                title=str(value.get("title") or ""), body=str(value.get("body") or ""),
            ),
            "self-issue-session-draft-version": lambda value, **_: session_host.draft_version(
                str(value.get("session_id") or ""), int(value.get("version") or 0),
            ),
            "self-issue-session-get": lambda value, **_: {
                "ok": True,
                **session_host.get(str(value.get("session_id") or "")),
            },
            "self-issue-session-retry": lambda value, **_: {
                "ok": True,
                **session_host.retry_and_schedule(str(value.get("session_id") or "")),
            },
            "self-issue-session-recover": lambda value, **_: {
                "ok": True,
                **session_host.recover_and_schedule(
                    str(value.get("session_id") or ""), automatic=self.source == "feishu-lifecycle",
                ),
            },
            "self-issue-preview": service.preview,
            "self-issue-confirm": service.confirm,
            "self-issue-publish": service.publish,
            "self-issue-recover": service.recover,
            "self-issue-oauth-start": service.oauth_start,
            "self-issue-oauth-callback": service.oauth_callback,
            "self-issue-oauth-disconnect": service.oauth_disconnect,
            "self-issue-github-device-start": service.github_device_start,
            "self-issue-github-device-poll": service.github_device_poll,
            "self-issue-resolve-unknown": service.resolve_unknown,
            "self-issue-get": service.get,
            "self-issue-dismiss": service.dismiss,
            "self-issue-restore-dismissed": service.restore_dismissed,
            "self-issue-privacy-erase": service.privacy_erase,
            "self-issue-suggestion-dismiss": service.dismiss_suggestion,
            "self-issue-suggestion-resolve": service.resolve_suggestion,
            "self-issue-conflict-resolve": service.resolve_conflict,
            "self-issue-intake-get": service.get_intake,
            "self-issue-intake-start": service.start_intake,
            "self-issue-intake-save": service.save_intake,
            "self-issue-intake-submit": service.submit_intake,
            "self-issue-intake-dismiss": service.dismiss_intake,
            "self-issue-intake-attachment-add": service.add_intake_attachment,
            "self-issue-intake-attachment-remove": service.remove_intake_attachment,
            "self-issue-draft-attachment-add": service.add_draft_attachment,
            "self-issue-draft-attachment-remove": service.remove_draft_attachment,
            "self-issue-evidence-start": service.start_evidence,
            "self-issue-evidence-interrupt": service.interrupt_evidence,
            "self-issue-evaluation-start": service.start_evidence,
            "self-issue-evaluation-cancel": service.interrupt_evidence,
            "self-issue-evidence-resume": service.resume_evidence,
            "self-issue-evidence-apply": service.apply_evidence_assessment,
            "self-issue-evidence-fail": service.fail_evidence,
            "self-issue-runtime-check": service.check_runtime,
            "self-issue-limited-continue": service.continue_limited,
            "self-issue-attachment-preview": service.attachment_preview,
            "self-issue-attachment-confirm": service.attachment_confirm,
            "self-issue-attachment-prepare": service.attachment_prepare,
            "self-issue-attachment-resolve-unknown": service.resolve_attachment_unknown,
        }[action]
        try:
            result = operation(payload, causation_id=requested.id)
        except (ValueError, PermissionError) as exc:
            return self._failed(
                requested=requested, action=action, requested_action=requested_action,
                task_id=_task_id_from_payload(payload), reason=str(exc),
                status_code=422, status="invalid_self_issue_request",
            )
        should_cancel_evidence = (
            action in {"self-issue-evidence-interrupt", "self-issue-evaluation-cancel"}
            and result.get("status") == "evidence_interrupted"
        ) or (
            action == "self-issue-dismiss" and result.get("cancel_evidence")
        )
        if should_cancel_evidence:
            from zf.web.agent_session_runtime import cancel_agent_session_run, run_key

            cancelled = cancel_agent_session_run(run_key(
                run_id=str(result.get("run_id") or ""),
                thread_id=str(result.get("thread_id") or ""),
            ))
            result.update({
                "interrupt_status": cancelled.status,
                "interrupt_supported": cancelled.interrupt_supported,
                "process_found": cancelled.process_found,
                "process_terminated": cancelled.process_terminated,
            })
        result.setdefault("action", action)
        result.setdefault("requested_action", requested_action)
        return result
