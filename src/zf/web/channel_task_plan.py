"""Normalize Task proposals sourced from a confirmed Channel PRD."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def selected_channel_task_authority(
    raw: dict[str, Any],
    context: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str], str]:
    artifact_ref = str(raw.get("channel_prd_ref") or "").strip()
    artifact_digest = str(
        raw.get("channel_prd_digest") or ""
    ).strip().removeprefix("sha256:")
    if not artifact_ref and not artifact_digest:
        if "channel_prd_intent" in raw:
            return {}, {}, (
                "channel_prd_intent requires channel_prd_ref and "
                "channel_prd_digest"
            )
        return {}, {}, ""
    if not artifact_ref or not artifact_digest:
        return {}, {}, (
            "Channel PRD selection requires channel_prd_ref and "
            "channel_prd_digest"
        )
    intent, intent_error = _normalize_channel_prd_intent(
        raw.get("channel_prd_intent"),
        user_semantic_context=str(context.get("user_semantic_context") or ""),
    )
    projection = context.get("canonical_channel_prds")
    items = projection.get("items") if isinstance(projection, dict) else None
    matches = [
        item
        for item in items or []
        if isinstance(item, dict)
        and str(item.get("artifact_ref") or "") == artifact_ref
        and str(item.get("artifact_digest") or "").removeprefix("sha256:")
        == artifact_digest
    ]
    if len(matches) != 1:
        return {}, intent, (
            "Channel PRD selection does not match exactly one current "
            "canonical artifact"
        )
    item = matches[0]
    authority = {
        "channel_id": item.get("channel_id"),
        "thread_id": item.get("thread_id") or "main",
        "channel_member_id": item.get("channel_member_id"),
        "leader_revision": item.get("leader_revision"),
        "prd_revision": item.get("prd_revision"),
        "source_ref": item.get("artifact_ref"),
        "source_digest": item.get("artifact_digest"),
    }
    missing = sorted(
        key for key, value in authority.items() if value in (None, "")
    )
    if missing:
        return {}, intent, (
            "Canonical Channel PRD is missing authority field(s): "
            + ", ".join(missing)
        )
    return authority, intent, intent_error


def _normalize_channel_prd_intent(
    raw: object,
    *,
    user_semantic_context: str,
) -> tuple[dict[str, str], str]:
    if not isinstance(raw, dict):
        return {}, "Channel PRD selection requires channel_prd_intent"

    errors: list[str] = []
    unknown = sorted(set(raw) - {"decision", "source_quote"})
    if unknown:
        errors.append(
            "unsupported channel_prd_intent field(s): " + ", ".join(unknown)
        )
    decision = str(raw.get("decision") or "").strip()
    source_quote = str(raw.get("source_quote") or "").strip()
    if decision != "bind_channel_prd":
        errors.append("channel_prd_intent.decision must be bind_channel_prd")
    if not source_quote:
        errors.append("channel_prd_intent.source_quote is required")
    elif source_quote not in user_semantic_context:
        errors.append(
            "channel_prd_intent.source_quote must occur verbatim in the "
            "user semantic context"
        )
    return {
        "decision": decision,
        "source_quote": source_quote,
    }, "; ".join(errors)


def normalize_channel_task_submit_payload(
    raw_payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    allowed_keys = {
        "acceptance",
        "acceptance_criteria",
        "channel_authority",
        "explicit_non_goals",
        "objective",
        "priority",
        "scope",
        "skills_required",
        "task_id",
        "title",
        "workflow_plan",
    }
    unknown = sorted(set(raw_payload) - allowed_keys)
    if unknown:
        return (
            {},
            {},
            "unsupported submit_payload field(s): " + ", ".join(unknown),
        )
    title = str(
        raw_payload.get("title")
        or raw_payload.get("objective")
        or ""
    ).strip()
    if not title:
        return {}, {}, "submit_payload.title is required"
    authority = (
        dict(raw_payload.get("channel_authority"))
        if isinstance(raw_payload.get("channel_authority"), dict)
        else {}
    )
    required_authority = {
        "channel_id",
        "channel_member_id",
        "leader_revision",
        "prd_revision",
        "source_digest",
        "source_ref",
        "thread_id",
    }
    missing = sorted(
        key for key in required_authority
        if authority.get(key) in (None, "")
    )
    if missing:
        return (
            {},
            {},
            "Channel Task proposal is missing authority field(s): "
            + ", ".join(missing)
            + "; open Channel Details and use Create Task from PRD so the "
            "runtime can bind canonical authority",
        )
    try:
        priority = int(raw_payload.get("priority") or 3)
        leader_revision = int(authority["leader_revision"])
        prd_revision = int(authority["prd_revision"])
    except (TypeError, ValueError):
        return (
            {},
            {},
            "Channel Task proposal revisions/priority must be integers",
        )
    objective = str(raw_payload.get("objective") or title).strip()
    scope = _string_values(raw_payload.get("scope"))
    invalid_scope = [
        entry for entry in scope if not scope_entry_is_path_like(entry)
    ]
    if invalid_scope:
        return (
            {},
            {},
            "submit_payload.scope must contain only repo-relative paths or "
            "globs; move scope prose into objective: "
            + ", ".join(repr(entry) for entry in invalid_scope),
        )
    contract = {
        "schema_version": "task-contract.v1",
        "behavior": objective,
        "acceptance": str(
            raw_payload.get("acceptance")
            or "Deliver the exact confirmed Channel PRD."
        ),
        "acceptance_criteria": _string_values(
            raw_payload.get("acceptance_criteria")
        ),
        "scope": scope,
        "explicit_non_goals": _string_values(
            raw_payload.get("explicit_non_goals")
        ),
        "source_ref": str(authority["source_ref"]),
        "source_revision": str(prd_revision),
        "source_mode": "channel_prd",
        "source_title": title,
        "evidence_contract": {
            "channel_id": str(authority["channel_id"]),
            "thread_id": str(authority["thread_id"]),
            "channel_member_id": str(authority["channel_member_id"]),
            "leader_revision": leader_revision,
            "prd_revision": prd_revision,
            "source_digest": str(authority["source_digest"]),
        },
    }
    payload: dict[str, Any] = {
        "title": title,
        "priority": priority,
        "execution_mode": "workflow",
        "contract": contract,
        "channel_authority": authority,
        "source_artifact": {
            "kind": "channel_prd",
            "ref": str(authority["source_ref"]),
            "digest": str(authority["source_digest"]),
            "revision": prd_revision,
        },
    }
    task_id = str(raw_payload.get("task_id") or "").strip()
    if task_id:
        payload["task_id"] = task_id
    skills = _string_values(raw_payload.get("skills_required"))
    if skills:
        payload["skills_required"] = skills
    workflow_plan = raw_payload.get("workflow_plan")
    if workflow_plan is not None:
        if not isinstance(workflow_plan, dict):
            return {}, {}, "workflow_plan must be a mapping"
        # Real Task/config identity is injected only after controlled creation.
        payload["workflow_plan"] = deepcopy(workflow_plan)
    return payload, {
        "title": title,
        "source_ref": str(authority["source_ref"]),
        "source_digest": str(authority["source_digest"]),
        "prd_revision": prd_revision,
    }, ""


def _string_values(value: object) -> list[str]:
    if isinstance(value, str):
        values: list[object] = value.splitlines()
    elif isinstance(value, list):
        values = value
    else:
        return []
    return list(dict.fromkeys(
        str(item).strip()
        for item in values
        if str(item).strip()
    ))


def scope_entry_is_path_like(entry: object) -> bool:
    """Return whether a proposed scope entry has a path/glob shape."""
    text = str(entry or "").strip()
    if not text or any(ch.isspace() for ch in text):
        return False
    has_extension = _has_short_file_extension(text)
    if any("一" <= ch <= "鿿" for ch in text):
        return "*" in text or has_extension
    if "/" in text or "*" in text:
        return True
    return has_extension


def _has_short_file_extension(text: str) -> bool:
    dot = text.rfind(".")
    suffix_length = len(text) - dot - 1
    return (
        0 < dot < len(text) - 1
        and text[dot + 1:].isalnum()
        and suffix_length <= 5
    )
