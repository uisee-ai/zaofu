"""Read-only operator attention derived from Channel discussion facts."""

from __future__ import annotations

from typing import Any

from zf.runtime.channel_contracts import discussion_engine_mode
from zf.runtime.channel_question_gate import question_dedup_gate_state
from zf.runtime.channel_member_proposals import pending_member_proposals
from zf.runtime.channel_synthesis_lock import retired_synthesis_reply_ids
from zf.runtime.channel_synthesis_routing import retired_synthesis_message_ids


_QUEUED_REPLY_STATUSES = {"pending", "queued"}
_RUNNING_REPLY_STATUSES = {"running", "started"}
_ACTIVE_REPLY_STATUSES = _RUNNING_REPLY_STATUSES
_WORK_REPLY_STATUSES = _QUEUED_REPLY_STATUSES | _RUNNING_REPLY_STATUSES
_FAILED_REPLY_STATUSES = {"failed", "rejected", "escalated"}
_COVERAGE_INCOMPLETE_STATUSES = {
    "pending",
    "queued",
    "running",
    "responded",
    "failed",
}


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
        session_started_at = str(session.get("continued_at") or session.get("started_at") or "")

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

        retired_replies = retired_synthesis_reply_ids(channel, thread_id)
        replies = [
            item for item in reply_requests
            if current_session_item(
                item,
                time_keys=("created_at", "updated_at", "ts"),
            ) and str(item.get("request_id") or "") not in retired_replies
        ]
        role_coverage = _discussion_role_coverage(
            channel,
            session,
            replies,
            thread_id=thread_id,
        )
        coverage_required = bool(role_coverage["coverage_required"])
        coverage_complete = bool(role_coverage["coverage_complete"])
        incomplete_replies = [
            item for item in replies
            if _reply_is_incomplete(item)
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
        work_replies = [
            item for item in replies
            if str(item.get("status") or "") in _WORK_REPLY_STATUSES
        ]
        active_replies = [
            item for item in replies
            if str(item.get("status") or "") in _ACTIVE_REPLY_STATUSES
        ]
        queued_replies = [
            item for item in replies
            if str(item.get("status") or "") in _QUEUED_REPLY_STATUSES
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
            and not consensus.get("blocked")
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
            running_replies=bool(active_replies),
            queued_replies=bool(queued_replies),
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
            running_replies=bool(active_replies),
            queued_replies=bool(queued_replies),
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
        invitation_count = len(pending_member_proposals(channel, thread_id))
        packs = channel.get("context_packs") or {}
        retired_messages = retired_synthesis_message_ids(channel, thread_id)
        context_rejections = [
            {key: row.get(key, "") for key in ("context_pack_id", "target_member_id", "trigger_message_id", "reason", "ts")}
            for row in (packs.values() if isinstance(packs, dict) else packs)
            if row.get("status") == "rejected" and current_session_item(row, time_keys=("ts",))
            and row.get("trigger_message_id") not in retired_messages
        ]
        if invitation_count:
            state, reason, next_action = "needs_input", "member_invitation_pending", "review_members"
        if context_rejections:
            state, reason, next_action = "blocked", "context_pack_rejected", "diagnose_attention"
            execution_state = "running" if active_replies else "blocked"
        retry_synthesis = bool(
            phase == "phase3_synthesis" and synthesis_requests
            and latest_synthesis_status == "failed"
            and synthesis_requests[-1].get("recovery_reply_id")
            and not work_replies and not open_questions and not visible_owner_questions
            and question_consolidation_status == "open"
            and not invitation_count and not context_rejections
            and (not coverage_required or coverage_complete)
            and not any(str(r.get("status") or "") in _FAILED_REPLY_STATUSES
                        and r.get("request_id") != synthesis_requests[-1].get("recovery_reply_id") for r in replies)
        )
        if retry_synthesis:
            next_action = "synthesize"
        if coverage_required and not coverage_complete and state == "ready":
            next_action = "supplement_roles"
        frozen = (channel.get("questions_frozen") or {}).get(thread_id) or {}
        missing_frozen_members = sorted(set(session.get("roster") or []) - set(frozen))
        contribution_missing = bool(
            phase == "phase2_relay" and missing_frozen_members
            and not work_replies and not open_questions
            and not invitation_count and not context_rejections
        )
        if contribution_missing:
            state, reason, next_action = "blocked", "contribution_missing", "restart_discussion"
            execution_state = "blocked"
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
            "context_rejections": context_rejections,
            "missing_frozen_members": missing_frozen_members,
            "deferred_question_count": sum(item.get("resolution") == "deferred" for item in questions),
            "open_question_count": len(open_questions),
            "owner_question_count": len(visible_owner_questions),
            "total_question_count": len(questions),
            "resolved_question_count": sum(item.get("status") == "resolved" for item in questions),
            "pending_member_invitation_count": invitation_count,
            "merged_question_count": sum(item.get("status") == "merged" for item in questions),
            "question_consolidation_status": question_consolidation_status,
            "last_activity_at": max(
                (item for item in activity_times if item),
                default="",
            ),
            "can_drain_replies": bool(work_replies),
            # A default conversation deliberately becomes quiet after its
            # reply cycle settles. Quiet must not remove the explicit
            # Finalize/Synthesize path: the operator may turn complete,
            # question-free discussion into a PRD from Details.
            "can_synthesize": (
                (
                    state == "ready" or retry_synthesis
                    or (
                        phase == "active"
                        and bool(replies)
                        and not work_replies
                        and not failed_count
                        and not visible_owner_questions
                        and not open_questions
                        and not latest_synthesis_status
                        and not syntheses
                        and not consensus
                    )
                )
                and (not coverage_required or coverage_complete)
                and not invitation_count
                and not context_rejections
                and not contribution_missing
            ),
            "coverage_required": coverage_required,
            "coverage_complete": coverage_complete,
            "coverage_summary": role_coverage["coverage_summary"],
            "coverage_missing": role_coverage["coverage_missing"],
            "coverage_failed": role_coverage["coverage_failed"],
            "role_coverage": role_coverage["role_coverage"],
            "discussion_contract": role_coverage["discussion_contract"],
            "answer_integrity": {
                "incomplete_reply_count": len(incomplete_replies),
                "incomplete_reply_request_ids": [
                    str(item.get("request_id") or "")
                    for item in incomplete_replies
                    if str(item.get("request_id") or "")
                ],
                "full_body_source": "channel.message.body_ref when present; conversation projection hydrates it",
            },
            "can_restart": state == "blocked" and not retry_synthesis,
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


def project_discussion_evaluation_summary(
    channel: dict[str, Any],
    discussion_attention: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build a stable, read-only comparison contract for Channel evaluations."""

    sessions = channel.get("discussions")
    sessions = sessions if isinstance(sessions, dict) else {}
    contributions = channel.get("contributions")
    contribution_rows = (
        list(contributions.values())
        if isinstance(contributions, dict)
        else list(contributions or [])
    )
    questions = channel.get("open_questions")
    question_rows = (
        list(questions.values())
        if isinstance(questions, dict)
        else list(questions or [])
    )
    thread_ids = set(sessions) | set(discussion_attention)
    if not thread_ids:
        thread_ids.add("main")

    def compact_findings(thread_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        p0: list[dict[str, Any]] = []
        p1: list[dict[str, Any]] = []
        for contribution in contribution_rows:
            if not isinstance(contribution, dict):
                continue
            if str(contribution.get("thread_id") or "main") != thread_id:
                continue
            member_id = str(contribution.get("member_id") or "")
            evidence_refs = list(contribution.get("evidence_refs") or [])
            for kind in ("findings", "contradictions", "risks"):
                for finding in contribution.get(kind) or []:
                    if isinstance(finding, dict):
                        severity = str(
                            finding.get("severity")
                            or finding.get("priority")
                            or ""
                        ).strip().lower()
                        summary = str(
                            finding.get("summary")
                            or finding.get("finding")
                            or finding.get("title")
                            or finding.get("description")
                            or finding.get("detail")
                            or finding.get("statement")
                            or finding.get("scenario")
                            or finding.get("text")
                            or ""
                        )
                    else:
                        severity = ""
                        summary = str(finding or "").strip()
                    row = {
                        "member_id": member_id,
                        "kind": kind,
                        "severity": severity,
                        "summary": summary,
                        "evidence_refs": evidence_refs,
                    }
                    if severity in {"p0", "critical", "blocker"}:
                        p0.append(row)
                    elif severity in {"p1", "high", "major"}:
                        p1.append(row)
        return p0, p1

    thread_summaries: dict[str, dict[str, Any]] = {}
    source_messages = channel.get("messages")
    source_messages = (
        list(source_messages.values())
        if isinstance(source_messages, dict)
        else list(source_messages or [])
    )
    source_message = next(
        (
            item for item in source_messages
            if isinstance(item, dict)
            and str(item.get("role") or "") == "user"
            and item.get("body_sha256")
        ),
        {},
    )
    member_rows = channel.get("members")
    member_rows = (
        list(member_rows.values())
        if isinstance(member_rows, dict)
        else list(member_rows or [])
    )
    provider_backends = sorted({
        str(member.get("backend") or member.get("provider") or "")
        for member in member_rows
        if isinstance(member, dict)
        and str(member.get("backend") or member.get("provider") or "")
    })
    dispatch_policy = str(
        (source_message.get("refs") or {}).get("dispatch_policy")
        if isinstance(source_message.get("refs"), dict)
        else ""
    )
    for thread_id in sorted(str(item) for item in thread_ids):
        attention = discussion_attention.get(thread_id) or {}
        session = sessions.get(thread_id) if isinstance(sessions.get(thread_id), dict) else {}
        contract = attention.get("discussion_contract") if isinstance(
            attention.get("discussion_contract"), dict
        ) else {}
        p0_findings, p1_findings = compact_findings(thread_id)
        open_questions = [
            {
                "question_id": str(item.get("question_id") or item.get("id") or ""),
                "summary": str(item.get("question") or item.get("summary") or ""),
                "target_member_id": str(item.get("target_member_id") or ""),
            }
            for item in question_rows
            if isinstance(item, dict)
            and str(item.get("thread_id") or "main") == thread_id
            and str(item.get("status") or "open") == "open"
        ]
        thread_summaries[thread_id] = {
            "thread_id": thread_id,
            "mode": str(contract.get("mode") or session.get("product_mode") or "conversation"),
            "engine_mode": str(contract.get("engine_mode") or ""),
            "required_roles": list(contract.get("required_participants") or session.get("roster") or []),
            "role_coverage": list(attention.get("role_coverage") or []),
            "coverage_summary": attention.get("coverage_summary") or {},
            "p0_findings": p0_findings,
            "p1_findings": p1_findings,
            "open_questions": open_questions,
            "evidence_refs": sorted({
                str(ref)
                for row in [*p0_findings, *p1_findings]
                for ref in row.get("evidence_refs") or []
                if str(ref)
            }),
            "answer_integrity": attention.get("answer_integrity") or {},
            "synthesis_ready": bool(attention.get("can_synthesize")),
            "result_status": (
                "done" if attention.get("state") == "done"
                else "blocked" if attention.get("state") == "blocked"
                else "ready" if attention.get("can_synthesize")
                else "in_progress"
            ),
        }
    current = thread_summaries.get("main") or next(iter(thread_summaries.values()))
    return {
        "schema_version": "channel.evaluation-summary.v1",
        "is_derived_projection": True,
        "current_thread_id": str(current.get("thread_id") or "main"),
        "provider_backends": provider_backends,
        "dispatch_policy": dispatch_policy,
        "source_body_sha256": str(source_message.get("body_sha256") or ""),
        "source_body_byte_count": int(source_message.get("body_byte_count") or 0),
        "threads": thread_summaries,
        **current,
    }


def _reply_is_incomplete(item: dict[str, Any]) -> bool:
    """Recognize provider truncation/incomplete signals without parsing prose."""
    status = str(item.get("status") or "").strip().lower()
    marker = " ".join(
        str(item.get(key) or "").strip().lower()
        for key in ("completion_reason", "reason")
    )
    return (
        status == "incomplete"
        or any(
            token in marker
            for token in (
                "incomplete",
                "max_tokens",
                "context_limit",
                "length_limit",
                "truncated",
            )
        )
    )


def _discussion_role_coverage(
    channel: dict[str, Any],
    session: dict[str, Any],
    replies: list[dict[str, Any]],
    *,
    thread_id: str,
) -> dict[str, Any]:
    """Project the role-level completion contract for one discussion.

    This is deliberately derived from reply and structured-contribution facts.
    It tells the UI whether a role has supplied a usable contribution without
    making semantic decisions or dispatching another agent.
    """
    roster = [
        str(member_id).strip()
        for member_id in session.get("roster") or []
        if str(member_id).strip()
    ]
    discussion = channel.get("discussion")
    discussion = discussion if isinstance(discussion, dict) else {}
    raw_mode = str(
        session.get("product_mode")
        or discussion.get("mode")
        or "conversation"
    )
    try:
        engine_mode = discussion_engine_mode(raw_mode)
    except (TypeError, ValueError):
        engine_mode = "manual_mention"
    coverage_required = bool(roster) and engine_mode == "fanout_then_synthesis"
    optional_ids = {
        str(member_id).strip()
        for member_id in discussion.get("optional_participants") or []
        if str(member_id).strip()
    }
    members = channel.get("members")
    member_map = members if isinstance(members, dict) else {
        str(item.get("member_id") or ""): item
        for item in members or []
        if isinstance(item, dict) and str(item.get("member_id") or "")
    }
    contributions = channel.get("contributions")
    contribution_rows = (
        list(contributions.values())
        if isinstance(contributions, dict)
        else list(contributions or [])
    )
    trigger_id = str(
        session.get("adaptive_next_round_message_id")
        or session.get("requirement_message_id")
        or ""
    )

    def target_id(item: dict[str, Any]) -> str:
        return str(
            item.get("target_member_id")
            or item.get("member_id")
            or ""
        ).strip()

    def row_time(item: dict[str, Any]) -> str:
        return str(
            item.get("updated_at")
            or item.get("ended_at")
            or item.get("created_at")
            or item.get("ts")
            or ""
        )

    coverage_rows: list[dict[str, Any]] = []
    for member_id in roster:
        candidates = [
            item for item in replies
            if target_id(item) == member_id
            and (
                not trigger_id
                or not str(item.get("message_id") or "")
                or str(item.get("message_id") or "") == trigger_id
            )
        ]
        latest = max(candidates, key=row_time, default=None)
        contribution = None
        for item in contribution_rows:
            if not isinstance(item, dict):
                continue
            if str(item.get("thread_id") or "main") != thread_id:
                continue
            if str(item.get("member_id") or "") != member_id:
                continue
            if str(item.get("contract_status") or "") != "structured":
                continue
            if trigger_id and str(item.get("message_id") or "") not in {"", trigger_id}:
                continue
            if latest is not None:
                request_id = str(latest.get("request_id") or "")
                contribution_request_id = str(item.get("request_id") or "")
                if request_id and contribution_request_id and request_id != contribution_request_id:
                    continue
            if contribution is None or row_time(item) >= row_time(contribution):
                contribution = item

        status = "pending"
        reason = "no reply requested"
        if contribution is not None:
            status = "completed"
            reason = "structured contribution available"
        elif latest is not None:
            reply_status = str(latest.get("status") or "")
            if reply_status in _FAILED_REPLY_STATUSES:
                status = "failed"
                reason = str(latest.get("reason") or "reply failed")
            elif reply_status in _RUNNING_REPLY_STATUSES:
                status = "running"
                reason = "reply in progress"
            elif reply_status in _QUEUED_REPLY_STATUSES:
                status = "queued"
                reason = "reply queued"
            elif reply_status == "completed":
                status = "responded"
                reason = "reply completed without structured contribution"
            else:
                reason = str(latest.get("reason") or "reply not completed")
        optional = member_id in optional_ids
        if optional and status == "pending":
            status = "skipped"
            reason = "optional participant"
        member = member_map.get(member_id)
        member = member if isinstance(member, dict) else {}
        coverage_rows.append({
            "member_id": member_id,
            "channel_role": str(
                member.get("channel_role")
                or member.get("role")
                or "member"
            ),
            "display_name": str(
                member.get("display_name")
                or member.get("persona")
                or member_id
            ),
            "required": not optional,
            "status": status,
            "reply_request_id": str(
                (latest or {}).get("request_id") or ""
            ),
            "contribution_event_id": str(
                (contribution or {}).get("event_id") or ""
            ),
            "reason": reason,
        })

    required_rows = [row for row in coverage_rows if row["required"]]
    counts = {
        status: sum(row["status"] == status for row in coverage_rows)
        for status in (
            "pending",
            "queued",
            "running",
            "responded",
            "completed",
            "failed",
            "skipped",
        )
    }
    required_counts = {
        status: sum(
            row["required"] and row["status"] == status
            for row in coverage_rows
        )
        for status in counts
    }
    complete = all(
        row["status"] == "completed"
        for row in required_rows
    )
    missing = [
        row["member_id"]
        for row in required_rows
        if row["status"] in _COVERAGE_INCOMPLETE_STATUSES
    ]
    failed = [
        row["member_id"]
        for row in required_rows
        if row["status"] == "failed"
    ]
    synthesizer = str(session.get("synthesizer") or "")
    return {
        "coverage_required": coverage_required,
        "coverage_complete": complete,
        "coverage_summary": {
            "required": len(required_rows),
            "total": len(coverage_rows),
            "completed": counts["completed"],
            "required_completed": required_counts["completed"],
            "responded": counts["responded"],
            "pending": counts["pending"],
            "queued": counts["queued"],
            "running": counts["running"],
            "failed": counts["failed"],
            "skipped": counts["skipped"],
        },
        "coverage_missing": missing,
        "coverage_failed": failed,
        "role_coverage": coverage_rows,
        "discussion_contract": {
            "mode": raw_mode,
            "engine_mode": engine_mode,
            "required_participants": [
                row["member_id"] for row in required_rows
            ],
            "optional_participants": [
                row["member_id"] for row in coverage_rows
                if not row["required"]
            ],
            "synthesizer": synthesizer,
            "stop_conditions": [
                "required_roles_complete",
                "open_questions_resolved",
                "synthesis_reviewed",
            ],
        },
    }


def _attention_state(
    *,
    phase: str,
    outcome: str,
    owner_questions: bool,
    question_consolidation_status: str,
    owner_confirmation_required: bool,
    has_replies: bool,
    running_replies: bool,
    queued_replies: bool,
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
    if running_replies:
        return "running", "replies_active"
    if queued_replies:
        return "running", "replies_queued"
    if synthesis_status == "stale_ignored":
        return "blocked", "synthesis_superseded"
    if failed_replies or synthesis_status in {"blocked", "stale_ignored"}:
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
        if consensus_reached:
            return "done", "discussion_completed"
        if synthesis_status in {"requested", "repair_requested"}:
            return "running", "synthesis_active"
        if open_questions:
            return "blocked", "unresolved_questions"
        return "blocked", "synthesis_missing"
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
    running_replies: bool,
    queued_replies: bool,
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
    if running_replies:
        return "running"
    if queued_replies:
        return "queued"
    if failed_replies or synthesis_status in {"blocked", "stale_ignored"}:
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
        return "running" if synthesis_status in {"requested", "repair_requested"} else "blocked"
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
