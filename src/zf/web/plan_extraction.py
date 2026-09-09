"""Extract a resumable Plan question from a Kanban Agent reply."""

from __future__ import annotations

import json
import re
from typing import Any

from zf.core.security.redaction import redact_obj
from zf.runtime.kanban_plan_requests import (
    PLAN_APPLY_ALLOWED_ACTIONS,
    PLAN_DIRECT_APPLY_ACTIONS,
    PLAN_PROPOSAL_ACTIONS,
    PLAN_REQUEST_SCHEMA_VERSION,
    plan_request_digest,
    plan_request_id,
)
from zf.runtime.task_workflow_plans import normalize_task_workflow_parameters
from zf.runtime.workflow_route_catalog import workflow_route_catalog
from zf.runtime.workflow_start import is_workflow_start_action
from zf.web.channel_task_plan import (
    normalize_channel_task_submit_payload,
    selected_channel_task_authority,
)
from zf.web.channel_setup_plan import normalize_channel_setup_submit_payload
from zf.web.plan_workflow_submit import (
    normalize_task_workflow_submit_payload,
)
from zf.web.proposal_extraction import (
    action_json_candidates,
    is_dedicated_bare_plan,
    json_candidates,
    label_is_recommended,
)


def extract_plan_request(
    answer: str,
    *,
    plan_context: dict[str, Any] | None = None,
    config: Any | None = None,
) -> dict[str, Any] | None:
    for candidate in json_candidates(answer):
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            try:
                decoded, _ = json.JSONDecoder().raw_decode(candidate)
            except json.JSONDecodeError:
                continue
        request = normalize_plan_request(
            decoded,
            plan_context=plan_context or {},
            config=config,
        )
        if request is not None:
            return request
    for candidate in action_json_candidates(answer):
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not is_dedicated_bare_plan(decoded):
            continue
        request = normalize_plan_request(
            {"plan_request": decoded},
            plan_context=plan_context or {},
            config=config,
        )
        if request is not None:
            return request
    return None


