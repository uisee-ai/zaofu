"""Feishu Issue/Triage/Self-Issue edge adapter.

Commands are untrusted; mutations always go through ``ControlledActionService``.
"""

from __future__ import annotations

import hashlib
import base64
import json
import mimetypes
import time
import urllib.parse
from pathlib import Path
from typing import Any

from zf.core.events import EventWriter
from zf.core.events.factory import event_log_from_project
from zf.core.issue_triage.store import IssueMirrorStore
from zf.core.self_issue.store import (
    IssueDraftStore,
    PublicationBatchStore,
    SelfIssueIntakeStore,
)
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.integrations.feishu.callback_token import attach_action_token
from zf.integrations.feishu.storage import IdempotencyStore
from zf.integrations.feishu.transport import (
    FeishuMessage, FeishuTransport, FeishuTransportError, extract_message_resources,
)
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.self_issue_intake import ALLOWED_ATTACHMENTS
from zf.integrations.feishu.self_issue_cards import (
    build_self_issue_card,
    push_self_issue_cards_once,
)

ISSUE_QUERY_COMMANDS = frozenset({"issue-summary", "issue-list", "issue-show"})
ISSUE_MUTATION_COMMANDS = frozenset({
    "issue-triage-start", "issue-triage-cancel", "issue-fix-cancel", "issue-needs-info-submit",
    "issue-candidate-review", "issue-candidate-prepare", "issue-candidate-pr",
    "issue-candidate-pr-refresh",
    "issue-fix-proposal-approve", "issue-fix-proposal-reject", "issue-fix-proposal-refresh",
    "issue-run-pause", "issue-run-resume", "issue-run-cancel",
})
SELF_ISSUE_COMMANDS = frozenset({
    "self-issue-session-answer", "self-issue-session-confirm",
    "self-issue-session-cancel", "self-issue-session-get",
    "self-issue-session-retry", "self-issue-session-pause",
    "self-issue-session-reanalyze", "self-issue-session-materialize",
    "self-issue-session-activity-result",
    "self-issue-new", "self-issue-intake-start", "self-issue-get", "self-issue-intake-get",
    "self-issue-save", "self-issue-intake-save", "self-issue-submit", "self-issue-intake-submit",
    "self-issue-dismiss", "self-issue-intake-dismiss", "self-issue-preview", "self-issue-confirm",
    "self-issue-publish", "self-issue-recover", "self-issue-resolve-unknown",
    "self-issue-github-device-start", "self-issue-github-device-poll",
    "self-issue-attachment-add", "self-issue-attachment-remove",
    "self-issue-intake-attachment-add", "self-issue-intake-attachment-remove",
    "self-issue-evidence-start", "self-issue-evidence-interrupt",
    "self-issue-evidence-resume", "self-issue-evidence-apply",
    "self-issue-evidence-fail", "self-issue-runtime-check",
    "self-issue-limited-continue", "self-issue-attachment-preview",
    "self-issue-attachment-confirm", "self-issue-attachment-prepare",
    "self-issue-attachment-resolve-unknown",
})

ISSUE_COMMANDS = ISSUE_QUERY_COMMANDS | ISSUE_MUTATION_COMMANDS | SELF_ISSUE_COMMANDS

_ACTION_BY_COMMAND = {
    "issue-triage-start": "external-issue-triage-start",
    "issue-triage-cancel": "external-issue-triage-queued-cancel",
    "issue-fix-cancel": "external-issue-fix-queued-cancel",
    "issue-needs-info-submit": "external-issue-needs-info-submit",
    "issue-candidate-review": "external-issue-candidate-review",
    "issue-candidate-prepare": "external-issue-candidate-prepare",
    "issue-candidate-pr": "external-issue-candidate-record-pr",
    "issue-candidate-pr-refresh": "external-issue-candidate-refresh-pr",
    "issue-fix-proposal-approve": "workflow-start",
    "issue-fix-proposal-reject": "kanban-proposal-dismiss",
    "issue-run-pause": "run-pause",
    "issue-run-resume": "run-resume",
    "issue-run-cancel": "run-cancel",
}
_SELF_ACTION_BY_COMMAND = {
    "self-issue-new": "self-issue-capture",
    "self-issue-intake-start": "self-issue-intake-start",
    "self-issue-get": "self-issue-get",
    "self-issue-intake-get": "self-issue-intake-get",
    "self-issue-save": "self-issue-update",
    "self-issue-intake-save": "self-issue-intake-save",
    "self-issue-submit": "self-issue-intake-submit",
    "self-issue-intake-submit": "self-issue-intake-submit",
    "self-issue-dismiss": "self-issue-intake-dismiss",
    "self-issue-intake-dismiss": "self-issue-intake-dismiss",
    "self-issue-session-answer": "self-issue-session-answer",
    "self-issue-session-confirm": "self-issue-session-confirm",
    "self-issue-session-cancel": "self-issue-session-cancel",
    "self-issue-session-get": "self-issue-session-get",
    "self-issue-session-retry": "self-issue-session-retry",
    "self-issue-session-pause": "self-issue-session-pause",
    "self-issue-session-reanalyze": "self-issue-session-reanalyze",
    "self-issue-session-materialize": "self-issue-session-materialize",
    "self-issue-session-activity-result": "self-issue-session-activity-result",
    "self-issue-preview": "self-issue-preview",
    "self-issue-confirm": "self-issue-confirm",
    "self-issue-publish": "self-issue-publish",
    "self-issue-github-device-start": "self-issue-github-device-start",
    "self-issue-github-device-poll": "self-issue-github-device-poll",
    "self-issue-recover": "self-issue-recover",
    "self-issue-resolve-unknown": "self-issue-resolve-unknown",
    "self-issue-attachment-add": "self-issue-intake-attachment-add",
    "self-issue-attachment-remove": "self-issue-intake-attachment-remove",
    "self-issue-intake-attachment-add": "self-issue-intake-attachment-add",
    "self-issue-intake-attachment-remove": "self-issue-intake-attachment-remove",
    "self-issue-evidence-start": "self-issue-evidence-start",
    "self-issue-evidence-interrupt": "self-issue-evidence-interrupt",
    "self-issue-evidence-resume": "self-issue-evidence-resume",
    "self-issue-evidence-apply": "self-issue-evidence-apply",
    "self-issue-evidence-fail": "self-issue-evidence-fail",
    "self-issue-runtime-check": "self-issue-runtime-check",
    "self-issue-limited-continue": "self-issue-limited-continue",
    "self-issue-attachment-preview": "self-issue-attachment-preview",
    "self-issue-attachment-confirm": "self-issue-attachment-confirm",
    "self-issue-attachment-prepare": "self-issue-attachment-prepare",
    "self-issue-attachment-resolve-unknown": "self-issue-attachment-resolve-unknown",
}

