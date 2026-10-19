"""Durable Kanban Agent Plan request projection and response gate."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable

from zf.core.events import ZfEvent
from zf.core.security.redaction import redact_obj

PLAN_REQUESTED_EVENT = "kanban.agent.plan.requested"
PLAN_ANSWERED_EVENT = "kanban.agent.plan.answered"
PLAN_REPAIR_REQUESTED_EVENT = "kanban.agent.plan.repair.requested"
PLAN_REPAIR_COMPLETED_EVENT = "kanban.agent.plan.repair.completed"
PLAN_REPAIR_EXHAUSTED_EVENT = "kanban.agent.plan.repair.exhausted"
PLAN_REQUEST_SCHEMA_VERSION = "kanban-plan-request.v3"
PLAN_RESPONSE_SCHEMA_VERSION = "kanban-plan-response.v2"
PLAN_DIRECT_APPLY_ACTIONS = frozenset({"channel-create-and-start"})
PLAN_CHANNEL_MODES = frozenset({
    "conversation",
    "clarification",
    "multi_lens",
})
PLAN_PROPOSAL_ACTIONS = frozenset({
    "create-task",
    "workflow-start",
    "task-workflow-start",
})
PLAN_APPLY_ALLOWED_ACTIONS = (
    PLAN_DIRECT_APPLY_ACTIONS | PLAN_PROPOSAL_ACTIONS
)


def plan_request_digest(request: dict[str, Any]) -> str:
    """Hash the exact semantic question and its conversation binding."""
    questions = _request_questions(request)
    semantics: dict[str, Any] = {
        "project_id": str(request.get("project_id") or ""),
        "task_id": str(request.get("task_id") or ""),
        "task_event_id": str(request.get("task_event_id") or ""),
        "task_contract_digest": str(
            request.get("task_contract_digest") or ""
        ),
        "conversation_id": str(request.get("conversation_id") or ""),
        "thread_key": str(request.get("thread_key") or ""),
        "backend": str(request.get("backend") or ""),
        "originating_message_event_ids": [
            str(item)
            for item in request.get(
                "originating_message_event_ids", []
            )
            if str(item)
        ],
        "requirement_digest": str(
            request.get("requirement_digest") or ""
        ),
        "subject_type": str(request.get("subject_type") or ""),
        "discussion_seed": str(request.get("discussion_seed") or ""),
        "config_digest": str(request.get("config_digest") or ""),
        "submit_action": str(request.get("submit_action") or ""),
        "submit_label": str(request.get("submit_label") or ""),
        "workflow_parameters": request.get("workflow_parameters") or {},
    }
    if len(questions) == 1:
        semantics.update({
            "header": str(request.get("header") or ""),
            "question_id": str(request.get("question_id") or ""),
            "question": str(request.get("question") or ""),
            "options": request.get("options") or [],
            "allow_other": bool(request.get("allow_other", True)),
        })
    else:
        semantics["header"] = str(request.get("header") or "")
        semantics["questions"] = questions
    encoded = json.dumps(
        semantics,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def plan_requirement_digest(
    rows: Iterable[tuple[str, str]],
) -> str:
    encoded = json.dumps(
        [
            {"event_id": str(event_id), "message": str(message)}
            for event_id, message in rows
            if str(event_id) and str(message).strip()
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def plan_request_id(request: dict[str, Any]) -> str:
    """Identify one logical clarification chain independently of its revision."""
    questions = _request_questions(request)
    identity: dict[str, Any] = {
        "project_id": str(request.get("project_id") or ""),
        "task_id": str(request.get("task_id") or ""),
        "subject_type": str(request.get("subject_type") or ""),
        "conversation_id": str(request.get("conversation_id") or ""),
        "thread_key": str(request.get("thread_key") or ""),
        "backend": str(request.get("backend") or ""),
        "originating_message_event_id": str(
            request.get("originating_message_event_id") or ""
        ),
    }
    if len(questions) == 1:
        identity["question_id"] = str(request.get("question_id") or "")
    else:
        identity["question_ids"] = [
            str(question.get("id") or "") for question in questions
        ]
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"plan-{hashlib.sha256(encoded).hexdigest()[:24]}"


def normalize_plan_request_revision(
    events: Iterable[ZfEvent],
    request: dict[str, Any],
) -> dict[str, Any]:
    """Assign a monotonic revision when one clarification chain changes."""
    request_id = str(request.get("request_id") or "")
    request_digest = str(request.get("request_digest") or "")
    requested_revision = _revision(request.get("revision"))
    prior: list[dict[str, Any]] = []
    for event in events:
        if event.type != PLAN_REQUESTED_EVENT:
            continue
        candidate = _event_request(
            event.payload if isinstance(event.payload, dict) else {}
        )
        if str(candidate.get("request_id") or event.id) == request_id:
            prior.append(candidate)
    if not prior:
        return {**request, "revision": requested_revision}

    highest_revision = max(_revision(item.get("revision")) for item in prior)
    if requested_revision > highest_revision:
        revision = requested_revision
    else:
        matching_revisions = [
            _revision(item.get("revision"))
            for item in prior
            if str(item.get("request_digest") or "") == request_digest
        ]
        revision = (
            max(matching_revisions)
            if matching_revisions
            else highest_revision + 1
        )
    return {**request, "revision": revision}


def pending_kanban_plan_requests(
    events: Iterable[ZfEvent],
) -> list[dict[str, Any]]:
    """Fold requested/answered events into the current Plan inbox."""
    latest: dict[str, dict[str, Any]] = {}
    answered_event_ids: set[str] = set()
    answered_revisions: set[tuple[str, int]] = set()
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == PLAN_REQUESTED_EVENT:
            request = _event_request(payload)
            request_id = str(request.get("request_id") or event.id)
            record = {
                **request,
                "request_event_id": event.id,
                "request_id": request_id,
                "source": str(payload.get("source") or event.actor or ""),
                "ts": event.ts,
            }
            for key in (
                "backend",
                "channel_id",
                "conversation_id",
                "member_id",
                "permission_profile",
                "project_id",
                "refs",
                "reply_event_id",
                "thread_id",
                "thread_key",
            ):
                if key in payload:
                    record[key] = payload[key]
            prior = latest.get(request_id)
            if prior is None or _request_sort_key(record) >= _request_sort_key(prior):
                latest[request_id] = record
        elif event.type == PLAN_ANSWERED_EVENT:
            request_event_id = str(payload.get("request_event_id") or "")
            request_id = str(payload.get("request_id") or "")
            if request_event_id:
                answered_event_ids.add(request_event_id)
            if request_id:
                answered_revisions.add((
                    request_id,
                    _revision(payload.get("revision")),
                ))

    pending = [
        redact_obj(record)
        for request_id, record in latest.items()
        if str(record.get("request_event_id") or "") not in answered_event_ids
        and (request_id, _revision(record.get("revision"))) not in answered_revisions
        and bool(record.get("valid"))
        and not _expired(str(record.get("expires_at") or ""))
    ]
    return sorted(
        pending,
        key=lambda item: str(item.get("ts") or ""),
        reverse=True,
    )


def plan_response_gate(
    events: Iterable[ZfEvent],
    *,
    request_event_id: str,
    request_id: str,
    revision: object,
    question_id: str,
    option_id: str,
    answer: str,
    answers: object = None,
) -> dict[str, Any]:
    """Validate a response against one exact, current Plan request."""
    event_list = list(events)
    request_gate = plan_request_gate(
        event_list,
        request_event_id=request_event_id,
        request_id=request_id,
        revision=revision,
    )
    if not request_gate.get("ok"):
        # A later revision may supersede the request after its answer has
        # already been recorded. Preserve retry idempotency for that exact
        # answered request while still rejecting unanswered stale responses.
        if request_gate.get("status") == "plan_request_superseded":
            historical_events = [
                event
                for event in event_list
                if not (
                    event.type == PLAN_REQUESTED_EVENT
                    and event.id != request_event_id
                    and _request_revision_for_event(event) > _revision(revision)
                    and _request_id_for_event(event) == request_id
                )
            ]
            historical_gate = plan_response_gate(
                historical_events,
                request_event_id=request_event_id,
                request_id=request_id,
                revision=revision,
                question_id=question_id,
                option_id=option_id,
                answer=answer,
                answers=answers,
            )
            if historical_gate.get("status") == "already_answered":
                return historical_gate
        return request_gate
    source = request_gate["source"]
    request = request_gate["request"]
    source_request_id = str(request_gate["request_id"])
    source_revision = int(request_gate["revision"])
    questions = _request_questions(request)
    response_rows = _response_answer_rows(
        answers,
        question_id=question_id,
        option_id=option_id,
        answer=answer,
    )
    if len(response_rows) != len(questions):
        return {"ok": False, "status": "plan_answers_incomplete"}
    by_question_id = {
        str(row.get("question_id") or ""): row
        for row in response_rows
        if isinstance(row, dict)
    }
    if len(by_question_id) != len(response_rows):
        return {"ok": False, "status": "plan_question_id_mismatch"}

    canonical_answers: list[dict[str, str]] = []
    selected_options: list[dict[str, Any] | None] = []
    for question in questions:
        current_question_id = str(question.get("id") or "")
        row = by_question_id.get(current_question_id)
        if row is None:
            return {"ok": False, "status": "plan_question_id_mismatch"}
        canonical, selected, error = _canonical_plan_answer(
            question, row,
            allow_revision=bool(request.get("submit_action") or any(
                option.get("submit_action")
                for option in question.get("options") or []
                if isinstance(option, dict)
            )),
        )
        if error:
            return {"ok": False, "status": error}
        canonical_answers.append(canonical)
        selected_options.append(selected)

    selected_effects = [
        (selected, question)
        for selected, question in zip(selected_options, questions, strict=True)
        if isinstance(selected, dict)
        and (
            str(selected.get("submit_action") or "")
            or str(request.get("submit_action") or "")
        )
    ]
    if len(selected_effects) > 1:
        return {"ok": False, "status": "plan_multiple_actions_not_allowed"}
    selected = selected_effects[0][0] if selected_effects else selected_options[0]
    normalized_answer = canonical_answers[0]["answer"]
    normalized_option_id = canonical_answers[0]["option_id"]
    selected_submit_action = (
        str(selected.get("submit_action") or "")
        if isinstance(selected, dict)
        else ""
    ) or str(request.get("submit_action") or "")
    selected_submit_mode = (
        str(selected.get("submit_mode") or "")
        if isinstance(selected, dict)
        else ""
    ) or str(request.get("submit_mode") or "")
    if not selected_submit_mode:
        selected_submit_mode = (
            "apply" if selected_submit_action else "continue"
        )
    selected_submit_payload = (
        selected.get("submit_payload")
        if isinstance(selected, dict)
        and isinstance(selected.get("submit_payload"), dict)
        else {}
    )
    # Other answers revise the request; they never approve an inherited action,
    # including when another question selected an executable option.
    if any(row["option_id"] == "other" for row in canonical_answers):
        selected_submit_action = ""
        selected_submit_mode = "continue"
        selected_submit_payload = {}
    for event in event_list:
        if event.type != PLAN_ANSWERED_EVENT:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            str(payload.get("request_event_id") or "") == request_event_id
            or (
                str(payload.get("request_id") or "") == source_request_id
                and _revision(payload.get("revision")) == source_revision
            )
        ):
            prior_answers = _response_answer_rows(
                payload.get("answers"),
                question_id=str(payload.get("question_id") or ""),
                option_id=str(payload.get("option_id") or ""),
                answer=str(payload.get("answer") or ""),
            )
            same_answer = prior_answers == canonical_answers
            return {
                "ok": same_answer,
                "status": (
                    "already_answered"
                    if same_answer
                    else "plan_request_already_answered"
                ),
                "answer_event_id": event.id,
                "request_id": source_request_id,
                "revision": source_revision,
                "answers": canonical_answers,
            }

    if selected_submit_action == "channel-create-and-start":
        channel_mode = str(
            selected_submit_payload.get("mode") or ""
        ).strip()
        if channel_mode not in PLAN_CHANNEL_MODES:
            return {
                "ok": False,
                "status": "plan_channel_mode_required",
            }

    return {
        "ok": True,
        "status": "ready",
        "request": request,
        "request_id": source_request_id,
        "request_digest": str(request.get("request_digest") or ""),
        "revision": source_revision,
        "question_id": canonical_answers[0]["question_id"],
        "option_id": normalized_option_id,
        "answer": normalized_answer,
        "answers": canonical_answers,
        "submit_action": selected_submit_action,
        "submit_mode": selected_submit_mode,
        "submit_payload": selected_submit_payload,
        "submit_details": (
            selected.get("submit_details")
            if isinstance(selected, dict)
            and isinstance(selected.get("submit_details"), dict)
            else {}
        ),
    }


def plan_request_gate(
    events: Iterable[ZfEvent],
    *,
    request_event_id: str,
    request_id: str,
    revision: object,
    require_valid: bool = True,
) -> dict[str, Any]:
    """Resolve one exact current Plan request without answering it.

    Discussion may set ``require_valid=False`` so an agent can repair its own
    current invalid draft. Answering and side-effect paths keep the default
    fail-closed validity gate.
    """
    event_list = list(events)
    source = next(
        (
            event
            for event in event_list
            if event.id == request_event_id
            and event.type == PLAN_REQUESTED_EVENT
        ),
        None,
    )
    if source is None:
        return {"ok": False, "status": "plan_request_not_found"}
    source_payload = source.payload if isinstance(source.payload, dict) else {}
    request = _event_request(source_payload)
    source_request_id = str(request.get("request_id") or source.id)
    source_revision = _revision(request.get("revision"))
    if request_id != source_request_id:
        return {"ok": False, "status": "plan_request_id_mismatch"}
    if _revision(revision) != source_revision:
        return {
            "ok": False,
            "status": "plan_request_revision_mismatch",
            "revision": source_revision,
        }
    if require_valid and not bool(request.get("valid")):
        return {"ok": False, "status": "plan_request_invalid"}
    if _expired(str(request.get("expires_at") or "")):
        return {"ok": False, "status": "plan_request_expired"}
    for event in event_list:
        if event.type != PLAN_REQUESTED_EVENT or event.id == source.id:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        candidate = _event_request(payload)
        if str(candidate.get("request_id") or event.id) != source_request_id:
            continue
        if _revision(candidate.get("revision")) > source_revision:
            return {
                "ok": False,
                "status": "plan_request_superseded",
                "latest_revision": _revision(candidate.get("revision")),
            }
    return {
        "ok": True,
        "status": "ready",
        "source": source,
        "request": request,
        "request_id": source_request_id,
        "request_digest": str(request.get("request_digest") or ""),
        "revision": source_revision,
    }


def _request_questions(request: dict[str, Any]) -> list[dict[str, Any]]:
    questions = request.get("questions")
    if isinstance(questions, list):
        normalized = [
            item for item in questions
            if isinstance(item, dict) and str(item.get("id") or "")
        ]
        if normalized:
            return normalized
    return [{
        "id": str(request.get("question_id") or ""),
        "header": str(request.get("header") or ""),
        "question": str(request.get("question") or ""),
        "options": [
            item for item in request.get("options") or []
            if isinstance(item, dict)
        ],
        "allow_other": bool(request.get("allow_other", True)),
    }]


def _response_answer_rows(
    answers: object,
    *,
    question_id: str,
    option_id: str,
    answer: str,
) -> list[dict[str, str]]:
    if isinstance(answers, list):
        return [
            {
                "question_id": str(item.get("question_id") or "").strip(),
                "option_id": str(item.get("option_id") or "").strip(),
                "answer": str(item.get("answer") or "").strip(),
            }
            for item in answers
            if isinstance(item, dict)
        ]
    return [{
        "question_id": str(question_id or "").strip(),
        "option_id": str(option_id or "").strip(),
        "answer": str(answer or "").strip(),
    }]


def _canonical_plan_answer(
    question: dict[str, Any],
    response: dict[str, str],
    *,
    allow_revision: bool = False,
) -> tuple[dict[str, str], dict[str, Any] | None, str]:
    normalized_answer = str(response.get("answer") or "").strip()
    normalized_option_id = str(response.get("option_id") or "").strip()
    options = [
        item for item in question.get("options") or []
        if isinstance(item, dict)
    ]
    selected = next(
        (
            item
            for item in options
            if str(item.get("id") or "") == normalized_option_id
        ),
        None,
    )
    if normalized_option_id == "other":
        if not allow_revision and not bool(question.get("allow_other", True)):
            return {}, None, "plan_other_not_allowed"
        if not normalized_answer:
            return {}, None, "plan_answer_required"
    elif selected is not None:
        normalized_answer = str(selected.get("label") or "").strip()
    else:
        return {}, None, "plan_option_invalid"
    return {
        "question_id": str(question.get("id") or ""),
        "option_id": normalized_option_id,
        "answer": normalized_answer,
    }, selected, ""


def _request_sort_key(request: dict[str, Any]) -> tuple[int, str]:
    return (
        _revision(request.get("revision")),
        str(request.get("ts") or ""),
    )


def _event_request(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("plan_request", "request"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _request_id_for_event(event: ZfEvent) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    request = _event_request(payload)
    return str(request.get("request_id") or event.id)


def _request_revision_for_event(event: ZfEvent) -> int:
    payload = event.payload if isinstance(event.payload, dict) else {}
    request = _event_request(payload)
    return _revision(request.get("revision"))


def _expired(value: str) -> bool:
    if not value:
        return False
    try:
        expires_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc)


def _revision(value: object) -> int:
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


__all__ = [
    "PLAN_ANSWERED_EVENT",
    "PLAN_REPAIR_COMPLETED_EVENT",
    "PLAN_REPAIR_EXHAUSTED_EVENT",
    "PLAN_REPAIR_REQUESTED_EVENT",
    "PLAN_APPLY_ALLOWED_ACTIONS",
    "PLAN_DIRECT_APPLY_ACTIONS",
    "PLAN_PROPOSAL_ACTIONS",
    "PLAN_REQUESTED_EVENT",
    "PLAN_REQUEST_SCHEMA_VERSION",
    "PLAN_RESPONSE_SCHEMA_VERSION",
    "normalize_plan_request_revision",
    "pending_kanban_plan_requests",
    "plan_request_digest",
    "plan_request_gate",
    "plan_request_id",
    "plan_response_gate",
]