def normalize_plan_request(
    decoded: Any,
    *,
    plan_context: dict[str, Any] | None = None,
    config: Any | None = None,
) -> dict[str, Any] | None:
    if not isinstance(decoded, dict):
        return None
    raw = decoded.get("plan_request") or decoded.get("input_request")
    if not isinstance(raw, dict):
        return None

    questions = raw.get("questions")
    if isinstance(questions, list):
        question_rows = [item for item in questions if isinstance(item, dict)]
    else:
        question_rows = [raw]
    question = question_rows[0] if question_rows else {}
    validation_errors: list[str] = []
    if not 1 <= len(question_rows) <= 3:
        validation_errors.append("one to three questions are required")
    question_rows = question_rows[:3]

    request_header = str(
        raw.get("header") or question.get("header") or ""
    ).strip()
    question_header = str(
        question.get("header") or request_header
    ).strip()
    question_id = _slug(
        str(question.get("id") or question.get("question_id") or "decision")
    )
    question_text = str(
        question.get("question")
        or question.get("text")
        or raw.get("question")
        or ""
    ).strip()
    if not question_text:
        validation_errors.append("question is required")
    if bool(question.get("isSecret") or question.get("is_secret")):
        validation_errors.append("secret input is not supported")

    context = plan_context or {}
    task_binding_digests = {
        str(key): str(value)
        for key, value in (
            context.get("task_binding_digests", {}).items()
            if isinstance(context.get("task_binding_digests"), dict)
            else []
        )
        if str(key).strip() and str(value).strip()
    }
    context_task_id = str(context.get("task_id") or "").strip()
    context_task_digest = str(
        context.get("task_contract_digest") or ""
    ).strip()
    if context_task_id and context_task_digest:
        task_binding_digests.setdefault(
            context_task_id,
            context_task_digest,
        )
    workflow_route_eligibility = {
        str(task_id): {
            str(route_id): str(reason)
            for route_id, reason in routes.items()
            if str(route_id).strip() and str(reason).strip()
        }
        for task_id, routes in (
            context.get("workflow_route_eligibility", {}).items()
            if isinstance(context.get("workflow_route_eligibility"), dict)
            else []
        )
        if str(task_id).strip() and isinstance(routes, dict)
    }
    workflow_context = context.get("workflow_parameters")
    if workflow_context is None:
        workflow_context = {}
    if not isinstance(workflow_context, dict):
        validation_errors.append("workflow_parameters must be a mapping")
        workflow_context = {}
    normalized_workflow_context, workflow_context_error = (
        normalize_task_workflow_parameters(workflow_context)
    )
    if workflow_context_error:
        validation_errors.append(
            workflow_context_error.replace(
                "unsupported parameter field(s)",
                "unsupported workflow context field(s)",
            )
        )
    submit_action = str(
        raw.get("submit_action")
        or raw.get("commit_action")
        or ""
    ).strip()
    if is_workflow_start_action(submit_action):
        submit_action = "workflow-start"
    submit_mode = "apply" if submit_action else ""
    submit_label = str(
        raw.get("submit_label")
        or raw.get("commit_label")
        or ""
    ).strip()
    allow_other = bool(
        question.get("allow_other", raw.get("allow_other", True))
    )
    if submit_action and submit_action not in PLAN_APPLY_ALLOWED_ACTIONS:
        validation_errors.append(
            f"unsupported Plan submit action: {submit_action}"
        )
    if submit_action and not submit_label:
        validation_errors.append("submit_label is required for an action-bound Plan")
    if submit_action and allow_other:
        validation_errors.append(
            "action-bound Plan requests must set allow_other to false"
        )
    subject_type = _plan_subject_type(
        raw,
        question,
        submit_action=submit_action,
    )
    if subject_type not in {
        "channel_setup",
        "clarification",
        "task_create",
        "task_workflow",
    }:
        validation_errors.append(
            "subject_type must be channel_setup, clarification, "
            "task_create, or task_workflow"
        )
    (
        selected_channel_authority,
        channel_prd_intent,
        channel_selection_error,
    ) = (
        selected_channel_task_authority(raw, context)
    )
    if channel_selection_error:
        validation_errors.append(channel_selection_error)
    if selected_channel_authority:
        if subject_type != "task_create":
            validation_errors.append(
                "Channel PRD selection is only valid for task_create"
            )
        else:
            normalized_workflow_context = {
                **normalized_workflow_context,
                **selected_channel_authority,
            }
    if not request_header:
        request_header = _default_plan_header(subject_type)
    if not question_header:
        question_header = request_header
    discussion_seed = str(raw.get("discussion_seed") or "").strip()
    if subject_type == "channel_setup" and not discussion_seed:
        validation_errors.append(
            "channel_setup requires a clean discussion_seed"
        )
    if len(discussion_seed) > 8000:
        validation_errors.append("discussion_seed exceeds 8000 characters")

    raw_options = question.get("options")
    options: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    if isinstance(raw_options, list):
        for index, item in enumerate(raw_options[:3], start=1):
            if isinstance(item, str):
                label = item.strip()
                description = ""
                explicit_id = ""
                recommended = label_is_recommended(label)
            elif isinstance(item, dict):
                label = str(item.get("label") or "").strip()
                description = str(item.get("description") or "").strip()
                explicit_id = str(item.get("id") or "").strip()
                recommended = bool(item.get("recommended")) or (
                    "(Recommended)" in label or "(推荐)" in label
                )
            else:
                continue
            if not label:
                validation_errors.append(f"option {index} label is required")
                continue
            option_id = _unique_id(
                _slug(explicit_id or label) or f"option-{index}",
                used_ids,
            )
            if option_id == "other":
                validation_errors.append("'other' is reserved for free-form input")
            option: dict[str, Any] = {
                "id": option_id,
                "label": label,
                "description": description,
                "recommended": recommended,
            }
            option_submit_action = submit_action
            option_submit_mode = submit_mode or "continue"
            raw_submit_payload: object = None
            if isinstance(item, dict):
                effect = (
                    item.get("effect")
                    if isinstance(item.get("effect"), dict)
                    else {}
                )
                explicit_option_action = str(
                    effect.get("action")
                    or item.get("submit_action")
                    or item.get("action")
                    or ""
                ).strip()
                explicit_option_mode = str(
                    effect.get("mode")
                    or item.get("submit_mode")
                    or item.get("mode")
                    or ""
                ).strip().lower()
                # A provider often puts a no-op alternative next to a
                # top-level action-bound Plan as ``effect: {mode: continue}``.
                # That explicit continue must not inherit the request action;
                # otherwise a cancel option becomes an invalid hidden apply.
                option_submit_action = (
                    ""
                    if explicit_option_mode == "continue"
                    and not explicit_option_action
                    else explicit_option_action or submit_action
                )
                if is_workflow_start_action(option_submit_action):
                    option_submit_action = "workflow-start"
                option_submit_mode = str(
                    explicit_option_mode
                    or ("apply" if option_submit_action == submit_action and submit_action else "continue")
                ).strip().lower()
                if option_submit_mode == "defer" and not option_submit_action:
                    option_submit_mode = "continue"
                raw_submit_payload = (
                    effect.get("payload")
                    if isinstance(effect.get("payload"), dict)
                    else (
                        item.get("submit_payload")
                        if isinstance(item.get("submit_payload"), dict)
                        else item.get("payload")
                    )
                )
            if option_submit_mode not in {"apply", "continue", "propose"}:
                validation_errors.append(
                    f"option {index}: submit mode must be apply, continue, or propose"
                )
            if (
                option_submit_action
                and option_submit_action not in PLAN_APPLY_ALLOWED_ACTIONS
            ):
                validation_errors.append(
                    f"option {index}: unsupported Plan submit action: "
                    f"{option_submit_action}"
                )
            if (
                option_submit_mode == "apply"
                and option_submit_action not in PLAN_DIRECT_APPLY_ACTIONS
            ):
                validation_errors.append(
                    f"option {index}: action is not allowed for direct Plan apply"
                )
            if (
                option_submit_mode == "propose"
                and option_submit_action not in PLAN_PROPOSAL_ACTIONS
            ):
                validation_errors.append(
                    f"option {index}: action is not allowed for Plan proposal"
                )
            if option_submit_mode == "continue" and option_submit_action:
                validation_errors.append(
                    f"option {index}: continue mode cannot carry an action"
                )
            boundary_error = _subject_action_error(
                subject_type,
                option_submit_mode,
                option_submit_action,
            )
            if boundary_error:
                validation_errors.append(
                    f"option {index}: {boundary_error}"
                )
            if option_submit_action:
                if (
                    option_submit_action == "create-task"
                    and isinstance(raw_submit_payload, dict)
                    and normalized_workflow_context
                ):
                    raw_submit_payload = {
                        **raw_submit_payload,
                        "channel_authority": {
                            key: normalized_workflow_context[key]
                            for key in (
                                "channel_id",
                                "thread_id",
                                "channel_member_id",
                                "leader_revision",
                                "prd_revision",
                                "source_ref",
                                "source_digest",
                            )
                            if key in normalized_workflow_context
                        },
                    }
                if (
                    is_workflow_start_action(option_submit_action)
                    and isinstance(raw_submit_payload, dict)
                ):
                    if (
                        not raw_submit_payload.get("task_id")
                        and context.get("task_id")
                    ):
                        raw_submit_payload = {
                            **raw_submit_payload,
                            "task_id": str(context.get("task_id") or ""),
                        }
                    raw_parameters = raw_submit_payload.get("parameters")
                    if (
                        normalized_workflow_context
                        and raw_parameters in (None, {})
                    ):
                        raw_parameters = {}
                    if (
                        normalized_workflow_context
                        and isinstance(raw_parameters, dict)
                    ):
                        raw_submit_payload = {
                            **raw_submit_payload,
                            "parameters": {
                                **raw_parameters,
                                **normalized_workflow_context,
                            },
                        }
                submit_payload, submit_details, submit_error = (
                    _normalize_plan_submit_payload(
                        option_submit_action,
                        raw_submit_payload,
                        config=config,
                        task_binding_digests=task_binding_digests,
                        workflow_route_eligibility=workflow_route_eligibility,
                    )
                )
                if submit_error:
                    validation_errors.append(
                        f"option {index}: {submit_error}"
                    )
                option["submit_payload"] = submit_payload
                option["submit_details"] = submit_details
                option["submit_action"] = option_submit_action
                option["submit_mode"] = option_submit_mode
            else:
                option["submit_mode"] = "continue"
            options.append(option)
    if not 2 <= len(options) <= 3:
        validation_errors.append("two or three options are required")
    if isinstance(raw_options, list) and len(raw_options) > 3:
        validation_errors.append("at most three options are allowed")
    _normalize_recommended_options(
        options,
        validation_errors,
        question_id=question_id,
    )
    normalized_questions = [{
        "id": question_id,
        "header": question_header,
        "question": question_text,
        "options": options,
        "allow_other": allow_other,
    }]
    question_ids = {question_id}
    for index, extra_question in enumerate(question_rows[1:], start=2):
        normalized_question = _normalize_clarification_question(
            extra_question,
            index=index,
            fallback_header=request_header,
            fallback_allow_other=bool(raw.get("allow_other", True)),
            validation_errors=validation_errors,
        )
        if normalized_question["id"] in question_ids:
            validation_errors.append(
                f"question {index}: question id must be unique"
            )
        question_ids.add(normalized_question["id"])
        normalized_questions.append(normalized_question)
    if len(normalized_questions) > 1:
        if subject_type != "clarification":
            validation_errors.append(
                "multi-question Plan requests must be clarification"
            )
        if submit_action or any(
            str(option.get("submit_action") or "")
            for option in options
        ):
            validation_errors.append(
                "multi-question Plan requests cannot bind an action"
            )
    workflow_task_ids = {
        str(option.get("submit_payload", {}).get("task_id") or "")
        for option in options
        if is_workflow_start_action(str(option.get("submit_action") or ""))
        and isinstance(option.get("submit_payload"), dict)
    }
    workflow_task_ids.discard("")
    if subject_type == "task_workflow":
        if not any(
            is_workflow_start_action(str(option.get("submit_action") or ""))
            for option in options
        ):
            validation_errors.append(
                "task_workflow requires at least one workflow proposal"
            )
        if len(workflow_task_ids) != 1:
            validation_errors.append(
                "task_workflow options must bind exactly one Task"
            )
        if (
            context_task_id
            and workflow_task_ids
            and context_task_id not in workflow_task_ids
        ):
            validation_errors.append(
                "task_workflow options do not match the active Task"
            )
    request_task_id = (
        next(iter(workflow_task_ids))
        if len(workflow_task_ids) == 1
        else context_task_id
    )
    task_contract_errors = context.get("task_contract_errors", {})
    if not isinstance(task_contract_errors, dict):
        task_contract_errors = {}
    if subject_type == "task_workflow" and len(workflow_task_ids) == 1:
        bound_contract_errors = task_contract_errors.get(request_task_id, [])
        if isinstance(bound_contract_errors, list) and bound_contract_errors:
            validation_errors.append(
                "workflow Task contract is incomplete: "
                + "; ".join(
                    str(error).strip()
                    for error in bound_contract_errors
                    if str(error).strip()
                )
            )

    route_catalog = workflow_route_catalog(config)
    request: dict[str, Any] = {
        "schema_version": PLAN_REQUEST_SCHEMA_VERSION,
        "interaction_mode": "plan",
        "subject_type": subject_type,
        "discussion_seed": discussion_seed,
        "revision": _revision(raw.get("revision")),
        "expires_at": str(raw.get("expires_at") or ""),
        "header": request_header,
        "question_id": question_id,
        "question": question_text,
        "options": options,
        "allow_other": allow_other,
        "questions": normalized_questions,
        "reason": str(raw.get("reason") or raw.get("summary") or ""),
        "submit_action": submit_action,
        "submit_mode": submit_mode,
        "submit_label": submit_label,
        "project_id": str(context.get("project_id") or ""),
        "task_id": request_task_id,
        "task_contract_digest": str(
            task_binding_digests.get(request_task_id) or ""
        ),
        "config_digest": (
            str(route_catalog.get("config_digest") or "")
            if subject_type == "task_workflow"
            else ""
        ),
        "conversation_id": str(context.get("conversation_id") or ""),
        "thread_key": str(
            context.get("thread_key")
            or context.get("thread_id")
            or ""
        ),
        "turn_id": str(context.get("turn_id") or ""),
        "backend": str(context.get("backend") or ""),
        "provider_session_id": str(context.get("provider_session_id") or ""),
        "originating_message_event_id": str(
            context.get("originating_message_event_id") or ""
        ),
        "originating_message_event_ids": [
            str(item)
            for item in context.get(
                "originating_message_event_ids", []
            )
            if str(item)
        ],
        "requirement_digest": str(
            context.get("requirement_digest") or ""
        ),
        "workflow_parameters": normalized_workflow_context,
        "channel_prd_ref": str(raw.get("channel_prd_ref") or ""),
        "channel_prd_digest": str(
            raw.get("channel_prd_digest") or ""
        ),
        "channel_prd_intent": channel_prd_intent,
        "valid": not validation_errors,
        "validation_error": "; ".join(dict.fromkeys(validation_errors)),
    }
    digest = plan_request_digest(request)
    request["request_digest"] = digest
    request["request_id"] = plan_request_id(request)
    return redact_obj(request)