def _workflow(item: Any, state_dir: Path) -> dict[str, Any]:
    """Use the Web projection as the single Issue lifecycle interpretation."""
    try:
        from zf.web.issue_triage_routes import _workflow_projection
        events = event_log_from_project(state_dir).read_all()
        result = _workflow_projection(events, item)
        if not isinstance(result, dict):
            return {"state": "mirrored"}
        history = result.get("triage_history")
        if isinstance(history, list):
            current_id = str(result.get("triage_id") or "")
            current = next(
                (
                    row for row in history
                    if isinstance(row, dict)
                    and str(row.get("triage_id") or "") == current_id
                ),
                None,
            )
            if current is not None:
                result["triage_attempt"] = int(current.get("attempt") or 1)
        if result.get("state") in {"triage_queued", "fix_queued"}:
            queue_type = (
                "workflow.invoke.requested" if result["state"] == "fix_queued"
                else "external_issue.triage.queued"
            )
            queued = next((event for event in reversed(events)
                           if event.type == queue_type
                           and event.payload.get("workflow_run_id") == result.get("triage_id")), None)
            if queued is not None:
                result["triage_attempt"] = int(queued.payload.get("triage_attempt") or 1)
        return result
    except Exception:
        # Partial projections stay read-only and do not block notifications.
        return {"state": "mirrored", "projection_error": True}
def _project_id(project_root: Path | None) -> str:
    if project_root is None:
        return ""
    from zf.core.workspace import stable_project_id
    return stable_project_id(name=project_root.name, root=project_root)
def _safe_json(value: str) -> dict[str, Any]:
    if not value or len(value.encode("utf-8")) > 64 * 1024:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}
def _payload(envelope: Any) -> dict[str, Any]:
    values = dict(getattr(envelope, "form_values", {}) or {})
    for key in ("payload", "data", "json"):
        candidate = _safe_json(str(values.get(key) or ""))
        if candidate:
            values.update(candidate)
            break
    for key in ("answers", "issue_ref", "target_binding"):
        raw = values.get(key)
        if isinstance(raw, str):
            parsed = _safe_json(raw)
            if parsed:
                values[key] = parsed
    args = list(getattr(envelope, "args", []) or [])
    if args and args[0].lstrip().startswith("{"):
        values.update({**_safe_json(args[0]), **values})
        args = args[1:]
    values.setdefault("issue", args[0] if args else "")
    if len(args) > 1:
        values.setdefault("triage_id", args[1])
    if len(args) > 2:
        values.setdefault("revision", args[2])
    values["_args"] = args
    return values
def _issue_public(item: Any, workflow: dict[str, Any]) -> dict[str, Any]:
    value = item.to_dict()
    value["workflow"] = workflow
    # Cards are summaries; full bodies/comments remain behind Web API.
    value.pop("body_ref", None)
    value.pop("comments_ref", None)
    return value
def issue_summary(state_dir: Path) -> dict[str, Any]:
    store = IssueMirrorStore(state_dir)
    items = store.list()
    states: dict[str, int] = {}
    for item in items:
        state = str(_workflow(item, state_dir).get("state") or "mirrored")
        states[state] = states.get(state, 0) + 1
    return {
        "schema_version": "feishu-issue-summary.v1",
        "status": "ready" if items else "empty",
        "total": len(items),
        "states": dict(sorted(states.items())),
        "sync": store.sync_state().to_dict(),
    }
def issue_list(state_dir: Path, *, query: str = "", state: str = "", limit: int = 20) -> dict[str, Any]:
    needle = query.strip().casefold()[:200]
    wanted = state.strip().casefold()
    items = []
    for item in IssueMirrorStore(state_dir).list():
        workflow = _workflow(item, state_dir)
        current = str(workflow.get("state") or "mirrored")
        haystack = " ".join((item.title, item.author_login, str(item.number), *item.labels)).casefold()
        if wanted and wanted not in {item.github_state.casefold(), current.casefold()}:
            continue
        if needle and needle not in haystack:
            continue
        items.append(_issue_public(item, workflow))
        if len(items) >= max(1, min(limit, 50)):
            break
    return {"schema_version": "feishu-issue-list.v1", "items": items, "count": len(items)}

def issue_show(state_dir: Path, reference: str) -> dict[str, Any]:
    text = str(reference or "").strip().removeprefix("#")
    if not text.isdigit():
        return {"ok": False, "status": "invalid_issue_reference"}
    item = IssueMirrorStore(state_dir).get(int(text))
    if item is None:
        return {"ok": False, "status": "not_found", "issue_number": int(text)}
    workflow = _workflow(item, state_dir)
    return {"ok": True, "status": "ready", "issue": _issue_public(item, workflow)}

def _format_read_response(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, sort_keys=True)

def _record_projection_failure(
    state_dir: Path,
    *,
    card_key: str,
    operation: str,
    error: Exception,
) -> None:
    """Audit provider failures without changing canonical business state."""
    try:
        EventWriter(event_log_from_project(Path(state_dir))).emit(
            "feishu.notification.failed",
            actor="feishu-projector",
            payload={
                "schema_version": "feishu-notification-failure.v1",
                "card_key": card_key,
                "operation": operation,
                "reason": str(error)[:500],
            },
        )
    except Exception:
        # The projection failure itself must never bring down the bridge.
        pass

def _attachment_content_type(filename: str, provider_type: str) -> str:
    """Normalize generic provider MIME metadata through Intake's allowlist."""
    supplied = str(provider_type or "").split(";", 1)[0].strip().lower()
    suffix = Path(filename).suffix.lower()
    if supplied in {"", "application/octet-stream"}:
        allowed = ALLOWED_ATTACHMENTS.get(suffix)
        if allowed:
            return allowed
        guessed, _ = mimetypes.guess_type(filename)
        return str(guessed or supplied)
    return supplied

def _clarification_answers(values: dict[str, Any]) -> dict[str, str]:
    answers: dict[str, str] = {}
    for key, value in values.items():
        key_text = str(key)
        if not key_text.startswith("answer_"):
            continue
        question_id = key_text.removeprefix("answer_").strip()
        answer = str(value or "").strip()
        if question_id and answer:
            answers[question_id] = answer[:4000]
    return answers


