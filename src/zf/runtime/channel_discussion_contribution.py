"""Mechanical Phase-1 contribution completion checks."""

from __future__ import annotations

from typing import Any


def valid_phase1_members(
    channel: dict[str, Any],
    session: dict[str, Any],
    thread_id: str,
) -> set[str]:
    """Members whose current blind turn ended in a valid frozen contract."""

    roster = {
        str(member)
        for member in session.get("roster") or []
        if str(member)
    }
    # The original requirement remains the durable PRD provenance anchor. An
    # adaptive pass, however, has its own current blind-turn message; use it
    # for completion matching so preserving provenance does not strand the
    # focused roster in phase 1.
    trigger = str(
        session.get("adaptive_next_round_message_id")
        or session.get("requirement_message_id")
        or ""
    )
    if not roster or not trigger:
        return set()
    requests = {
        str(item.get("target_member_id") or ""): item
        for item in channel.get("reply_requests") or []
        if isinstance(item, dict)
        and str(item.get("message_id") or "") == trigger
        and str(item.get("target_member_id") or "") in roster
        and str(item.get("status") or "") == "completed"
    }
    raw = channel.get("contributions") or []
    contributions = list(raw.values()) if isinstance(raw, dict) else list(raw)
    return {
        member_id
        for member_id, request in requests.items()
        if any(
            _matches_current_reply(
                item,
                request=request,
                member_id=member_id,
                thread_id=thread_id,
                trigger=trigger,
            )
            for item in contributions
        )
    }


def _matches_current_reply(
    item: object,
    *,
    request: dict[str, Any],
    member_id: str,
    thread_id: str,
    trigger: str,
) -> bool:
    if not isinstance(item, dict):
        return False
    return (
        str(item.get("contract_status") or "") == "structured"
        and str(item.get("thread_id") or "main") == thread_id
        and str(item.get("member_id") or "") == member_id
        and str(item.get("message_id") or "") == trigger
        and str(item.get("request_id") or "")
        == str(request.get("request_id") or "")
        and int(item.get("run_generation") or 1)
        == int(request.get("run_generation") or 1)
        and str(item.get("source_reply_event_id") or "")
        == str(request.get("event_id") or "")
        and item.get("questions_frozen") is True
    )


__all__ = ["valid_phase1_members"]
