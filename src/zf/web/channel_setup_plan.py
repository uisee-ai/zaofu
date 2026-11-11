"""Normalize action-bound Channel setup Plan options."""

from __future__ import annotations

from typing import Any

from zf.runtime.channel_contracts import discussion_engine_mode
from zf.runtime.channel_profiles import channel_profile_selection_manifest
from zf.runtime.channel_templates import materialize_channel_template


def normalize_channel_setup_submit_payload(
    raw_payload: dict[str, Any],
    *,
    config: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    allowed_keys = {
        "channel_id",
        "mode",
        "name",
        "overrides",
        "task_id",
        "template_id",
        "thread_id",
        "dispatch_policy",
    }
    unknown = sorted(set(raw_payload) - allowed_keys)
    if unknown:
        return (
            {},
            {},
            "unsupported submit_payload field(s): " + ", ".join(unknown),
        )
    template_id = str(raw_payload.get("template_id") or "").strip()
    if not template_id:
        return {}, {}, "submit_payload.template_id is required"
    overrides, override_error = _normalize_channel_setup_overrides(
        raw_payload.get("overrides")
    )
    if override_error:
        return {}, {}, override_error
    materialized, error = materialize_channel_template(
        template_id,
        overrides={**overrides, "discussion_mode": str(raw_payload.get("mode") or overrides.get("discussion_mode") or "libre_discuss")},
    )
    if error or materialized is None:
        return {}, {}, error or "channel template preflight failed"
    profile_rows, profile_selection_digest, profile_error = (
        channel_profile_selection_manifest(
            config,
            template_id=template_id,
            members=list(materialized.get("members") or []),
        )
    )
    if profile_error:
        return {}, {}, profile_error

    mode = str(raw_payload.get("mode") or "libre_discuss").strip()
    if not mode:
        return (
            {},
            {},
            "submit_payload.mode is required for Channel setup Plans",
        )
    if mode not in {"libre_discuss", "conversation", "clarification", "multi_lens"}:
        return (
            {},
            {},
            "submit_payload.mode must be libre_discuss, conversation, clarification, or multi_lens",
        )
    dispatch_policy = str(raw_payload.get("dispatch_policy") or "auto").strip()
    if dispatch_policy not in {"auto", "trigger_only"}:
        return (
            {},
            {},
            "submit_payload.dispatch_policy must be auto or trigger_only",
        )

    payload: dict[str, Any] = {
        "template_id": template_id,
        "mode": mode,
    }
    if "dispatch_policy" in raw_payload:
        payload["dispatch_policy"] = dispatch_policy
    name = str(raw_payload.get("name") or "").strip()
    if name:
        payload["name"] = name
    for key in ("channel_id", "task_id", "thread_id"):
        value = str(raw_payload.get(key) or "").strip()
        if value:
            payload[key] = value
    if isinstance(overrides, dict) and overrides:
        payload["overrides"] = overrides
    payload["expected_profile_selection_digest"] = profile_selection_digest

    members = [
        {
            "member_id": str(member.get("member_id") or ""),
            "role": str(member.get("channel_role") or ""),
            "permission_profile": str(
                member.get("permission_profile") or "read_only"
            ),
        }
        for member in materialized["members"]
        if isinstance(member, dict)
    ]
    discussion = (
        materialized.get("discussion")
        if isinstance(materialized.get("discussion"), dict)
        else {}
    )
    leader_member_id = str(materialized.get("leader_member_id") or "").strip()
    default_responder_id = str(
        discussion.get("default_responder_id")
        or materialized.get("default_responder_id")
        or ""
    ).strip()
    if not leader_member_id or not default_responder_id:
        return {}, {}, "Channel setup requires a moderator and default responder"
    member_ids = {
        str(member.get("member_id") or "").strip()
        for member in materialized.get("members") or []
        if isinstance(member, dict)
    }
    if leader_member_id not in member_ids:
        return {}, {}, "Channel setup moderator must be an active member"
    if default_responder_id not in member_ids:
        return {}, {}, "Channel setup default responder must be an active member"
    engine_mode = discussion_engine_mode(mode)
    details = {
        "template_id": template_id,
        "template_name": str(materialized.get("name") or template_id),
        "template_version": str(materialized.get("template_version") or ""),
        "template_digest": str(materialized.get("template_digest") or ""),
        "materialization_digest": str(
            materialized.get("materialization_digest") or ""
        ),
        "member_count": len(members),
        "members": members,
        "leader_member_id": leader_member_id,
        "moderator_member_id": leader_member_id,
        "default_responder_id": default_responder_id,
        "workflow_trigger": "moderator_propose_workflow",
        "product_mode": mode,
        "mode": mode,
        "engine_mode": engine_mode,
        "dispatch_policy": dispatch_policy,
        "routing_strategy": {
            "libre_discuss": "quiet_user_led",
            "conversation": "single_responder",
            "clarification": "facilitated_relay",
            "multi_lens": "blind_fanout_then_synthesis",
        }[mode],
        "first_pass_reply_count": (
            0 if mode == "libre_discuss" else len(members)
            if engine_mode == "fanout_then_synthesis"
            else min(1, len(members))
        ),
        "max_rounds": int(discussion.get("max_rounds") or 0),
        "round_policy": (
            "explicit_cap"
            if discussion.get("max_rounds_explicit") is True
            else "synthesis_adaptive"
        ),
        "profile_selection_digest": profile_selection_digest,
        "profiles": profile_rows,
    }
    return payload, details, ""


def _normalize_channel_setup_overrides(
    raw_overrides: object,
) -> tuple[dict[str, Any], str]:
    """Normalize Plan-only aliases before template materialization.

    `disabled_roles` is a common model-facing shorthand. The runtime owns one
    canonical template shape, so map it into the existing role slot override
    rather than teaching downstream control actions another config dialect.
    """
    if raw_overrides is None:
        return {}, ""
    if not isinstance(raw_overrides, dict):
        return {}, "submit_payload.overrides must be a mapping"
    overrides = dict(raw_overrides)
    disabled_roles = overrides.pop("disabled_roles", None)
    if disabled_roles is None:
        return overrides, ""
    if not isinstance(disabled_roles, list) or not all(
        isinstance(role, str) and role.strip()
        for role in disabled_roles
    ):
        return {}, "overrides.disabled_roles must be a non-empty role string list"

    raw_role_overrides = overrides.get("role_overrides")
    if raw_role_overrides is None:
        role_overrides: dict[str, Any] = {}
    elif isinstance(raw_role_overrides, dict):
        role_overrides = dict(raw_role_overrides)
    else:
        return {}, "role_overrides must be a mapping"

    for role in dict.fromkeys(role.strip() for role in disabled_roles):
        current = role_overrides.get(role)
        if current is None:
            slot_override: dict[str, Any] = {}
        elif isinstance(current, dict):
            slot_override = dict(current)
        else:
            return {}, f"role_overrides.{role} must be a mapping"
        if "enabled" in slot_override and slot_override["enabled"] is not False:
            return (
                {},
                "overrides.disabled_roles conflicts with "
                f"role_overrides.{role}.enabled",
            )
        slot_override["enabled"] = False
        role_overrides[role] = slot_override
    if role_overrides:
        overrides["role_overrides"] = role_overrides
    return overrides, ""


__all__ = ["normalize_channel_setup_submit_payload"]