def _session_attachments(envelope: Any, state_dir: Path, transport: FeishuTransport | None) -> list[dict[str, Any]]:
    """Download provider resources into the same verified sidecars used by Web."""
    from zf.web.kanban_agent_attachments import (
        MAX_KANBAN_ATTACHMENTS, MAX_KANBAN_ATTACHMENT_BYTES,
        store_kanban_agent_attachment,
    )

    resources = list(getattr(envelope, "attachments", []) or [])
    quoted_id = str(getattr(envelope, "attachment_message_id", "") or "")
    if quoted_id and not resources:
        message = transport.get_message(quoted_id) if transport else None
        if message is None or message.get("deleted"):
            raise ValueError("Quoted Feishu message is unavailable")
        if message.get("msg_type") in {"file", "image", "media", "post"}:
            sender = message.get("sender") or {}
            if (
                message.get("chat_id") != getattr(envelope, "chat_id", "")
                or sender.get("id_type") != "open_id"
                or sender.get("sender_type") != "user"
                or sender.get("id") != getattr(envelope, "user_id", "")
            ):
                raise ValueError("Quoted attachment must belong to the same sender and chat")
            resources = [
                {**resource, "message_id": quoted_id}
                for resource in extract_message_resources((message.get("body") or {}).get("content"))
            ]
            if not resources:
                raise ValueError("Quoted message has no downloadable attachment")
    if len(resources) > MAX_KANBAN_ATTACHMENTS:
        raise ValueError(f"at most {MAX_KANBAN_ATTACHMENTS} attachments are allowed")
    if resources and transport is None:
        raise ValueError("Feishu attachment transport is unavailable")
    descriptors = []
    for resource in resources:
        if not isinstance(resource, dict) or not resource.get("resource_key"):
            raise ValueError("Feishu attachment resource is unavailable")
        if resource.get("user_id") and resource["user_id"] != getattr(envelope, "user_id", ""):
            raise ValueError("Attachment must belong to the command sender")
        downloaded = transport.download_message_resource(
            str(resource.get("message_id") or getattr(envelope, "message_id", "") or ""),
            str(resource["resource_key"]),
            resource_type=str(resource.get("resource_type") or "file"),
        )
        if not downloaded:
            raise ValueError("Feishu attachment could not be downloaded")
        content, content_type = downloaded
        if not content or len(content) > MAX_KANBAN_ATTACHMENT_BYTES:
            raise ValueError("Attachment must be non-empty and no larger than 20 MB")
        filename = str(resource.get("filename") or resource.get("file_name") or "attachment.bin")
        descriptors.append(store_kanban_agent_attachment(state_dir, {
            "filename": filename,
            "content_type": _attachment_content_type(filename, content_type),
            "content_base64": base64.b64encode(content).decode("ascii"),
        }))
    return descriptors

def _intake_answers(values: dict[str, Any]) -> dict[str, Any]:
    answers: dict[str, Any] = {}
    environment: dict[str, str] = {}
    for key, value in values.items():
        key_text = str(key)
        if key_text == "answer_environment_os":
            environment["os"] = str(value or "").strip()[:80]
        elif key_text == "answer_environment_version":
            environment["version"] = str(value or "").strip()[:120]
        elif key_text.startswith("answer_"):
            question_id = key_text.removeprefix("answer_").strip()
            if question_id:
                answers[question_id] = str(value or "").strip()[:8000]
    if environment:
        answers["environment"] = environment
    return answers

def _as_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}

def _int_payload(value: Any, field: str, *, minimum: int = 0) -> tuple[int | None, dict[str, Any] | None]:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None, {
            "ok": False,
            "status": "invalid_payload",
            "message": f"{field} must be an integer",
        }
    if parsed < minimum:
        return None, {
            "ok": False,
            "status": "invalid_payload",
            "message": f"{field} must be >= {minimum}",
        }
    return parsed, None


