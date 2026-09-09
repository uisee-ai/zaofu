"""Read-only operator attention derived from Channel discussion facts."""

from __future__ import annotations

from typing import Any

from zf.runtime.channel_question_gate import question_dedup_gate_state


_ACTIVE_REPLY_STATUSES = {"pending", "queued", "running", "started"}
_QUEUED_REPLY_STATUSES = {"pending", "queued"}
_RUNNING_REPLY_STATUSES = {"running", "started"}
_FAILED_REPLY_STATUSES = {"failed", "rejected", "escalated"}


def project_discussion_attention(
    channel: dict[str, Any],
    reply_requests: list[dict[str, Any]],
    owner_questionnaires: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Derive actionable UI state without becoming discussion truth."""
    thread_ids = {
        str(thread_id)
        for thread_id in channel["discussions"]
    } | {
        str(thread_id)
        for thread_id in channel["consensus"]
    } | {
        str(item.get("thread_id") or "main")
        for key in ("open_questions", "synthesis_requests", "syntheses")
        for item in (
            channel[key].values()
            if isinstance(channel[key], dict)
            else channel[key]
        )
        if isinstance(item, dict)
    }
    projection: dict[str, dict[str, Any]] = {}

    for thread_id in sorted(thread_ids):
        session = channel["discussions"].get(thread_id)
        if not isinstance(session, dict):
            session = {}
        session_started_at = str(session.get("started_at") or "")

        def current_session_item(
            item: dict[str, Any],
            *,
            time_keys: tuple[str, ...],
        ) -> bool:
            if str(item.get("thread_id") or "main") != thread_id:
                return False
            item_time = next(
                (str(item.get(key) or "") for key in time_keys if item.get(key)),
                "",
            )
            return (
                not session_started_at
                or not item_time
                or item_time >= session_started_at
            )

        replies = [
            item for item in reply_requests
            if current_session_item(
                item,
                time_keys=("created_at", "updated_at", "ts"),
            )
        ]
        questions = [
            item for item in channel["open_questions"].values()
            if isinstance(item, dict)
            and str(item.get("thread_id") or "main") == thread_id
        ]
        open_questions = [
            item for item in questions
            if str(item.get("status") or "") == "open"
        ]
        owner_questions = owner_questionnaires.get(thread_id, [])
        question_consolidation_status = question_dedup_gate_state(
            channel,
            thread_id=thread_id,
        )
        question_consolidation_pending = (
            question_consolidation_status != "open"
        )
        visible_owner_questions = (
            [] if question_consolidation_pending else owner_questions
        )
        synthesis_requests = [
            item for item in channel["synthesis_requests"]
            if isinstance(item, dict)
            and current_session_item(item, time_keys=("ts",))
        ]
        syntheses = [
            item for item in channel["syntheses"]
            if isinstance(item, dict)
            and current_session_item(item, time_keys=("ts",))
        ]
        consensus = channel["consensus"].get(thread_id)
        if not isinstance(consensus, dict):
            consensus = {}

        statuses = [str(item.get("status") or "") for item in replies]
        active_replies = [
            item for item in replies
            if str(item.get("status") or "") in _ACTIVE_REPLY_STATUSES
        ]
        active_agents_by_id: dict[str, dict[str, str]] = {}
        for item in active_replies:
            member_id = str(
                item.get("target_member_id") or item.get("member_id") or ""
            )
            if not member_id:
                continue
            active_started_at = str(
                item.get("started_at")
                or item.get("updated_at")
                or item.get("created_at")
                or ""
            )
            candidate = {
                "member_id": member_id,
                "request_id": str(item.get("request_id") or ""),
                "status": str(item.get("status") or "pending"),
                "started_at": active_started_at,
            }
            current = active_agents_by_id.get(member_id)
            if current is None or active_started_at >= current["started_at"]:
                active_agents_by_id[member_id] = candidate
        active_agents = sorted(
            active_agents_by_id.values(),
            key=lambda item: (item["started_at"], item["member_id"]),
        )
        failed_count = sum(
            status in _FAILED_REPLY_STATUSES for status in statuses
        )
        phase = str(session.get("state") or "idle")
        outcome = str(session.get("last_outcome") or "")
        latest_synthesis_status = str(
            synthesis_requests[-1].get("status") or ""
        ) if synthesis_requests else ""
        consensus_time = str(consensus.get("ts") or "")
        consensus_is_current = bool(consensus) and (
            not session_started_at
            or not consensus_time
            or consensus_time >= session_started_at
        )
        consensus_reached = (
            consensus_is_current and bool(consensus.get("reached_event_id"))
        )
        owner_confirmed = (
            consensus_is_current and bool(consensus.get("human_confirmed"))
        )
        owner_confirmation_required = bool(
            consensus_is_current
            and consensus.get("artifact_ref")
            and not consensus_reached
            and not owner_confirmed
        )

        state, reason = _attention_state(
            phase=phase,
            outcome=outcome,
            owner_questions=bool(visible_owner_questions),
            question_consolidation_status=question_consolidation_status,
            owner_confirmation_required=owner_confirmation_required,
            has_replies=bool(replies),
            active_replies=bool(active_replies),
            failed_replies=failed_count,
            synthesis_status=latest_synthesis_status,
            has_synthesis=bool(syntheses),
            consensus_reached=consensus_reached,
            owner_confirmed=owner_confirmed,
            open_questions=bool(open_questions),
        )
        execution_state = _execution_state(
            phase=phase,
            outcome=outcome,
            owner_questions=bool(visible_owner_questions),
            question_consolidation_status=question_consolidation_status,
            owner_confirmation_required=owner_confirmation_required,
            has_replies=bool(replies),
            active_replies=bool(active_replies),
            failed_replies=failed_count,
            synthesis_status=latest_synthesis_status,
            has_synthesis=bool(syntheses),
            consensus_reached=consensus_reached,
            owner_confirmed=owner_confirmed,
            open_questions=bool(open_questions),
        )
        attention_kind = (
            "consolidating"
            if question_consolidation_pending
            else "question"
            if visible_owner_questions
            else "review"
            if owner_confirmation_required
            else "none"
        )
        next_action = _next_action(
            state,
            reason=reason,
            has_result=bool(syntheses or consensus),
        )
        activity_times = [
            str(session.get(key) or "")
            for key in ("started_at", "phase_changed_at", "closed_at")
        ]
        if consensus_is_current:
            activity_times.append(consensus_time)
        for item in [*replies, *questions, *synthesis_requests, *syntheses]:
            activity_times.extend(
                str(item.get(key) or "")
                for key in ("updated_at", "resolved_at", "created_at", "ts")
            )
        activity_times.extend(
            str(item.get("ts") or "")
            for item in channel["question_activity"]
            if isinstance(item, dict)
            and str(item.get("thread_id") or "main") == thread_id
        )

        projection[thread_id] = {
            "schema_version": "channel.discussion-attention.v2",
            "is_derived_projection": True,
            "thread_id": thread_id,
            "state": state,
            "reason": reason,
            "next_action": next_action,
            "execution_state": execution_state,
            "attention_kind": attention_kind,
            "blocking_scope": (
                "phase"
                if attention_kind in {"question", "consolidating"}
                else "workflow"
                if attention_kind == "review"
                else "none"
            ),
            "blocks_transition": (
                "synthesis"
                if attention_kind in {"question", "consolidating"}
                else "consensus"
                if attention_kind == "review"
                else ""
            ),
            "kernel_phase": phase,
            "last_outcome": outcome,
            "participant_count": len(session.get("roster") or []),
            "active_agent_count": len(active_agents),
            "active_agents": active_agents,
            "oldest_active_started_at": min(
                (
                    item["started_at"]
                    for item in active_agents
                    if item["started_at"]
                ),
                default="",
            ),
            "active_reply_count": len(active_replies),
            "queued_reply_count": sum(
                status in _QUEUED_REPLY_STATUSES for status in statuses
            ),
            "running_reply_count": sum(
                status in _RUNNING_REPLY_STATUSES for status in statuses
            ),
            "completed_reply_count": sum(
                status == "completed" for status in statuses
            ),
            "failed_reply_count": failed_count,
            "open_question_count": len(open_questions),
            "owner_question_count": len(visible_owner_questions),
            "total_question_count": len(questions),
            "resolved_question_count": len(questions) - len(open_questions),
            "question_consolidation_status": question_consolidation_status,
            "last_activity_at": max(
                (item for item in activity_times if item),
                default="",
            ),
            "can_drain_replies": bool(active_replies),
            # A default conversation deliberately becomes quiet after its
            # reply cycle settles. Quiet must not remove the explicit
            # Finalize/Synthesize path: the operator may turn complete,
            # question-free discussion into a PRD from Details.
            "can_synthesize": (
                state == "ready"
                or (
                    phase == "active"
                    and bool(replies)
                    and not active_replies
                    and not failed_count
                    and not visible_owner_questions
                    and not open_questions
                    and not latest_synthesis_status
                    and not syntheses
                    and not consensus
                )
            ),
            "can_restart": state == "blocked",
            "can_review_questions": bool(visible_owner_questions),
            "can_review_result": reason == "owner_confirmation_required",
            "can_view_activity": bool(
                session
                or replies
                or questions
                or syntheses
                or synthesis_requests
                or consensus
            ),
        }
    return projection


def _attention_state(
    *,
    phase: str,
    outcome: str,
    owner_questions: bool,
    question_consolidation_status: str,
    owner_confirmation_required: bool,
    has_replies: bool,
    active_replies: bool,
    failed_replies: int,
    synthesis_status: str,
    has_synthesis: bool,
    consensus_reached: bool,
    owner_confirmed: bool,
    open_questions: bool,
) -> tuple[str, str]:
    if question_consolidation_status == "consolidating":
        return "running", "question_consolidation_active"
    if question_consolidation_status == "blocked":
        return "blocked", "question_consolidation_blocked"
    if owner_questions:
        return "needs_input", "owner_questions_open"
    if owner_confirmation_required:
        return "needs_input", "owner_confirmation_required"
    if phase == "idle" and outcome == "consensus":
        return "done", "discussion_completed"
    if phase == "idle" and outcome == "stalled":
        return "blocked", "discussion_stalled"
    if active_replies:
        return "running", "replies_active"
    if failed_replies or synthesis_status == "blocked":
        return (
            "blocked",
            "reply_failures" if failed_replies else "synthesis_blocked",
        )
    if phase == "phase2_relay":
        return (
            ("blocked", "unresolved_questions")
            if open_questions
            else ("ready", "synthesis_ready")
        )
    if phase == "phase3_synthesis":
        if has_synthesis and not consensus_reached and not owner_confirmed:
            return "needs_input", "owner_confirmation_required"
        if consensus_reached:
            return "done", "discussion_completed"
        if synthesis_status in {"requested", "repair_requested"}:
            return "running", "synthesis_active"
        if open_questions:
            return "blocked", "unresolved_questions"
        return "running", "synthesis_active"
    if phase == "active":
        return (
            ("done", "discussion_idle")
            if has_replies
            else ("running", "discussion_active")
        )
    if phase == "phase1_blind":
        return "running", "discussion_active"
    if open_questions:
        return "blocked", "unresolved_questions"
    return "done", "discussion_idle"


def _execution_state(
    *,
    phase: str,
    outcome: str,
    owner_questions: bool,
    question_consolidation_status: str,
    owner_confirmation_required: bool,
    has_replies: bool,
    active_replies: bool,
    failed_replies: int,
    synthesis_status: str,
    has_synthesis: bool,
    consensus_reached: bool,
    owner_confirmed: bool,
    open_questions: bool,
) -> str:
    """Project work progress independently from operator attention."""
    if question_consolidation_status == "consolidating":
        return "running"
    if question_consolidation_status == "blocked":
        return "blocked"
    if phase == "idle" and outcome == "consensus":
        return "done"
    if phase == "idle" and outcome == "stalled":
        return "blocked"
    if active_replies:
        return "running"
    if failed_replies or synthesis_status == "blocked":
        return "blocked"
    if owner_questions or owner_confirmation_required:
        return "ready"
    if phase == "phase2_relay":
        return "blocked" if open_questions else "ready"
    if phase == "phase3_synthesis":
        if consensus_reached or owner_confirmed:
            return "done"
        if has_synthesis:
            return "ready"
        return "running"
    if phase == "active":
        return "done" if has_replies else "running"
    if phase == "phase1_blind":
        return "running"
    if open_questions:
        return "blocked"
    return "done"


def _next_action(state: str, *, reason: str, has_result: bool) -> str:
    if state == "needs_input":
        return (
            "review_questions"
            if reason == "owner_questions_open"
            else "review_result"
        )
    return {
        "running": "view_activity",
        "ready": "synthesize",
        "blocked": "restart",
        "done": "view_result" if has_result else "none",
    }[state]
