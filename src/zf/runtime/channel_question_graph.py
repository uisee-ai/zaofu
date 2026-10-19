"""Mechanical question-graph validation and frontier projections."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from zf.runtime.channel_question_provenance import QUESTION_SOURCE_FIELDS


QUESTION_KINDS = frozenset({
    "fact",
    "owner_decision",
    "tradeoff",
    "clarification",
    "revision_request",
})
QUESTION_PRIORITIES = frozenset({"p0", "p1", "p2", "p3"})
OWNER_QUESTION_KINDS = frozenset({
    "owner_decision",
    "tradeoff",
    "clarification",
})
_PRIORITY_ORDER = {"p0": 0, "p1": 1, "p2": 2, "p3": 3}


def question_text_identity(value: object) -> str:
    """Return the conservative identity used for exact question reuse."""
    return " ".join(str(value or "").split()).casefold()


def normalize_question_payload(
    raw: dict[str, Any],
    *,
    question_id: str,
    question: str,
    asked_by: str,
    member_ids: Iterable[str] = (),
) -> tuple[dict[str, Any], str]:
    """Normalize one question without making a semantic decision."""
    kind = str(raw.get("kind") or "owner_decision").strip().lower()
    if kind not in QUESTION_KINDS:
        return {}, f"invalid_question_kind:{kind}"
    priority = str(raw.get("priority") or "p1").strip().lower()
    if priority not in QUESTION_PRIORITIES:
        return {}, f"invalid_question_priority:{priority}"
    target_member_id = str(
        raw.get("target_member_id") or "owner"
    ).strip()
    if (
        kind in OWNER_QUESTION_KINDS
        and target_member_id in {"operator", "owner:operator"}
    ):
        target_member_id = "owner"
    known_members = {
        str(member_id).strip()
        for member_id in member_ids
        if str(member_id).strip()
    }
    if (
        target_member_id
        and target_member_id != "owner"
        and known_members
        and target_member_id not in known_members
    ):
        return {}, f"unknown_question_target:{target_member_id}"
    if kind == "fact" and target_member_id == "owner":
        return {}, "fact_question_requires_member_target"
    raw_dependencies = raw.get("depends_on")
    if raw_dependencies in (None, ""):
        raw_dependencies = []
    if not isinstance(raw_dependencies, list):
        return {}, "question_dependencies_must_be_a_list"
    depends_on = _string_list(raw_dependencies, limit=16)
    if len(depends_on) != len(raw_dependencies):
        return {}, "question_dependencies_must_be_non_empty_strings"
    options, option_error = _normalize_question_options(raw.get("options"))
    if option_error:
        return {}, option_error
    normalized = {
        "question_id": str(question_id).strip(),
        "question": str(question).strip(),
        "category": str(raw.get("category") or "clarification").strip(),
        "kind": kind,
        "depends_on": depends_on,
        "priority": priority,
        "why_it_matters": str(raw.get("why_it_matters") or "").strip(),
        "recommended_answer": str(
            raw.get("recommended_answer") or ""
        ).strip(),
        "options": options,
        "allow_other": bool(raw.get("allow_other", True)),
        "target_member_id": target_member_id or "owner",
        "asked_by": str(asked_by).strip(),
    }
    if not normalized["question_id"]:
        return {}, "question_id_required"
    if not normalized["question"]:
        return {}, "question_text_required"
    return normalized, ""


def validate_question_graph(
    questions: Iterable[dict[str, Any]],
) -> str:
    """Validate identity, dependency existence, and acyclicity."""
    by_id: dict[str, dict[str, Any]] = {}
    for question in questions:
        question_id = str(question.get("question_id") or "").strip()
        if not question_id:
            return "question_id_required"
        if question_id in by_id:
            return f"duplicate_question_id:{question_id}"
        by_id[question_id] = question
    for question_id, question in by_id.items():
        for dependency in _string_list(question.get("depends_on"), limit=16):
            if dependency == question_id:
                return f"question_self_dependency:{question_id}"
            if dependency not in by_id:
                return f"unknown_question_dependency:{question_id}:{dependency}"

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(question_id: str) -> str:
        if question_id in visited:
            return ""
        if question_id in visiting:
            return f"question_dependency_cycle:{question_id}"
        visiting.add(question_id)
        for dependency in _string_list(
            by_id[question_id].get("depends_on"),
            limit=16,
        ):
            error = visit(dependency)
            if error:
                return error
        visiting.remove(question_id)
        visited.add(question_id)
        return ""

    for question_id in sorted(by_id):
        error = visit(question_id)
        if error:
            return error
    return ""


def question_frontier(
    channel: dict[str, Any] | None,
    *,
    thread_id: str,
) -> list[dict[str, Any]]:
    """Return open questions whose dependencies are mechanically settled."""
    records = _thread_questions(channel, thread_id=thread_id)
    by_id = {
        str(record.get("question_id") or ""): record
        for record in records
        if str(record.get("question_id") or "")
    }
    ready = [
        record
        for record in records
        if str(record.get("status") or "") == "open"
        and all(
            _dependency_settled(dependency, by_id, seen=set())
            for dependency in _string_list(
                record.get("depends_on"),
                limit=16,
            )
        )
    ]
    return sorted(ready, key=_question_sort_key)


def owner_questionnaire(
    channel: dict[str, Any] | None,
    *,
    thread_id: str,
) -> list[dict[str, Any]]:
    """Return the current owner-facing decision frontier."""
    return [
        item
        for item in question_frontier(channel, thread_id=thread_id)
        if str(item.get("kind") or "owner_decision") in OWNER_QUESTION_KINDS
        and str(item.get("target_member_id") or "owner") == "owner"
    ]


def question_graph_digest(
    channel: dict[str, Any] | None,
    *,
    thread_id: str,
) -> str:
    body = json.dumps(
        # Recovering navigation metadata must not invalidate an existing decision frontier.
        [{key: value for key, value in question.items() if key not in QUESTION_SOURCE_FIELDS}
         for question in _thread_questions(channel, thread_id=thread_id)],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _thread_questions(
    channel: dict[str, Any] | None,
    *,
    thread_id: str,
) -> list[dict[str, Any]]:
    raw = (channel or {}).get("open_questions") or []
    candidates = list(raw.values()) if isinstance(raw, dict) else list(raw)
    return sorted(
        [
            dict(item)
            for item in candidates
            if isinstance(item, dict)
            and str(item.get("thread_id") or "main") == thread_id
            and str(item.get("question_id") or "")
        ],
        key=lambda item: str(item.get("question_id") or ""),
    )


def _dependency_settled(
    question_id: str,
    by_id: dict[str, dict[str, Any]],
    *,
    seen: set[str],
) -> bool:
    if question_id in seen:
        return False
    question = by_id.get(question_id)
    if not isinstance(question, dict):
        return False
    status = str(question.get("status") or "")
    if status == "resolved":
        return True
    if status != "merged":
        return False
    merged_into = str(question.get("merged_into") or "")
    if not merged_into:
        return False
    return _dependency_settled(
        merged_into,
        by_id,
        seen={*seen, question_id},
    )


def _question_sort_key(item: dict[str, Any]) -> tuple[int, str]:
    return (
        _PRIORITY_ORDER.get(str(item.get("priority") or "p1"), 1),
        str(item.get("question_id") or ""),
    )


def _string_list(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(
        str(item).strip()
        for item in value
        if isinstance(item, str) and str(item).strip()
    ))[:limit]


def _normalize_question_options(
    value: object,
) -> tuple[list[dict[str, Any]], str]:
    if value in (None, "", []):
        return [], ""
    if not isinstance(value, list):
        return [], "question_options_must_be_a_list"
    if not 2 <= len(value) <= 3:
        return [], "question_options_require_two_or_three_items"
    options: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(value, 1):
        if not isinstance(raw, dict):
            return [], "question_options_must_be_objects"
        option_id = str(raw.get("id") or f"option-{index}").strip()
        label = str(raw.get("label") or "").strip()
        if not option_id or not label:
            return [], "question_options_require_id_and_label"
        if option_id == "other" or option_id in seen_ids:
            return [], f"invalid_question_option_id:{option_id}"
        seen_ids.add(option_id)
        options.append({
            "id": option_id,
            "label": label,
            "description": str(raw.get("description") or "").strip(),
            "recommended": bool(raw.get("recommended"))
            or "(recommended)" in label.lower()
            or "(推荐)" in label,
        })
    recommended = [
        index for index, option in enumerate(options)
        if option["recommended"]
    ]
    if len(recommended) > 1:
        return [], "question_options_allow_one_recommendation"
    if not recommended:
        options[0]["recommended"] = True
    elif recommended[0] != 0:
        options.insert(0, options.pop(recommended[0]))
    return options, ""


__all__ = [
    "OWNER_QUESTION_KINDS",
    "QUESTION_KINDS",
    "QUESTION_PRIORITIES",
    "normalize_question_payload",
    "owner_questionnaire",
    "question_frontier",
    "question_graph_digest",
    "validate_question_graph",
]
