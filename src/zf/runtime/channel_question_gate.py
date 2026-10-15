"""Mechanical owner-question gates derived from one Channel projection."""

from __future__ import annotations

from typing import Any


def question_dedup_gate_state(
    channel: dict[str, Any] | None,
    *,
    thread_id: str,
) -> str:
    """Return whether owner answers must wait for question consolidation.

    The phase-2 dedup reply is bound to one complete ledger digest. Letting an
    owner resolve one of the raw questions while that reply is running races
    the digest and can also make a previously open merge target closed. This
    module deliberately depends on no projection or provider code so action
    handlers and read projections share exactly the same gate.
    """
    current = channel or {}
    raw_questions = current.get("open_questions") or []
    questions = (
        raw_questions.values()
        if isinstance(raw_questions, dict)
        else raw_questions
    )
    open_questions = [
        item for item in questions
        if isinstance(item, dict)
        and str(item.get("thread_id") or "main") == thread_id
        and str(item.get("status") or "") == "open"
    ]
    if not open_questions:
        return "open"

    discussions = current.get("discussions") or {}
    session = discussions.get(thread_id) if isinstance(discussions, dict) else {}
    if not isinstance(session, dict):
        session = {}
    phase = str(session.get("state") or "")
    if phase != "phase2_relay":
        return "open"
    if (str(session.get("phase_reason") or "") == "consensus_blocked"
            and all(str(item.get("category") or "") == "blocker"
                    for item in open_questions)):
        # Review blockers are already explicit, separate questions. The
        # phase-3 rollback does not dispatch a phase-2 dedup request.
        return "open"

    session_started_at = str(session.get("started_at") or "")
    requests = [
        item
        for item in current.get("question_dedup_requests") or []
        if isinstance(item, dict)
        and str(item.get("thread_id") or "main") == thread_id
        and (
            not session_started_at
            or not str(item.get("updated_at") or item.get("ts") or "")
            or str(item.get("updated_at") or item.get("ts") or "")
            >= session_started_at
        )
    ]
    if not requests:
        if str(session.get("phase_reason") or "") == "synthesis_questions_opened":
            # These questions came from the single structured synthesis. This
            # rollback does not schedule blind-round dedup work.
            return "open"
        # The phase transition is appended before its dedup request. Fail
        # closed during that short projection window as well.
        return "consolidating"
    latest_status = str(requests[-1].get("status") or "")
    if latest_status == "applied":
        return "open"
    if latest_status == "exhausted":
        return "blocked"
    return "consolidating"


__all__ = ["question_dedup_gate_state"]