def _pending_issue_proposal(state_dir: Path, workflow: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve the current exact Fix proposal from the durable proposal ledger."""
    if str(workflow.get("state") or "") != "awaiting_fix_approval":
        return None
    task_id = str(workflow.get("task_id") or "").strip()
    proposal_id = str(workflow.get("proposal_id") or "").strip()
    if not task_id and not proposal_id:
        return None
    try:
        from zf.runtime.kanban_proposals import pending_kanban_proposals

        records = pending_kanban_proposals(event_log_from_project(state_dir).read_all())
    except Exception:
        return None
    candidates = []
    for record in records:
        if not isinstance(record, dict) or not record.get("valid"):
            continue
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if not str(payload.get("route_id") or "").startswith("delivery:issue:"):
            continue
        if task_id and str(payload.get("task_id") or "") != task_id:
            continue
        if proposal_id and str(record.get("proposal_id") or "") != proposal_id:
            continue
        candidates.append(record)
    return candidates[0] if candidates else None


def _issue_run_validation(
    state_dir: Path,
    *,
    issue_number: int,
    run_id: str,
    command: str,
) -> dict[str, Any] | None:
    item = IssueMirrorStore(state_dir).get(issue_number)
    if item is None:
        return {"ok": False, "status": "not_found", "message": "GitHub Issue is not mirrored"}
    workflow = _workflow(item, state_dir)
    if str(workflow.get("run_id") or "") != run_id:
        return {"ok": False, "status": "revision_conflict", "message": "Issue Run is stale"}
    state = str(workflow.get("state") or "")
    allowed = {
        "issue-run-pause": {"triaging", "triage_recovering", "fixing", "fix_recovering", "verifying"},
        "issue-run-resume": {"triage_paused", "fix_paused"},
        "issue-run-cancel": {"triaging", "triage_paused", "triage_recovering", "fixing", "fix_paused", "fix_recovering", "verifying"},
    }
    if state not in allowed.get(command, set()):
        return {"ok": False, "status": "invalid_transition", "message": f"Run cannot be {command.removeprefix('issue-run-')} from {state}"}
    return None

def handle_issue_command(
    envelope: Any,
    *,
    state_dir: Path,
    config: Any,
    project_root: Path | None = None,
    transport: FeishuTransport | None = None,
) -> dict[str, Any]:
    """Handle one normalized Feishu Issue/Self-Issue command."""
    command = str(getattr(envelope, "command", "") or "")
    if command not in ISSUE_COMMANDS:
        return {"ok": False, "status": "unsupported_command"}
    if command == "issue-summary":
        result = issue_summary(state_dir)
        return {"ok": True, "status": "completed", "message": _format_read_response(result), **result}
    values = _payload(envelope)
    if command == "issue-list":
        result = issue_list(state_dir, query=str(values.get("q") or values.get("query") or ""), state=str(values.get("state") or ""))
        return {"ok": True, "status": "completed", "message": _format_read_response(result), **result}
    if command == "issue-show":
        result = issue_show(state_dir, str(values.get("issue") or values.get("issue_number") or ""))
        return {**result, "message": _format_read_response(result)}

    action = _ACTION_BY_COMMAND.get(command) or _SELF_ACTION_BY_COMMAND.get(command)
    if not action:
        if command == "issue-fix-proposal-refresh":
            issue_number, invalid = _int_payload(
                values.get("issue_number") or values.get("issue"),
                "issue_number",
                minimum=1,
            )
            if invalid is not None:
                return invalid
            item = IssueMirrorStore(state_dir).get(issue_number or 0)
            if item is None:
                return {"ok": False, "status": "not_found", "message": "GitHub Issue is not mirrored"}
            workflow = _workflow(item, state_dir)
            proposal = _pending_issue_proposal(state_dir, workflow)
            result = {
                "ok": bool(proposal),
                "status": "ready" if proposal else "not_found",
                "issue_number": issue_number,
                "proposal": proposal or {},
            }
            return {**result, "message": _format_read_response(result)}
        return {"ok": False, "status": "unsupported_command"}
    payload = dict(values)
    raw_args = list(payload.pop("_args", []) or [])
    payload.pop("payload", None)
    payload.pop("data", None)
    payload.pop("json", None)
    if command.startswith("issue-"):
        try:
            payload["issue_number"] = int(
                payload.get("issue_number") or payload.get("issue") or 0
            )
        except (TypeError, ValueError):
            return {
                "ok": False,
                "status": "invalid_payload",
                "message": "issue number must be an integer",
            }
        if command.startswith("issue-candidate-") and len(raw_args) > 1:
            payload["candidate_sha"] = str(raw_args[1])
            if len(raw_args) > 2:
                payload["source_revision"] = str(raw_args[2])
            if command == "issue-candidate-review" and len(raw_args) > 3:
                payload["verdict"] = str(raw_args[3])
        if command == "issue-candidate-pr" and len(raw_args) > 1 and not payload.get("url"):
            payload["url"] = str(raw_args[1])
    if command in {
        "issue-fix-proposal-approve", "issue-fix-proposal-reject",
    }:
        if len(raw_args) < 2:
            return {"ok": False, "status": "invalid_payload", "message": "proposal identity is required"}
        item = IssueMirrorStore(state_dir).get(int(payload["issue_number"]))
        if item is None:
            return {"ok": False, "status": "not_found", "message": "GitHub Issue is not mirrored"}
        workflow = _workflow(item, state_dir)
        proposal = _pending_issue_proposal(state_dir, workflow)
        proposal_event_id = str(raw_args[1])
        if proposal is None or proposal_event_id != str(proposal.get("proposal_event_id") or ""):
            return {"ok": False, "status": "stale_proposal", "message": "Fix proposal changed; refresh the Issue card"}
        if len(raw_args) > 2 and str(raw_args[2]) != str(proposal.get("proposal_id") or ""):
            return {"ok": False, "status": "stale_proposal", "message": "Fix proposal identity changed; refresh the Issue card"}
        if len(raw_args) > 3 and str(raw_args[3]) != str(proposal.get("proposal_digest") or ""):
            return {"ok": False, "status": "stale_proposal", "message": "Fix proposal digest changed; refresh the Issue card"}
        payload = (
            dict(proposal.get("payload") or {})
            if command.endswith("approve")
            else {"proposal_event_id": proposal_event_id, "reason": str(payload.get("reason") or "")[:2000]}
        )
        payload["proposal_event_id"] = proposal_event_id
        if command.endswith("approve"):
            action = str(proposal.get("action") or "workflow-start")
        else:
            action = "kanban-proposal-dismiss"
            if not str(payload.get("reason") or "").strip():
                return {"ok": False, "status": "invalid_payload", "message": "rejection reason is required"}
    if command.startswith("issue-run-"):
        if len(raw_args) < 2:
            return {"ok": False, "status": "invalid_payload", "message": "run_id is required"}
        payload["run_id"] = str(raw_args[1])
        invalid = _issue_run_validation(
            state_dir,
            issue_number=int(payload["issue_number"]),
            run_id=str(payload["run_id"]),
            command=command,
        )
        if invalid is not None:
            return invalid
        if command == "issue-run-cancel" and str(payload.get("confirm_cancel") or "").strip() != "确认取消":
            return {"ok": False, "status": "confirmation_required", "message": "请输入“确认取消”后再永久取消 Run"}
        payload["reason"] = str(payload.get("reason") or f"Feishu operator {command.removeprefix('issue-run-')} for Issue #{payload['issue_number']}")
    if command == "issue-triage-start" and len(raw_args) > 1:
        payload["source_revision"] = str(raw_args[1])
        payload.pop("triage_id", None)
    if command in {"issue-triage-cancel", "issue-fix-cancel"} and len(raw_args) > 2:
        # Cancel's third segment is the triage attempt; the service expects
        # this as ``expected_revision``.
        try:
            payload["expected_revision"] = int(raw_args[2])
        except (TypeError, ValueError):
            return {
                "ok": False,
                "status": "invalid_payload",
                "message": "triage attempt must be an integer",
            }
    if command in {"self-issue-save", "self-issue-intake-save"} and payload.get("intake_id"):
        action = "self-issue-intake-save"
    if command == "self-issue-dismiss" and payload.get("draft_id") and not payload.get("intake_id"):
        action = "self-issue-dismiss"
    if command in {"self-issue-new", "self-issue-intake-start"} and raw_args and not payload.get("description"):
        payload["description"] = " ".join(raw_args)
        payload.pop("issue", None)
    if command == "self-issue-new":
        payload["direct_report"] = True
        payload["portable_session"] = True
    if command in {"self-issue-new", "self-issue-session-answer"}:
        if getattr(envelope, "attachments", None) or getattr(envelope, "attachment_message_id", ""):
            try:
                payload["attachments"] = _session_attachments(envelope, state_dir, transport)
            except (OSError, ValueError, FeishuTransportError) as exc:
                return {"ok": False, "status": "attachment_download_failed", "message": str(exc)}
    if command.startswith("self-issue-session-") and raw_args:
        payload["session_id"] = raw_args[0]
    if command.startswith("self-issue-session-"):
        # These names belong to Issue/Triage positional arguments, not Sessions.
        for key in ("issue", "triage_id", "revision"):
            if key not in (getattr(envelope, "form_values", {}) or {}):
                payload.pop(key, None)
    if command == "self-issue-session-answer" and not payload.get("answer") and len(raw_args) > 1:
        payload["answer"] = " ".join(raw_args[1:])
    if command == "self-issue-session-confirm":
        payload["confirmed_by"] = str(getattr(envelope, "user_id", "") or "feishu-user")
        if len(raw_args) > 1:
            payload.setdefault("provider", raw_args[1])
        if len(raw_args) > 2:
            payload.setdefault("target", raw_args[2])
    if command == "self-issue-session-reanalyze" and not payload.get("comment") and len(raw_args) > 1:
        payload["comment"] = " ".join(raw_args[1:])
    if command == "self-issue-session-materialize" and len(raw_args) > 1:
        try:
            payload["report_index"] = int(raw_args[1])
        except ValueError:
            return {"ok": False, "status": "invalid_payload", "message": "report_index must be an integer"}
    if command == "self-issue-session-activity-result" and len(raw_args) > 1:
        payload.setdefault("activity_id", raw_args[1])
    if command == "self-issue-session-answer" and not str(payload.get("answer") or "").strip() and not payload.get("attachments"):
        return {"ok": False, "status": "invalid_payload", "message": "answer is required"}
    if command == "self-issue-session-answer" and not str(payload.get("answer") or "").strip() and payload.get("attachments"):
        payload["answer"] = "用户补充了附件：" + ", ".join(
            str(item.get("filename") or item.get("name") or "attachment")
            for item in payload["attachments"] if isinstance(item, dict)
        )
    if command == "self-issue-get" and raw_args and not payload.get("draft_id"):
        payload["draft_id"] = raw_args[0]
        payload.pop("issue", None)
    if command.startswith("self-issue-") and raw_args:
        if command in {"self-issue-intake-get", "self-issue-intake-save", "self-issue-intake-submit", "self-issue-intake-dismiss", "self-issue-submit", "self-issue-save"} and not payload.get("intake_id"):
            payload["intake_id"] = raw_args[0]
        elif command in {"self-issue-preview", "self-issue-dismiss", "self-issue-evidence-start", "self-issue-evidence-interrupt", "self-issue-evidence-resume", "self-issue-evidence-apply", "self-issue-evidence-fail", "self-issue-runtime-check", "self-issue-limited-continue", "self-issue-attachment-preview"} and not payload.get("draft_id"):
            payload["draft_id"] = raw_args[0]
        elif command in {"self-issue-confirm", "self-issue-publish"} and not payload.get("batch_id"):
            payload.setdefault("batch_id", raw_args[0])
            payload.setdefault("intent_id", raw_args[0])
        elif command in {"self-issue-attachment-add", "self-issue-attachment-remove", "self-issue-intake-attachment-add", "self-issue-intake-attachment-remove"} and not payload.get("intake_id"):
            # Intake attachments use the canonical ``intake_id`` field.
            payload["intake_id"] = raw_args[0]
        elif command.startswith("self-issue-attachment-") and not payload.get("preparation_id"):
            payload["preparation_id"] = raw_args[0]
        if command in {"self-issue-attachment-remove", "self-issue-intake-attachment-remove"} and len(raw_args) > 1:
            payload.setdefault("attachment_id", raw_args[1])
        if command in {
            "self-issue-submit", "self-issue-save",
            "self-issue-intake-submit", "self-issue-intake-save",
        } and len(raw_args) > 1:
            payload["revision"] = raw_args[1]
        if command in {"self-issue-preview", "self-issue-evidence-start"} and len(raw_args) > 1:
            payload["revision"] = raw_args[1]
        if command == "self-issue-confirm" and len(raw_args) > 1:
            payload["payload_digest"] = raw_args[1]
        if command == "self-issue-publish" and len(raw_args) > 1:
            payload["confirmation_id"] = raw_args[1]
        if command == "self-issue-recover" and raw_args:
            payload["intent_id"] = raw_args[0]
        if command == "self-issue-resolve-unknown" and raw_args:
            payload["intent_id"] = raw_args[0]
    if command in {"self-issue-github-device-start", "self-issue-github-device-poll"}:
        from zf.integrations.feishu.publication_feedback import github_session_binding
        expected = 4 if command.endswith("start") else 2
        if len(raw_args) != expected:
            return {"ok": False, "status": "invalid_payload", "message": "invalid GitHub authorization arguments"}
        draft_id = raw_args[1] if command.endswith("start") else raw_args[0]
        # Do not accept caller-supplied user/workspace/session credentials or a
        # forged continuation through form_values; only the signed target IDs.
        payload = {"draft_id": draft_id, "session_id": github_session_binding(envelope, draft_id)}
        if command.endswith("start"):
            payload.update(batch_id=raw_args[2], confirmation_id=raw_args[3])
        else:
            payload["transaction_id"] = raw_args[1]
    if command in {"self-issue-submit", "self-issue-save", "self-issue-intake-submit", "self-issue-intake-save"} and payload.get("intake_id") and payload.get("revision"):
        revision, invalid = _int_payload(payload.get("revision"), "revision", minimum=0)
        if invalid is not None:
            return invalid
        intake = SelfIssueIntakeStore(
            Path(state_dir) / "self-issues" / "intakes.json",
        ).get(str(payload["intake_id"]))
        if intake is not None and revision != intake.revision:
            return {"ok": False, "status": "revision_conflict", "message": "Intake revision changed"}
    if command in {
        "self-issue-submit", "self-issue-save",
        "self-issue-intake-submit", "self-issue-intake-save",
    } and len(raw_args) > 2 and not isinstance(payload.get("answers"), dict):
        candidate = _safe_json(str(raw_args[2] or ""))
        if isinstance(candidate, dict):
            payload["answers"] = candidate
    if command == "self-issue-preview" and payload.get("draft_id") and payload.get("revision"):
        revision, invalid = _int_payload(payload.get("revision"), "revision", minimum=0)
        if invalid is not None:
            return invalid
        draft = IssueDraftStore(Path(state_dir) / "self-issues" / "drafts.json").get(str(payload["draft_id"]))
        if draft is not None and revision != draft.revision:
            return {"ok": False, "status": "revision_conflict", "message": "Draft revision changed"}
    if command in {"self-issue-attachment-add", "self-issue-intake-attachment-add"} and not payload.get("content_base64"):
        resources = list(getattr(envelope, "attachments", []) or [])
        resource_key = str(payload.get("resource_key") or "").strip()
        resource = next((item for item in resources if str(item.get("resource_key") or "") == resource_key), None)
        resource = resource or (resources[0] if resources else None)
        if resource is None or transport is None:
            return {"ok": False, "status": "attachment_download_required", "message": "Feishu attachment resource is unavailable"}
        downloaded = transport.download_message_resource(
            str(getattr(envelope, "message_id", "") or ""),
            str(resource.get("resource_key") or ""),
            resource_type=str(resource.get("resource_type") or "file"),
        )
        if not downloaded:
            return {"ok": False, "status": "attachment_download_failed", "message": "Feishu attachment could not be downloaded"}
        content, content_type = downloaded
        if not content or len(content) > 20 * 1024 * 1024:
            return {"ok": False, "status": "attachment_too_large", "message": "Attachment exceeds the 20 MB limit"}
        filename = str(payload.get("filename") or resource.get("filename") or "attachment.bin")
        payload["filename"] = filename
        payload["content_type"] = _attachment_content_type(
            filename,
            str(payload.get("content_type") or content_type),
        )
        payload["content_base64"] = base64.b64encode(content).decode("ascii")
    if command == "issue-triage-start" and not payload.get("issue_number"):
        return {"ok": False, "status": "invalid_payload", "message": "issue_number is required"}
    if command in {"issue-triage-cancel", "issue-fix-cancel"}:
        # Queue cancellation is locally durable; remote lifecycle labels are
        # a projection and must not hold the Feishu action response open.
        payload["defer_lifecycle_label_sync"] = True
    if command == "issue-needs-info-submit":
        issue_number, invalid = _int_payload(payload.get("issue_number"), "issue_number", minimum=1)
        if invalid is not None:
            return invalid
        workflow = None
        item = IssueMirrorStore(state_dir).get(issue_number) if issue_number is not None else None
        if item is not None:
            workflow = _workflow(item, state_dir)
        expected_triage = str(payload.get("triage_id") or "")
        expected_revision = str(payload.get("revision") or "")
        if (
            workflow is None
            or expected_triage != str(workflow.get("triage_id") or "")
            or (
                expected_revision
                and expected_revision != str(workflow.get("source_revision") or "")
            )
        ):
            return {
                "ok": False,
                "status": "revision_conflict",
                "message": "needs_info request is stale",
            }
        payload["answers"] = _clarification_answers(payload)
    if command in {"self-issue-save", "self-issue-submit", "self-issue-intake-save", "self-issue-intake-submit"}:
        form_answers = _intake_answers(payload)
        if form_answers:
            payload["answers"] = form_answers
        if "attachment_disclosure_confirmed" in payload:
            payload["attachment_disclosure_confirmed"] = _as_bool(
                payload["attachment_disclosure_confirmed"]
            )
    if command in {"self-issue-attachment-add", "self-issue-intake-attachment-add"} and "video_disclosure_confirmed" in payload:
        payload["video_disclosure_confirmed"] = _as_bool(
            payload["video_disclosure_confirmed"]
        )
    actor = f"feishu:{getattr(envelope, 'user_id', '') or 'unknown'}"
    log = event_log_from_project(state_dir, config=config)
    writer = EventWriter(log)
    requested = writer.emit(
        "feishu.issue.command.received",
        actor=actor,
        correlation_id=str(payload.get("issue_number") or payload.get("draft_id") or "") or None,
        payload={
            "schema_version": "feishu-issue-command.v1",
            "command": command,
            "action": action,
            "chat_id": str(getattr(envelope, "chat_id", "") or ""),
            "message_id": str(getattr(envelope, "message_id", "") or ""),
            "idempotency_key": str(getattr(envelope, "idempotency_key", "") or ""),
        },
    )
    action_service = ControlledActionService(
        state_dir,
        writer,
        config=config,
        project_root=project_root or state_dir.parent,
        actor=actor,
        source="feishu",
        surface="feishu",
    )
    result = action_service.execute(
        action=action,
        requested_action=f"/zf {command}",
        payload=payload,
        requested=requested,
    )
    # Intake promotion returns ``start_evidence`` so CLI callers can continue
    # into the bounded assessment run. Feishu has no second CLI continuation,
    # therefore schedule that same controlled action immediately; otherwise a
    # subsequent preview correctly remains blocked in evidence_pending.
    if (
        action == "self-issue-intake-submit"
        and result.get("ok")
        and result.get("start_evidence")
    ):
        draft = result.get("draft") if isinstance(result.get("draft"), dict) else {}
        evidence = action_service.execute(
            action="self-issue-evidence-start",
            requested_action="/zf self-issue-evidence-start",
            payload={
                "draft_id": str(draft.get("draft_id") or ""),
                "revision": int(draft.get("revision") or 0),
            },
            requested=requested,
        )
        result = {**result, "evidence_start": evidence}
    from zf.integrations.feishu.publication_feedback import record_publication_feedback
    record_publication_feedback(Path(state_dir), envelope, payload, result, requested.id)
    return {**result, "message": _format_read_response(result)}

def build_issue_card(
    item: Any,
    workflow: dict[str, Any],
    *,
    project_id: str,
    web_base_url: str = "",
    clarification: dict[str, Any] | None = None,
    delivery: dict[str, Any] | None = None,
    proposal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = str(workflow.get("state") or "mirrored")
    issue_number = int(item.number)
    title = item.title[:300]
    lines = [
        f"**#{issue_number} {title}**",
        f"状态：`{state}`",
        f"仓库：`{item.repository}`",
        f"来源版本：`{workflow.get('source_revision') or 'unknown'}`",
    ]
    if workflow.get("triage_id"):
        lines.append(f"Triage：`{workflow['triage_id']}`")
    elements: list[dict[str, Any]] = [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}]
    actions: list[dict[str, Any]] = []
    if web_base_url:
        actions.append({"tag": "button", "text": {"tag": "plain_text", "content": "打开详情"}, "type": "default", "url": f"{web_base_url.rstrip('/')}/?page=issue-triage&issue={issue_number}"})
    if state in {"mirrored", "triage_cancelled", "failed", "triage_blocked"}:
        source_revision = str(workflow.get("source_revision") or "")
        target = f"{issue_number}:{source_revision}" if source_revision else str(issue_number)
        actions.append({"tag": "button", "text": {"tag": "plain_text", "content": "开始分诊"}, "type": "primary", "value": {"action": f"issue-triage-start:{target}"}})
    elif (
        state in {"triage_queued", "fix_queued"} and workflow.get("triage_id")
        and workflow.get("queued_cancel_available")
    ):
        cancel_command = "issue-fix-cancel" if state == "fix_queued" else "issue-triage-cancel"
        actions.append({"tag": "button", "text": {"tag": "plain_text", "content": "取消排队"}, "type": "danger", "value": {"action": f"{cancel_command}:{issue_number}:{workflow['triage_id']}:{workflow.get('triage_attempt') or 1}"}})
    if state in {"needs_info", "needs_info_exhausted"}:
        request_url = str(workflow.get("clarification_request_url") or (clarification or {}).get("request_url") or item.html_url or "")
        request_status = str(workflow.get("clarification_request_status") or (clarification or {}).get("request_status") or "pending")
        questions = [
            str(question.get("question") or "")[:500]
            for question in ((clarification or {}).get("questions") or [])[:10]
            if isinstance(question, dict)
        ]
        detail = "\n".join(questions)
        if state == "needs_info_exhausted":
            detail += "\n补充轮次已达上限；维护者需检查远端回复后显式重启分诊。"
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": (
            "补充信息请求已记录；请由报告者直接回复远端 GitHub Issue。\n"
            f"状态：{request_status}\n{detail}"
        )}})
        if request_url.startswith("https://"):
            elements.append({"tag": "action", "actions": [{
                "tag": "button", "text": {"tag": "plain_text", "content": "打开 GitHub Issue"},
                "type": "default", "url": request_url[:1000],
            }]})
    if state == "awaiting_fix_approval" and proposal:
        proposal_payload = proposal.get("payload") if isinstance(proposal.get("payload"), dict) else {}
        proposal_event_id = str(proposal.get("proposal_event_id") or "")
        proposal_id = str(proposal.get("proposal_id") or "")
        proposal_digest = str(proposal.get("proposal_digest") or "")
        allowed_paths = proposal_payload.get("parameters", {}).get("allowed_paths") if isinstance(proposal_payload.get("parameters"), dict) else []
        if not isinstance(allowed_paths, list):
            allowed_paths = []
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": (
            f"Fix Proposal：`{proposal_id or 'unknown'}`\n"
            f"目标：{str(proposal_payload.get('objective') or '—')[:500]}\n"
            f"允许修改：{', '.join(str(value) for value in allowed_paths[:20]) or '—'}"
        )}})
        if web_base_url and workflow.get("task_id"):
            query = urllib.parse.urlencode({"page": "task", "project": project_id, "task": str(workflow["task_id"])})
            actions.append({
                "tag": "button", "text": {"tag": "plain_text", "content": "打开 Proposal"}, "type": "default",
                "url": f"{web_base_url.rstrip('/')}/?{query}",
            })
        proposal_action = f"{issue_number}:{proposal_event_id}:{proposal_id}:{proposal_digest}"
        actions.append({
            "tag": "button", "text": {"tag": "plain_text", "content": "批准并排队 Fix"},
            "type": "primary",
            "value": {"action": f"issue-fix-proposal-approve:{proposal_action}"},
        })
        elements.append({"tag": "form", "name": f"issue-fix-proposal-reject-{issue_number}", "elements": [
            {"tag": "input", "name": "reason", "required": True, "max_length": 2000,
             "placeholder": {"tag": "plain_text", "content": "拒绝原因"}},
            {"tag": "action", "actions": [{
                "tag": "button", "text": {"tag": "plain_text", "content": "拒绝 Proposal"}, "type": "danger",
                "value": {"action": f"issue-fix-proposal-reject:{proposal_action}"},
            }]},
        ]})
        actions.append({
            "tag": "button", "text": {"tag": "plain_text", "content": "刷新 Proposal"}, "type": "default",
            "value": {"action": f"issue-fix-proposal-refresh:{issue_number}"},
        })
    run_id = str(workflow.get("run_id") or "")
    if run_id and state in {"triaging", "triage_recovering", "fixing", "fix_recovering", "verifying", "triage_paused", "fix_paused"}:
        if state in {"triage_paused", "fix_paused"}:
            actions.append({
                "tag": "button", "text": {"tag": "plain_text", "content": "恢复 Run"}, "type": "primary",
                "value": {"action": f"issue-run-resume:{issue_number}:{run_id}"},
            })
        else:
            actions.append({
                "tag": "button", "text": {"tag": "plain_text", "content": "暂停 Run"}, "type": "default",
                "value": {"action": f"issue-run-pause:{issue_number}:{run_id}"},
            })
        elements.append({"tag": "form", "name": f"issue-run-cancel-{issue_number}", "elements": [
            {"tag": "input", "name": "confirm_cancel", "required": True, "max_length": 20,
             "placeholder": {"tag": "plain_text", "content": "输入“确认取消”以永久取消 Run"}},
            {"tag": "action", "actions": [{
                "tag": "button", "text": {"tag": "plain_text", "content": "永久取消 Run"}, "type": "danger",
                "value": {"action": f"issue-run-cancel:{issue_number}:{run_id}"},
            }]},
        ]})
    candidate = (delivery or {}).get("candidate")
    candidate_sha = str((candidate or {}).get("candidate_head_sha") or "")
    candidate_revision = str((candidate or {}).get("source_revision") or "")
    if candidate and candidate_sha and candidate_revision:
        if state == "verified_candidate":
            actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": "批准候选"},
                "type": "primary",
                "value": {"action": f"issue-candidate-review:{issue_number}:{candidate_sha}:{candidate_revision}:approve"},
            })
            elements.append({"tag": "form", "name": f"issue-candidate-review-{issue_number}", "elements": [
                {"tag": "input", "name": "reason", "required": True, "max_length": 2000,
                 "placeholder": {"tag": "plain_text", "content": "要求修改或拒绝原因"}},
                {"tag": "action", "actions": [
                    {"tag": "button", "text": {"tag": "plain_text", "content": "要求修改"}, "type": "default",
                     "value": {"action": f"issue-candidate-review:{issue_number}:{candidate_sha}:{candidate_revision}:changes_requested"}},
                    {"tag": "button", "text": {"tag": "plain_text", "content": "拒绝候选"}, "type": "danger",
                     "value": {"action": f"issue-candidate-review:{issue_number}:{candidate_sha}:{candidate_revision}:reject"}},
                ]},
            ]})
        elif state == "approved_for_pr":
            actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": "准备 PR"},
                "type": "primary",
                "value": {"action": f"issue-candidate-prepare:{issue_number}:{candidate_sha}:{candidate_revision}"},
            })
        elif state == "owner_changes_requested" and web_base_url and workflow.get("task_id"):
            query = urllib.parse.urlencode({"page": "task", "project": project_id, "task": str(workflow["task_id"])})
            actions.append({
                "tag": "button", "text": {"tag": "plain_text", "content": "打开审核回执"}, "type": "default",
                "url": f"{web_base_url.rstrip('/')}/?{query}",
            })
        elif state == "publication_prepared":
            form_name = f"issue-candidate-pr-{issue_number}"
            elements.append({
                "tag": "form",
                "name": form_name,
                "elements": [
                    {
                        "tag": "input",
                        "name": "url",
                        "required": True,
                        "max_length": 500,
                        "placeholder": {"tag": "plain_text", "content": "GitHub PR URL"},
                    },
                    {
                        "tag": "action",
                        "actions": [{
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "登记 PR"},
                            "type": "primary",
                        "value": {
                            "action": (
                                f"issue-candidate-pr:{issue_number}:"
                                f"{candidate_sha}:{candidate_revision}"
                            ),
                        },
                        }],
                    },
                ],
            })
        elif state in {"pr_open", "pr_changes_requested", "pr_approved", "pr_closed_without_merge"}:
            pull_request = (delivery or {}).get("handoff", {}).get("pull_request", {}) if isinstance((delivery or {}).get("handoff"), dict) else {}
            pr_url = str(pull_request.get("url") or "")
            if pr_url.startswith("https://"):
                pr_label = {
                    "pr_changes_requested": "打开 PR review",
                    "pr_approved": "打开已批准 PR",
                    "pr_closed_without_merge": "打开已关闭 PR",
                }.get(state, "打开 PR")
                actions.append({"tag": "button", "text": {"tag": "plain_text", "content": pr_label}, "type": "default", "url": pr_url[:1000]})
            actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": "刷新 PR"},
                "type": "default",
                "value": {"action": f"issue-candidate-pr-refresh:{issue_number}"},
            })
    if actions:
        elements.append({"tag": "action", "actions": actions})
    return {
        # Message-ID PATCH requires shared cards at creation and update time.
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {"title": {"tag": "plain_text", "content": "Issue Triage"}, "template": "red" if "failed" in state or "blocked" in state else "blue"},
        "elements": elements,
        "_card_key": f"issue-{project_id}-{item.issue_key}-{workflow.get('triage_id') or 'base'}",
    }

def push_issue_cards_once(
    state_dir: Path,
    transport: FeishuTransport,
    *,
    receive_id: str,
    receive_id_type: str = "chat_id",
    project_root: Path | None = None,
    config: Any | None = None,
    web_base_url: str = "",
    action_secret: bytes | None = None,
    action_ttl_seconds: int = 86400,
    now: float | None = None,
) -> dict[str, Any]:
    """Project current Issue states to stable Feishu cards, idempotently."""
    if not receive_id:
        return {"sent": [], "updated": [], "skipped": ["missing_receive_id"]}
    project_id = _project_id(project_root)
    ledger_path = Path(state_dir) / "integrations" / "feishu" / "issue_card_ledger.json"
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger = ledger if isinstance(ledger, dict) else {}
    except (OSError, ValueError):
        ledger = {}
    sent: list[str] = []
    updated: list[str] = []
    failed: list[str] = []
    for item in IssueMirrorStore(state_dir).list()[:100]:
        workflow = _workflow(item, Path(state_dir))
        delivery = None
        if config is not None:
            try:
                from zf.runtime.issue_candidate_delivery import IssueCandidateDeliveryService

                delivery = IssueCandidateDeliveryService(
                    state_dir=Path(state_dir),
                    project_root=project_root or Path(state_dir).parent,
                    config=config,
                ).projection(item)
            except Exception:
                delivery = None
        clarification = None
        if workflow.get("state") in {"needs_info", "needs_info_exhausted"} and workflow.get("clarification_ref"):
            try:
                from zf.runtime.external_issue_clarification import (
                    hydrate_external_issue_clarification,
                )

                clarification = hydrate_external_issue_clarification(
                    Path(state_dir), workflow["clarification_ref"],
                )
            except Exception:
                clarification = None
        card = build_issue_card(
            item,
            workflow,
            project_id=project_id,
            web_base_url=web_base_url,
            clarification=clarification,
            delivery=delivery,
            proposal=_pending_issue_proposal(Path(state_dir), workflow),
        )
        effective_now = time.time() if now is None else now
        semantic_digest = hashlib.sha256(
            json.dumps(card, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        card_key = str(card.get("_card_key") or "")
        entry = ledger.get(card_key) if isinstance(ledger.get(card_key), dict) else {}
        token_valid = not action_secret
        if action_secret:
            try:
                token_valid = float(entry.get("token_expires_at") or 0) > effective_now
            except (TypeError, ValueError):
                token_valid = False
        if (
            entry.get("message_id")
            and entry.get("semantic_digest") == semantic_digest
            and token_valid
        ):
            continue
        if action_secret:
            card = attach_action_token(
                card,
                secret=action_secret,
                chat_id=receive_id,
                ttl_seconds=action_ttl_seconds,
                now=effective_now,
            )
        digest = hashlib.sha256(json.dumps(card, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if not entry.get("message_id"):
            try:
                message_id = transport.send_card(FeishuMessage(chat_id=receive_id, content=json.dumps(card, ensure_ascii=False), msg_type="interactive", receive_id_type=receive_id_type))
            except Exception as exc:
                _record_projection_failure(state_dir, card_key=card_key, operation="send", error=exc)
                failed.append(card_key)
                continue
            if message_id:
                ledger[card_key] = {
                    "message_id": str(message_id),
                    "digest": digest,
                    "semantic_digest": semantic_digest,
                    "state": workflow.get("state"),
                    "token_expires_at": (
                        effective_now + action_ttl_seconds if action_secret else 0
                    ),
                }
                sent.append(card_key)
            continue
        if entry.get("digest") != digest:
            try:
                transport.update_card(str(entry["message_id"]), card)

                # The card callback and the projection loop run on different
                # threads.  If a queued card was rendered just before the
                # cancel event committed, the first PATCH can still carry the
                # stale ``取消排队`` button.  Re-read the canonical workflow
                # after the provider call and immediately converge the same
                # message to the newest state before advancing the ledger.
                latest_item = IssueMirrorStore(state_dir).get(item.number) or item
                latest_workflow = _workflow(latest_item, Path(state_dir))
                latest_card = build_issue_card(
                    latest_item,
                    latest_workflow,
                    project_id=project_id,
                    web_base_url=web_base_url,
                    clarification=clarification,
                    delivery=delivery,
                    proposal=_pending_issue_proposal(Path(state_dir), latest_workflow),
                )
                latest_semantic_digest = hashlib.sha256(
                    json.dumps(latest_card, ensure_ascii=False, sort_keys=True).encode()
                ).hexdigest()
                if latest_semantic_digest != semantic_digest:
                    if action_secret:
                        latest_card = attach_action_token(
                            latest_card,
                            secret=action_secret,
                            chat_id=receive_id,
                            ttl_seconds=action_ttl_seconds,
                            now=effective_now,
                        )
                    transport.update_card(str(entry["message_id"]), latest_card)
                    card = latest_card
                    digest = hashlib.sha256(
                        json.dumps(card, ensure_ascii=False, sort_keys=True).encode()
                    ).hexdigest()
                    semantic_digest = latest_semantic_digest
                    workflow = latest_workflow
            except Exception as exc:
                _record_projection_failure(state_dir, card_key=card_key, operation="update", error=exc)
                failed.append(card_key)
                continue
            ledger[card_key] = {
                **entry,
                "digest": digest,
                "semantic_digest": semantic_digest,
                "state": workflow.get("state"),
                "token_expires_at": (
                    effective_now + action_ttl_seconds if action_secret else 0
                ),
            }
            updated.append(card_key)
    with locked_path(ledger_path):
        atomic_write_text(ledger_path, json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {"sent": sent, "updated": updated, "failed": failed, "ledger": ledger}

__all__ = [
    "ISSUE_COMMANDS", "ISSUE_QUERY_COMMANDS", "ISSUE_MUTATION_COMMANDS",
    "SELF_ISSUE_COMMANDS", "build_issue_card", "build_self_issue_card",
    "handle_issue_command", "issue_list", "issue_show", "issue_summary",
    "push_issue_cards_once", "push_self_issue_cards_once",
]
