"""Web-side durable Plan interaction choreography."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from zf.core.events import EventWriter, ZfEvent
from zf.core.security.redaction import redact_obj
from zf.runtime.kanban_plan_requests import (
    PLAN_ANSWERED_EVENT,
    PLAN_REQUESTED_EVENT,
    PLAN_REQUEST_SCHEMA_VERSION,
    PLAN_RESPONSE_SCHEMA_VERSION,
    normalize_plan_request_revision,
    plan_request_digest,
    plan_request_gate,
    plan_response_gate,
)
from zf.web.plan_extraction import extract_plan_request
from zf.web.plan_requirement_sources import requirement_source_messages
from zf.web.projections.common import _action_failed


@dataclass
class HeadlessPlanDraft:
    request: dict[str, Any]
    event: ZfEvent


def task_contract_context_for_plan(
    tasks: Iterable[Any],
    *,
    config: Any | None,
    project_root: Path,
) -> tuple[
    dict[str, str],
    dict[str, list[str]],
    dict[str, dict[str, str]],
]:
    from zf.core.task.contract_validation import validate_task_contract
    from zf.runtime.task_workflow_plans import (
        task_workflow_binding_digest,
        task_workflow_route_eligibility_map,
    )

    candidates = list(tasks)
    digests = {
        str(task.id): task_workflow_binding_digest(task)
        for task in candidates
    }
    errors_by_task: dict[str, list[str]] = {}
    route_eligibility = (
        task_workflow_route_eligibility_map(candidates, config)
        if config is not None
        else {}
    )
    if config is None:
        return digests, errors_by_task, route_eligibility
    for task in candidates:
        errors = validate_task_contract(
            task,
            config=config,
            project_root=project_root,
        )
        if errors:
            errors_by_task[str(task.id)] = errors
    return digests, errors_by_task, route_eligibility


def validate_chat_plan_payload(payload: dict[str, Any]) -> str:
    workflow_context = payload.get("workflow_context")
    if workflow_context is not None and not isinstance(
        workflow_context, dict
    ):
        return "workflow_context must be an object"
    plan_response = payload.get("plan_response")
    plan_discussion = payload.get("plan_discussion")
    if plan_discussion is not None and plan_response is not None:
        return "plan_discussion and plan_response are mutually exclusive"
    if plan_discussion is not None:
        if not isinstance(plan_discussion, dict):
            return "plan_discussion must be an object"
        for field in ("request_event_id", "request_id", "revision"):
            if not str(plan_discussion.get(field) or "").strip():
                return f"plan_discussion.{field} is required"
        try:
            discussion_revision = int(plan_discussion.get("revision"))
        except (TypeError, ValueError):
            return "plan_discussion.revision must be a positive integer"
        if discussion_revision < 1:
            return "plan_discussion.revision must be a positive integer"
    if not (
        str(payload.get("message") or "").strip()
        or isinstance(plan_response, dict)
    ):
        return "message or plan_response is required"
    if plan_response is None:
        return ""
    if not isinstance(plan_response, dict):
        return "plan_response must be an object"
    for field in ("request_event_id", "request_id", "revision"):
        if not str(plan_response.get(field) or "").strip():
            return f"plan_response.{field} is required"
    try:
        revision = int(plan_response.get("revision"))
    except (TypeError, ValueError):
        return "plan_response.revision must be a positive integer"
    if revision < 1:
        return "plan_response.revision must be a positive integer"
    answers = plan_response.get("answers")
    if answers is None:
        for field in ("question_id", "option_id"):
            if not str(plan_response.get(field) or "").strip():
                return f"plan_response.{field} is required"
    elif not isinstance(answers, list) or not 1 <= len(answers) <= 3:
        return "plan_response.answers must contain one to three answers"
    else:
        for index, item in enumerate(answers, start=1):
            if not isinstance(item, dict):
                return f"plan_response.answers[{index}] must be an object"
            for field in ("question_id", "option_id"):
                if not str(item.get(field) or "").strip():
                    return (
                        f"plan_response.answers[{index}].{field} is required"
                    )
    return ""


def prepare_web_plan_discussion(
    writer: EventWriter,
    *,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    task_id: str | None,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    discussion = payload.get("plan_discussion")
    if not isinstance(discussion, dict):
        return payload, None
    events = writer.event_log.read_all()
    gate = plan_request_gate(
        events,
        request_event_id=str(discussion.get("request_event_id") or ""),
        request_id=str(discussion.get("request_id") or ""),
        revision=discussion.get("revision"),
        require_valid=False,
    )
    if not gate.get("ok"):
        return payload, _action_failed(
            writer,
            requested=requested,
            action=action,
            requested_action=requested_action,
            task_id=task_id,
            reason=str(gate.get("status") or "plan_discussion_rejected"),
            status_code=409,
            status=str(gate.get("status") or "plan_discussion_rejected"),
        )
    if _plan_already_answered(
        events,
        request_event_id=str(discussion.get("request_event_id") or ""),
        request_id=str(gate.get("request_id") or ""),
        revision=int(gate.get("revision") or 1),
    ):
        return payload, _action_failed(
            writer,
            requested=requested,
            action=action,
            requested_action=requested_action,
            task_id=task_id,
            reason="plan_request_already_answered",
            status_code=409,
            status="plan_request_already_answered",
        )
    request = (
        gate.get("request")
        if isinstance(gate.get("request"), dict)
        else {}
    )
    context_patch, mismatch = _bound_plan_context_patch(request, payload)
    if mismatch:
        return payload, _action_failed(
            writer,
            requested=requested,
            action=action,
            requested_action=requested_action,
            task_id=task_id,
            reason=mismatch,
            status_code=409,
            status="plan_context_mismatch",
        )
    questions = request.get("questions")
    if not isinstance(questions, list):
        questions = [{
            "id": str(request.get("question_id") or ""),
            "header": str(request.get("header") or ""),
            "question": str(request.get("question") or ""),
            "options": request.get("options") or [],
            "allow_other": bool(request.get("allow_other", True)),
        }]
    canonical = {
        "schema_version": "kanban-plan-discussion.v1",
        "request_event_id": str(discussion.get("request_event_id") or ""),
        "request_id": str(gate.get("request_id") or ""),
        "request_digest": str(gate.get("request_digest") or ""),
        "revision": int(gate.get("revision") or 1),
        "header": str(request.get("header") or "Plan"),
        "questions": redact_obj(questions),
        "request_valid": bool(request.get("valid")),
        "validation_error": str(request.get("validation_error") or ""),
        "validation_errors": redact_obj(request.get("validation_errors") or []),
    }
    return {
        **payload,
        **context_patch,
        "plan_discussion": canonical,
    }, None


def prepare_web_plan_response(
    writer: EventWriter,
    *,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    task_id: str | None,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    plan_response = payload.get("plan_response")
    if not isinstance(plan_response, dict):
        return payload, None
    gate = plan_response_gate(
        writer.event_log.read_all(),
        request_event_id=str(plan_response.get("request_event_id") or ""),
        request_id=str(plan_response.get("request_id") or ""),
        revision=plan_response.get("revision"),
        question_id=str(plan_response.get("question_id") or ""),
        option_id=str(plan_response.get("option_id") or ""),
        answer=str(plan_response.get("answer") or ""),
        answers=plan_response.get("answers"),
    )
    if not gate.get("ok"):
        return payload, _action_failed(
            writer,
            requested=requested,
            action=action,
            requested_action=requested_action,
            task_id=task_id,
            reason=str(gate.get("status") or "plan_response_rejected"),
            status_code=409,
            status=str(gate.get("status") or "plan_response_rejected"),
        )
    if gate.get("status") == "already_answered":
        completion_payload = {
            "action": action,
            "requested_action": requested_action,
            "status": "already_answered",
            "answer_event_id": str(gate.get("answer_event_id") or ""),
            "request_id": str(gate.get("request_id") or ""),
        }
        completed = writer.emit(
            "runtime.action.completed",
            actor="web",
            task_id=task_id,
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload=completion_payload,
        )
        writer.emit(
            "web.action.completed",
            actor="web",
            task_id=task_id,
            causation_id=completed.id,
            correlation_id=completed.correlation_id,
            payload=completion_payload,
        )
        return payload, {
            "ok": True,
            "status": "already_answered",
            "action": action,
            "requested_action": requested_action,
            "answer_event_id": str(gate.get("answer_event_id") or ""),
            "request_id": str(gate.get("request_id") or ""),
        }

    source_request = (
        gate.get("request")
        if isinstance(gate.get("request"), dict)
        else {}
    )
    context_patch, mismatch = _bound_plan_context_patch(
        source_request,
        payload,
    )
    if mismatch:
        return payload, _action_failed(
            writer,
            requested=requested,
            action=action,
            requested_action=requested_action,
            task_id=task_id,
            reason=mismatch,
            status_code=409,
            status="plan_context_mismatch",
        )
    prepared = {**payload, **context_patch}
    originating_message_event_id = str(
        source_request.get("originating_message_event_id") or ""
    )
    if originating_message_event_id:
        prepared["plan_origin_message_event_id"] = (
            originating_message_event_id
        )
    originating_message_event_ids = [
        str(item)
        for item in source_request.get(
            "originating_message_event_ids", []
        )
        if str(item)
    ]
    if originating_message_event_ids:
        prepared["plan_origin_message_event_ids"] = (
            originating_message_event_ids
        )
    requirement_digest = str(
        source_request.get("requirement_digest") or ""
    )
    if requirement_digest:
        prepared["plan_requirement_digest"] = requirement_digest
    canonical_response = {
        "schema_version": PLAN_RESPONSE_SCHEMA_VERSION,
        "request_event_id": str(plan_response.get("request_event_id") or ""),
        "request_id": str(gate.get("request_id") or ""),
        "request_digest": str(gate.get("request_digest") or ""),
        "revision": int(gate.get("revision") or 1),
        "question_id": str(gate.get("question_id") or ""),
        "option_id": str(gate.get("option_id") or ""),
        "answer": str(gate.get("answer") or ""),
        "answers": redact_obj(gate.get("answers") or []),
        "source": "web",
        "project_id": str(prepared.get("project_id") or ""),
        "conversation_id": str(prepared.get("conversation_id") or ""),
        "thread_key": str(prepared.get("thread_key") or ""),
    }
    plan_answer = writer.emit(
        PLAN_ANSWERED_EVENT,
        actor="web",
        task_id=task_id,
        causation_id=requested.id,
        correlation_id=requested.correlation_id,
        payload=redact_obj(canonical_response),
    )
    return {
        **prepared,
        "plan_response": canonical_response,
        "plan_answer_event_id": plan_answer.id,
        "message": _plan_response_message(gate),
    }, None


def prepare_web_plan_interaction(
    writer: EventWriter,
    *,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    task_id: str | None,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    prepared, terminal = prepare_web_plan_discussion(
        writer,
        requested=requested,
        action=action,
        requested_action=requested_action,
        task_id=task_id,
        payload=payload,
    )
    if terminal is not None:
        return prepared, terminal
    return prepare_web_plan_response(
        writer,
        requested=requested,
        action=action,
        requested_action=requested_action,
        task_id=task_id,
        payload=prepared,
    )


def plan_proposal_user_message(
    events: Iterable[ZfEvent],
    *,
    payload: dict[str, Any],
    message: str,
) -> str:
    origin_message_event_ids = [
        str(item)
        for item in payload.get("plan_origin_message_event_ids", [])
        if str(item)
    ]
    origin_message_event_id = str(
        payload.get("plan_origin_message_event_id") or ""
    )
    if not origin_message_event_ids and origin_message_event_id:
        origin_message_event_ids = [origin_message_event_id]
    if not origin_message_event_ids:
        return message
    by_id = {event.id: event for event in events}
    origin_messages = []
    for event_id in origin_message_event_ids:
        event = by_id.get(event_id)
        event_payload = (
            event.payload
            if event is not None
            and event.type == "user.message"
            and isinstance(event.payload, dict)
            else {}
        )
        text = str(
            event_payload.get("message")
            or event_payload.get("text")
            or ""
        ).strip()
        if text:
            origin_messages.append(text)
    origin_message = "\n\n".join(origin_messages)
    return f"{origin_message}\n{message}" if origin_message else message


def prepare_headless_plan_draft(
    events: Iterable[ZfEvent],
    *,
    answer: str,
    action_proposal: dict[str, Any] | None,
    project_id: str,
    conversation_id: str,
    thread_key: str,
    fallback_thread_id: str,
    turn_id: str,
    backend: str,
    provider_session_id: str,
    originating_message_event_id: str,
    task_id: str | None,
    task_contract_digest: str = "",
    task_binding_digests: dict[str, str] | None = None,
    workflow_route_eligibility: dict[str, dict[str, str]] | None = None,
    task_contract_errors: dict[str, list[str]] | None = None,
    workflow_context: dict[str, Any] | None = None,
    canonical_channel_prds: dict[str, Any] | None = None,
    correlation_id: str | None,
    requirement_source_cutoff_event_id: str = "",
    config: Any | None = None,
) -> tuple[HeadlessPlanDraft | None, dict[str, Any] | None]:
    event_list = list(events)
    effective_task_contract_digest = task_contract_digest or str(
        (task_binding_digests or {}).get(task_id or "") or ""
    )
    source_cutoff = requirement_source_cutoff_event_id or originating_message_event_id
    source_messages = requirement_source_messages(
        event_list,
        originating_message_event_id=source_cutoff,
        project_id=project_id,
        conversation_id=conversation_id,
        thread_key=thread_key or fallback_thread_id,
    )
    plan_request = extract_plan_request(
        answer,
        plan_context={
            "project_id": project_id,
            "task_id": task_id or "",
            "task_contract_digest": effective_task_contract_digest,
            "task_binding_digests": task_binding_digests or {},
            "workflow_route_eligibility": workflow_route_eligibility or {},
            "task_contract_errors": task_contract_errors or {},
            "conversation_id": conversation_id,
            "thread_key": thread_key or fallback_thread_id,
            "turn_id": turn_id,
            "backend": backend,
            "provider_session_id": provider_session_id,
            "originating_message_event_id": originating_message_event_id,
            "requirement_source_cutoff_event_id": source_cutoff,
            "requirement_source_messages": source_messages,
            "workflow_parameters": workflow_context or {},
            "canonical_channel_prds": canonical_channel_prds or {},
        },
        config=config,
    )
    if plan_request is None:
        return None, action_proposal
    discussion = _origin_plan_discussion(
        event_list,
        originating_message_event_id,
    )
    if discussion:
        discussion_gate = plan_request_gate(
            event_list,
            request_event_id=str(
                discussion.get("request_event_id") or ""
            ),
            request_id=str(discussion.get("request_id") or ""),
            revision=discussion.get("revision"),
            require_valid=False,
        )
        plan_request["request_id"] = str(
            discussion.get("request_id") or ""
        )
        if discussion_gate.get("ok"):
            prior_request = (
                discussion_gate.get("request")
                if isinstance(discussion_gate.get("request"), dict)
                else {}
            )
            for key in (
                "originating_message_event_id",
                "originating_message_event_ids",
                "requirement_digest",
            ):
                plan_request[key] = prior_request.get(key)
            plan_request["request_digest"] = plan_request_digest(plan_request)
        else:
            plan_request["valid"] = False
            plan_request["validation_error"] = (
                "discussion no longer binds the current Plan: "
                + str(
                    discussion_gate.get("status")
                    or "plan_discussion_rejected"
                )
            )
    plan_request = normalize_plan_request_revision(event_list, plan_request)
    if action_proposal is not None:
        plan_request = {
            **plan_request,
            "valid": False,
            "validation_error": (
                "plan_request and action_proposal are mutually exclusive"
            ),
        }
        action_proposal = None
    event = ZfEvent(
        type=PLAN_REQUESTED_EVENT,
        actor="web",
        task_id=task_id,
        correlation_id=correlation_id,
    )
    plan_request["request_event_id"] = event.id
    event.payload = {
        "schema_version": PLAN_REQUEST_SCHEMA_VERSION,
        "source": "kanban-agent.headless",
        "turn_id": turn_id,
        "thread_key": thread_key,
        "project_id": project_id,
        "conversation_id": conversation_id,
        "reply_event_id": "",
        "plan_request": redact_obj(plan_request),
        "request": redact_obj(plan_request),
    }
    return HeadlessPlanDraft(request=plan_request, event=event), action_proposal


def append_headless_plan_draft(
    writer: EventWriter,
    draft: HeadlessPlanDraft | None,
    *,
    reply_event: ZfEvent,
) -> None:
    if draft is None:
        return
    draft.event.causation_id = reply_event.id
    draft.event.payload["reply_event_id"] = reply_event.id
    writer.append(draft.event)


def _plan_response_message(gate: dict[str, Any]) -> str:
    request = gate.get("request") if isinstance(gate.get("request"), dict) else {}
    questions = request.get("questions")
    if not isinstance(questions, list):
        questions = [{
            "id": str(request.get("question_id") or ""),
            "question": str(request.get("question") or ""),
        }]
    answers = gate.get("answers")
    if not isinstance(answers, list):
        answers = [{
            "question_id": str(gate.get("question_id") or ""),
            "answer": str(gate.get("answer") or ""),
        }]
    answer_by_id = {
        str(item.get("question_id") or ""): str(item.get("answer") or "")
        for item in answers
        if isinstance(item, dict)
    }
    lines = [f"Plan: {str(request.get('header') or 'Decision').strip()}"]
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = str(question.get("id") or "")
        lines.extend([
            f"Question: {str(question.get('question') or '').strip()}",
            f"Answer: {answer_by_id.get(question_id, '').strip()}",
        ])
    return "\n".join(lines)


def _bound_plan_context_patch(
    request: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[dict[str, str], str]:
    context_patch: dict[str, str] = {}
    for field in ("backend", "project_id", "conversation_id", "thread_key"):
        expected = str(request.get(field) or "").strip()
        supplied = str(payload.get(field) or "").strip()
        if expected and supplied and supplied != expected:
            return {}, f"plan_context_mismatch:{field}"
        if expected:
            context_patch[field] = expected
    return context_patch, ""


def _plan_already_answered(
    events: Iterable[ZfEvent],
    *,
    request_event_id: str,
    request_id: str,
    revision: int,
) -> bool:
    return any(
        event.type == PLAN_ANSWERED_EVENT
        and isinstance(event.payload, dict)
        and (
            str(event.payload.get("request_event_id") or "")
            == request_event_id
            or (
                str(event.payload.get("request_id") or "") == request_id
                and _safe_revision(event.payload.get("revision")) == revision
            )
        )
        for event in events
    )


def _safe_revision(value: object) -> int:
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


def _origin_plan_discussion(
    events: Iterable[ZfEvent],
    originating_message_event_id: str,
) -> dict[str, Any]:
    event = next(
        (
            item for item in events
            if item.id == originating_message_event_id
            and item.type == "user.message"
            and isinstance(item.payload, dict)
        ),
        None,
    )
    request = (
        event.payload.get("request")
        if event is not None
        and isinstance(event.payload.get("request"), dict)
        else {}
    )
    discussion = request.get("plan_discussion")
    return discussion if isinstance(discussion, dict) else {}


__all__ = [
    "HeadlessPlanDraft",
    "append_headless_plan_draft",
    "plan_proposal_user_message",
    "prepare_web_plan_discussion",
    "prepare_headless_plan_draft",
    "task_contract_context_for_plan",
    "prepare_web_plan_interaction",
    "prepare_web_plan_response",
    "validate_chat_plan_payload",
]