def _normalize_clarification_question(
    raw: dict[str, Any],
    *,
    index: int,
    fallback_header: str,
    fallback_allow_other: bool,
    validation_errors: list[str],
) -> dict[str, Any]:
    question_id = _slug(
        str(raw.get("id") or raw.get("question_id") or f"decision-{index}")
    )
    header = str(raw.get("header") or fallback_header or "").strip()
    question = str(raw.get("question") or raw.get("text") or "").strip()
    if not header:
        validation_errors.append(f"question {index}: header is required")
    if not question:
        validation_errors.append(f"question {index}: question is required")
    if bool(raw.get("isSecret") or raw.get("is_secret")):
        validation_errors.append(
            f"question {index}: secret input is not supported"
        )
    raw_options = raw.get("options")
    options: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    if isinstance(raw_options, list):
        for option_index, item in enumerate(raw_options[:3], start=1):
            if isinstance(item, str):
                label = item.strip()
                description = ""
                explicit_id = ""
                recommended = label_is_recommended(label)
                has_effect = False
            elif isinstance(item, dict):
                label = str(item.get("label") or "").strip()
                description = str(item.get("description") or "").strip()
                explicit_id = str(item.get("id") or "").strip()
                recommended = bool(item.get("recommended")) or (
                    label_is_recommended(label)
                )
                has_effect = any(
                    key in item
                    for key in (
                        "effect",
                        "submit_action",
                        "submit_mode",
                        "submit_payload",
                    )
                )
            else:
                continue
            if not label:
                validation_errors.append(
                    f"question {index} option {option_index}: label is required"
                )
                continue
            if has_effect:
                validation_errors.append(
                    f"question {index} option {option_index}: "
                    "clarification options cannot bind an action"
                )
            option_id = _unique_id(
                _slug(explicit_id or label) or f"option-{option_index}",
                used_ids,
            )
            if option_id == "other":
                validation_errors.append(
                    f"question {index}: 'other' is reserved for free-form input"
                )
            options.append({
                "id": option_id,
                "label": label,
                "description": description,
                "recommended": recommended,
                "submit_mode": "continue",
            })
    if not 2 <= len(options) <= 3:
        validation_errors.append(
            f"question {index}: two or three options are required"
        )
    if isinstance(raw_options, list) and len(raw_options) > 3:
        validation_errors.append(
            f"question {index}: at most three options are allowed"
        )
    _normalize_recommended_options(
        options,
        validation_errors,
        question_id=question_id,
    )
    return {
        "id": question_id,
        "header": header,
        "question": question,
        "options": options,
        "allow_other": bool(
            raw.get("allow_other", fallback_allow_other)
        ),
    }


def _normalize_recommended_options(
    options: list[dict[str, Any]],
    validation_errors: list[str],
    *,
    question_id: str,
) -> None:
    if not options:
        return
    recommended = [
        index for index, option in enumerate(options)
        if bool(option.get("recommended"))
    ]
    if not recommended:
        options[0]["recommended"] = True
        return
    if len(recommended) > 1:
        validation_errors.append(
            f"question {question_id}: exactly one recommended option is allowed"
        )
        return
    if recommended[0] != 0:
        options.insert(0, options.pop(recommended[0]))


def _normalize_plan_submit_payload(
    action: str,
    raw_payload: object,
    *,
    config: Any | None = None,
    task_binding_digests: dict[str, str] | None = None,
    workflow_route_eligibility: dict[str, dict[str, str]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    if not isinstance(raw_payload, dict):
        return {}, {}, "submit_payload must be a mapping"
    if is_workflow_start_action(action):
        return normalize_task_workflow_submit_payload(
            raw_payload,
            config=config,
            task_binding_digests=task_binding_digests or {},
            workflow_route_eligibility=workflow_route_eligibility or {},
        )
    if action == "create-task":
        return normalize_channel_task_submit_payload(raw_payload)
    if action == "channel-create-and-start":
        return normalize_channel_setup_submit_payload(raw_payload, config=config)
    return {}, {}, f"unsupported Plan submit action: {action}"


def _plan_subject_type(
    raw: dict[str, Any],
    question: dict[str, Any],
    *,
    submit_action: str,
) -> str:
    explicit = str(
        raw.get("subject_type")
        or question.get("subject_type")
        or ""
    ).strip().lower()
    if explicit:
        return explicit
    raw_options = question.get("options")
    if isinstance(raw_options, list):
        for item in raw_options:
            if not isinstance(item, dict):
                continue
            effect = (
                item.get("effect")
                if isinstance(item.get("effect"), dict)
                else {}
            )
            action = str(
                effect.get("action")
                or item.get("submit_action")
                or ""
            )
            if is_workflow_start_action(action):
                return "task_workflow"
            if action == "create-task":
                return "task_create"
    if submit_action == "channel-create-and-start":
        return "channel_setup"
    if submit_action == "create-task":
        return "task_create"
    return "clarification"


def _default_plan_header(subject_type: str) -> str:
    return {
        "channel_setup": "Channel setup",
        "clarification": "Clarification",
        "task_create": "Create task",
        "task_workflow": "Workflow",
    }.get(subject_type, "Plan")


def _subject_action_error(
    subject_type: str,
    mode: str,
    action: str,
) -> str:
    if subject_type == "channel_setup":
        if mode == "continue" and not action:
            return ""
        if mode != "apply" or action != "channel-create-and-start":
            return "channel_setup only allows direct channel-create-and-start"
    elif subject_type == "task_workflow":
        if mode == "continue" and not action:
            return ""
        if mode != "propose" or not is_workflow_start_action(action):
            return "task_workflow only allows workflow-start proposals"
    elif subject_type == "task_create":
        if mode == "continue" and not action:
            return ""
        if mode != "propose" or action != "create-task":
            return "task_create only allows create-task proposals"
    elif action:
        return "clarification options cannot carry actions"
    return ""


def _slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip().lower())
    return normalized.strip("-")[:64]


def _unique_id(candidate: str, used: set[str]) -> str:
    value = candidate
    suffix = 2
    while value in used:
        value = f"{candidate}-{suffix}"
        suffix += 1
    used.add(value)
    return value


def _revision(value: object) -> int:
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


__all__ = ["extract_plan_request", "normalize_plan_request"]
