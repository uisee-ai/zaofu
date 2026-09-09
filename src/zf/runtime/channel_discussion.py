"""Channel discussion driver (doc 122): mention_relay + clarification state machine.

Deterministic kernel side of the channel "requirement clarification room":

- relay_route_decision: lets an agent-authored post route to the members it
  @mentions (the only crack in the ``_auto_route_allowed`` gate), guarded by
  G1 causation-depth, G2 ledger-delta bare-ack, and structural rules
  (no self-mention, no @all, roster-only).
- discussion session state machine (idle -> phase1_blind -> phase2_relay ->
  phase3_synthesis -> idle) folded from events; transitions are emitted as
  events so state is replayable, never held in process memory.
- advance_discussion: the idempotent "tick" that emits phase transitions,
  synthesis requests and consensus closure based on the folded projection.

Agents participate through events (``zf emit channel.question.opened`` etc.)
and their reply text; the kernel never parses chat prose for state.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events import EventWriter
from zf.core.state.locks import locked_path
from zf.runtime.channel_projection import project_channel, project_channels
from zf.runtime.channel_contracts import (
    discussion_engine_mode,
    normalize_product_discussion_mode,
)
from zf.runtime.channel_consensus_identity import consensus_reached_payload
from zf.runtime.channel_discussion_contribution import valid_phase1_members
from zf.runtime.channel_synthesis_lock import (
    channel_synthesis_lock_path,
    synthesis_request_in_flight,
)
from zf.runtime.channel_question_dedup import (
    question_ledger,
    question_ledger_digest,
    stable_question_dedup_request_id,
)
from zf.runtime.channel_question_dedup_retry import (
    repair_rejected_question_dedup,
)

RELAY_MODES = {"mention_relay", "fanout_then_synthesis"}
DEFAULT_MAX_RELAY_DEPTH = 4
DISCUSSION_STATES = {"idle", "phase1_blind", "phase2_relay", "phase3_synthesis"}
AGENT_MEMBER_TYPES = {"provider_agent", "persona_agent", "persona"}
QUESTION_RESOLUTIONS = {
    "answered",
    "assumption",
    "out_of_scope",
    "evidence",
}


@dataclass(frozen=True)
class RelayDecision:
    allowed: bool
    targets: list[str] = field(default_factory=list)
    relay_depth: int = 0
    reason: str = ""  # set when suppressed / not applicable


# ---------------------------------------------------------------------------
# config / fold accessors
# ---------------------------------------------------------------------------

def discussion_config(channel: dict[str, Any] | None) -> dict[str, Any]:
    raw = (channel or {}).get("discussion")
    return raw if isinstance(raw, dict) else {}


def max_relay_depth(channel: dict[str, Any] | None) -> int:
    try:
        value = int(discussion_config(channel).get("max_relay_depth") or DEFAULT_MAX_RELAY_DEPTH)
    except (TypeError, ValueError):
        return DEFAULT_MAX_RELAY_DEPTH
    return max(1, min(value, 10))


def discussion_state(channel: dict[str, Any] | None, thread_id: str) -> dict[str, Any]:
    sessions = (channel or {}).get("discussions")
    if isinstance(sessions, dict):
        session = sessions.get(thread_id)
        if isinstance(session, dict):
            return session
    return {"state": "idle"}


def _agent_member_ids(channel: dict[str, Any] | None) -> set[str]:
    ids: set[str] = set()
    for member in (channel or {}).get("members") or []:
        if not isinstance(member, dict):
            continue
        if str(member.get("member_type") or "") in AGENT_MEMBER_TYPES:
            member_id = str(member.get("member_id") or "")
            if member_id:
                ids.add(member_id)
    return ids


def discussion_roster(channel: dict[str, Any] | None) -> list[str]:
    """Participants for the blind fanout: explicit config or role-filtered agents."""
    config = discussion_config(channel)
    explicit = [str(item).strip() for item in config.get("participants") or [] if str(item).strip()]
    if explicit:
        return explicit
    roster: list[str] = []
    for member in (channel or {}).get("members") or []:
        if not isinstance(member, dict):
            continue
        if str(member.get("member_type") or "") not in AGENT_MEMBER_TYPES:
            continue
        if str(member.get("status") or "").lower() in {"removed", "suspended", "rejected", "failed"}:
            continue
        if str(member.get("channel_role") or "").strip().lower() in {"owner_delegate", "observer"}:
            continue
        member_id = str(member.get("member_id") or "")
        if member_id:
            roster.append(member_id)
    return roster


def discussion_synthesizer(channel: dict[str, Any] | None, roster: list[str]) -> str:
    config = discussion_config(channel)
    explicit = str(config.get("synthesizer") or "").strip()
    if explicit:
        return explicit
    for member in (channel or {}).get("members") or []:
        if not isinstance(member, dict):
            continue
        member_id = str(member.get("member_id") or "")
        role = str(member.get("channel_role") or "").strip().lower()
        if member_id in roster and role in {"synthesizer", "product_pm", "facilitator"}:
            return member_id
    return roster[0] if roster else ""


# ---------------------------------------------------------------------------
# G1: causation depth from the projection message/reply chain
# ---------------------------------------------------------------------------

def relay_depth_of(channel: dict[str, Any] | None, message_id: str) -> int:
    """Count consecutive agent-authored hops ending at ``message_id``.

    Walks message -> refs.request_id -> reply_request -> trigger message and
    stops at the first human-authored message. Replayable: derived purely from
    the folded projection, no in-memory counters.
    """
    messages = {
        str(m.get("message_id") or ""): m
        for m in (channel or {}).get("messages") or []
        if isinstance(m, dict)
    }
    requests = {
        str(r.get("request_id") or ""): r
        for r in (channel or {}).get("reply_requests") or []
        if isinstance(r, dict)
    }
    agent_ids = _agent_member_ids(channel)
    depth = 0
    current = messages.get(message_id)
    seen: set[str] = set()
    while isinstance(current, dict):
        current_id = str(current.get("message_id") or "")
        if current_id in seen:
            break
        seen.add(current_id)
        role = str(current.get("role") or "").lower()
        sender = str(current.get("member_id") or "")
        is_agent = role in {"assistant", "agent"} or sender in agent_ids
        if not is_agent:
            break
        depth += 1
        refs = current.get("refs") if isinstance(current.get("refs"), dict) else {}
        request = requests.get(str(refs.get("request_id") or ""))
        if not isinstance(request, dict):
            break
        current = messages.get(str(request.get("message_id") or ""))
    return depth


# ---------------------------------------------------------------------------
# G2: bare-ack via ledger delta (discussion-scene criterion, doc 122 §4.3)
# ---------------------------------------------------------------------------

def _bare_ack_reason(
    channel: dict[str, Any] | None,
    *,
    thread_id: str,
    sender: str,
    targets: list[str],
    message_id: str = "",
) -> str:
    """Suppress the second consecutive A->B relay in an uninterrupted A<->B
    run when the question ledger did not change in between.

    Judged over our own ``channel.relay.routed`` fold (structured, replayable)
    rather than message prose — every prior agent relay went through the
    router, so the fold is the complete relay history.
    """
    if len(targets) != 1:
        return ""
    other = targets[0]
    pair = {sender, other}
    previous_same_direction = None
    for item in reversed((channel or {}).get("relay_events") or []):
        if not isinstance(item, dict) or str(item.get("type") or "") != "channel.relay.routed":
            continue
        if str(item.get("thread_id") or "main") != thread_id:
            continue
        if message_id and str(item.get("message_id") or "") == message_id:
            continue
        member = str(item.get("member_id") or "")
        item_targets = [str(t) for t in item.get("targets") or []]
        if member not in pair or not set(item_targets) <= pair:
            break  # relay run interrupted by someone outside the pair
        if member == sender and other in item_targets:
            previous_same_direction = item
            break
    if previous_same_direction is None:
        return ""
    since_ts = str(previous_same_direction.get("ts") or "")
    for activity in (channel or {}).get("question_activity") or []:
        if not isinstance(activity, dict):
            continue
        if str(activity.get("thread_id") or "main") != thread_id:
            continue
        if str(activity.get("ts") or "") > since_ts:
            return ""
    return "bare_ack"


# ---------------------------------------------------------------------------
# relay decision (router entry point for agent-authored posts)
# ---------------------------------------------------------------------------

def relay_route_decision(
    channel: dict[str, Any] | None,
    *,
    thread_id: str,
    message_id: str,
    sender: str,
    mention_tokens: list[str],
    resolved_targets: list[str],
) -> RelayDecision | None:
    """Decide whether an agent-authored post may relay to its mentions.

    Returns None when relay does not apply at all (mode off) so the router
    falls back to the legacy blocked path. Returns a suppressed decision
    (allowed=False with reason) when relay applies but a guard fires.
    """
    mode = discussion_engine_mode(
        discussion_config(channel).get("mode") or "conversation"
    )
    if mode not in RELAY_MODES:
        return None
    if mode == "fanout_then_synthesis":
        state = str(discussion_state(channel, thread_id).get("state") or "idle")
        if state != "phase2_relay":
            return RelayDecision(allowed=False, reason=f"relay_inactive_{state}")
    if "all" in mention_tokens:
        return RelayDecision(allowed=False, reason="all_disabled")
    targets = [t for t in resolved_targets if t and t != sender]
    if not targets:
        return RelayDecision(allowed=False, reason="no_relay_target")
    depth = relay_depth_of(channel, message_id)
    limit = max_relay_depth(channel)
    if depth >= limit:
        return RelayDecision(allowed=False, targets=targets, relay_depth=depth, reason="depth_exceeded")
    ack = _bare_ack_reason(
        channel, thread_id=thread_id, sender=sender, targets=targets, message_id=message_id,
    )
    if ack:
        return RelayDecision(allowed=False, targets=targets, relay_depth=depth, reason=ack)
    return RelayDecision(allowed=True, targets=targets, relay_depth=depth)


# ---------------------------------------------------------------------------
# discussion start (human @all in fanout_then_synthesis mode)
# ---------------------------------------------------------------------------

def should_start_discussion(
    channel: dict[str, Any] | None,
    *,
    thread_id: str,
    mention_tokens: list[str],
    explicit: bool = False,
    product_mode: str = "",
) -> bool:
    mode = discussion_engine_mode(
        product_mode
        or discussion_config(channel).get("mode")
        or "conversation"
    )
    if mode != "fanout_then_synthesis":
        return False
    if not explicit and "all" not in mention_tokens:
        return False
    if not discussion_roster(channel):
        # No participants -> starting would strand the thread in phase1_blind
        # forever (nobody can complete the blind round). Fall through to normal
        # @all routing instead.
        return False
    return str(discussion_state(channel, thread_id).get("state") or "idle") == "idle"


def start_discussion(
    writer: EventWriter,
    channel: dict[str, Any] | None,
    *,
    actor: str,
    channel_id: str,
    thread_id: str,
    trigger_message_id: str,
    trigger: str,
    source: str,
    causation_id: str | None = None,
    product_mode: str = "",
) -> list[str]:
    """Emit discussion.started + phase1 transition; returns the blind roster."""
    roster = discussion_roster(channel)
    synthesizer = discussion_synthesizer(channel, roster)
    discussion_id = "discussion-" + hashlib.sha1(
        f"{channel_id}:{thread_id}:{trigger_message_id}".encode("utf-8")
    ).hexdigest()[:16]
    context_digest = hashlib.sha256(
        f"{channel_id}:{thread_id}:{trigger_message_id}:{','.join(roster)}".encode(
            "utf-8"
        )
    ).hexdigest()
    started = writer.emit(
        "channel.discussion.started",
        actor=actor,
        causation_id=causation_id,
        correlation_id=channel_id,
        payload={
            "schema_version": "channel.discussion.started.v1",
            "channel_id": channel_id,
            "thread_id": thread_id,
            "discussion_id": discussion_id,
            "revision": 1,
            "context_digest": context_digest,
            "product_mode": normalize_product_discussion_mode(
                product_mode or discussion_config(channel).get("mode")
            ),
            "trigger": trigger,
            "roster": roster,
            "synthesizer": synthesizer,
            "requirement_message_id": trigger_message_id,
            "source": source,
        },
    )
    writer.emit(
        "channel.discussion.phase.changed",
        actor=actor,
        causation_id=started.id,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": thread_id,
            "phase": "phase1_blind",
            "reason": "discussion_started",
            "source": source,
        },
    )
    return roster


# ---------------------------------------------------------------------------
# advance: the idempotent state-machine tick
# ---------------------------------------------------------------------------

def advance_discussion(
    state_dir: Path,
    writer: EventWriter,
    *,
    channel_id: str,
    thread_id: str,
    actor: str = "channel-discussion",
    source: str = "runtime",
    channel: dict[str, Any] | None = None,
    now: datetime | None = None,
    config: Any | None = None,
    project_root: Path | None = None,
) -> list[str]:
    """Emit due transitions for one discussion thread. Returns emitted types.

    Safe to call repeatedly from the router tail and the reactor: every
    transition is guarded by the folded state so replays are no-ops.
    """
    if channel is None:
        channel = project_channel(Path(state_dir), channel_id) or {}
    session = discussion_state(channel, thread_id)
    state = str(session.get("state") or "idle")
    emitted: list[str] = []
    if state == "idle":
        return emitted

    overdue = _phase_overdue(channel, session, now=now)

    if state == "phase1_blind":
        if overdue:
            emitted.extend(_handle_phase1_deadline(
                writer, channel, session,
                actor=actor, source=source,
                channel_id=channel_id, thread_id=thread_id,
            ))
            return emitted
        if _phase1_complete(channel, session, thread_id):
            changed = writer.emit(
                "channel.discussion.phase.changed",
                actor=actor,
                correlation_id=channel_id,
                payload={
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "phase": "phase2_relay",
                    "reason": "blind_round_complete",
                    "source": source,
                },
            )
            emitted.append("channel.discussion.phase.changed")
            ledger = question_ledger(channel, thread_id=thread_id)
            if any(item["status"] == "open" for item in ledger):
                request_id = stable_question_dedup_request_id(
                    channel_id,
                    thread_id,
                    str(session.get("started_event_id") or ""),
                )
                if not _question_dedup_already_requested(
                    channel,
                    request_id,
                ):
                    writer.emit(
                        "channel.question.dedup.requested",
                        actor=actor,
                        task_id=changed.task_id,
                        causation_id=changed.id,
                        correlation_id=channel_id,
                        payload={
                            "schema_version": "channel.question.dedup.v1",
                            "channel_id": channel_id,
                            "thread_id": thread_id,
                            "request_id": request_id,
                            "target_member_id": str(
                                session.get("synthesizer") or ""
                            ),
                            "ledger_digest": question_ledger_digest(
                                channel,
                                thread_id=thread_id,
                            ),
                            "question_count": len(ledger),
                            "generation": 1,
                            "source": source,
                        },
                    )
                    emitted.append("channel.question.dedup.requested")
        return emitted

    if state == "phase2_relay":
        dedup_repair = repair_rejected_question_dedup(
            writer,
            channel,
            session,
            actor=actor,
            source=source,
            channel_id=channel_id,
            thread_id=thread_id,
        )
        if dedup_repair:
            return dedup_repair
        late_blockers = _open_unseen_consensus_blockers(
            writer,
            channel,
            _thread_consensus(channel, thread_id),
            actor=actor,
            source=source,
            channel_id=channel_id,
            thread_id=thread_id,
        )
        if late_blockers:
            return late_blockers
        emitted.extend(_reject_invalid_resolutions(
            writer, channel, actor=actor, source=source,
            channel_id=channel_id, thread_id=thread_id,
        ))
        if _ledger_converged(channel, session, thread_id):
            # The Web action and this tick can both observe the same ledger.
            # Lock the observation-to-append interval to keep one request per
            # synthesis generation.
            with locked_path(
                channel_synthesis_lock_path(state_dir, channel_id, thread_id)
            ):
                current = project_channel(Path(state_dir), channel_id) or channel
                current_session = discussion_state(current, thread_id)
                if (
                    str(current_session.get("state") or "idle")
                    != "phase2_relay"
                    or not _ledger_converged(current, current_session, thread_id)
                ):
                    return emitted
                synthesizer = str(current_session.get("synthesizer") or "")
                request_id = _stable_synthesis_request_id(
                    channel_id,
                    thread_id,
                    str(current_session.get("started_event_id") or ""),
                    generation=_next_synthesis_generation(current, thread_id),
                )
                if not synthesis_request_in_flight(current, thread_id):
                    writer.emit(
                        "channel.synthesis.requested",
                        actor=actor,
                        correlation_id=channel_id,
                        payload={
                            "channel_id": channel_id,
                            "thread_id": thread_id,
                            "request_id": request_id,
                            "target_member_id": synthesizer,
                            "status": "requested",
                            "reason": "ledger_converged",
                            "source": source,
                        },
                    )
                    emitted.append("channel.synthesis.requested")
                writer.emit(
                    "channel.discussion.phase.changed",
                    actor=actor,
                    correlation_id=channel_id,
                    payload={
                        "channel_id": channel_id,
                        "thread_id": thread_id,
                        "phase": "phase3_synthesis",
                        "reason": "ledger_converged",
                        "source": source,
                    },
                )
                emitted.append("channel.discussion.phase.changed")
            return emitted
        if overdue:
            emitted.extend(_close_stalled(
                writer, actor=actor, source=source,
                channel_id=channel_id, thread_id=thread_id, phase=state,
            ))
        return emitted

    if state == "phase3_synthesis":
        consensus = _thread_consensus(channel, thread_id)
        if overdue and not _consensus_reached(channel, session, thread_id, consensus):
            emitted.extend(_close_stalled(
                writer, actor=actor, source=source,
                channel_id=channel_id, thread_id=thread_id, phase=state,
            ))
            return emitted
        blocked = consensus.get("blocked") or []
        emitted.extend(_open_unseen_consensus_blockers(
            writer,
            channel,
            consensus,
            actor=actor,
            source=source,
            channel_id=channel_id,
            thread_id=thread_id,
        ))
        if blocked and not consensus.get("reopened"):
            writer.emit(
                "channel.discussion.phase.changed",
                actor=actor,
                correlation_id=channel_id,
                payload={
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "phase": "phase2_relay",
                    "reason": "consensus_blocked",
                    "source": source,
                },
            )
            emitted.append("channel.discussion.phase.changed")
            return emitted
        if _thread_has_open_questions(channel, thread_id):
            writer.emit(
                "channel.discussion.phase.changed",
                actor=actor,
                correlation_id=channel_id,
                payload={
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "phase": "phase2_relay",
                    "reason": "synthesis_questions_opened",
                    "source": source,
                },
            )
            emitted.append("channel.discussion.phase.changed")
            return emitted
        if _consensus_reached(channel, session, thread_id, consensus):
            artifact_ref = str(consensus.get("artifact_ref") or "")
            if not consensus.get("reached_event_id"):
                reached = writer.emit(
                    "channel.consensus.reached",
                    actor=actor,
                    correlation_id=channel_id,
                    payload=consensus_reached_payload(
                        consensus,
                        channel_id=channel_id,
                        thread_id=thread_id,
                        source=source,
                    ),
                )
                emitted.append("channel.consensus.reached")
            writer.emit(
                "channel.discussion.closed",
                actor=actor,
                correlation_id=channel_id,
                payload={
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "outcome": "consensus",
                    "artifact_ref": artifact_ref,
                    "source": source,
                },
            )
            emitted.append("channel.discussion.closed")
        return emitted

    return emitted


# ---------------------------------------------------------------------------
# advance helpers (pure folds over the projection)
# ---------------------------------------------------------------------------

def _phase1_complete(channel: dict[str, Any], session: dict[str, Any], thread_id: str) -> bool:
    roster = {
        str(member)
        for member in session.get("roster") or []
        if str(member)
    }
    return bool(roster) and roster <= valid_phase1_members(
        channel,
        session,
        thread_id,
    )


def _ledger_converged(channel: dict[str, Any], session: dict[str, Any], thread_id: str) -> bool:
    """All roster members froze their questions and every question is resolved."""
    roster = {str(m) for m in session.get("roster") or []}
    if not roster:
        return False
    frozen = channel.get("questions_frozen") or {}
    frozen_members = set((frozen.get(thread_id) or {}).keys()) if isinstance(frozen, dict) else set()
    if not roster <= frozen_members:
        return False
    for question in channel.get("open_questions") or []:
        if not isinstance(question, dict):
            continue
        if str(question.get("thread_id") or "main") != thread_id:
            continue
        if str(question.get("status") or "") == "open":
            return False
    cross_reviews = channel.get("cross_reviews") or []
    candidates = (
        list(cross_reviews.values())
        if isinstance(cross_reviews, dict)
        else list(cross_reviews)
    )
    if any(
        isinstance(item, dict)
        and str(item.get("thread_id") or "main") == thread_id
        and str(item.get("status") or "") != "completed"
        for item in candidates
    ):
        return False
    # Zero questions with everyone frozen is legitimate convergence: nothing
    # was unclear. The freeze set is the gate, not the question count.
    return True


def _thread_consensus(channel: dict[str, Any], thread_id: str) -> dict[str, Any]:
    consensus = channel.get("consensus")
    if isinstance(consensus, dict):
        item = consensus.get(thread_id)
        if isinstance(item, dict):
            return item
    return {}


def _open_unseen_consensus_blockers(
    writer: EventWriter,
    channel: dict[str, Any],
    consensus: dict[str, Any],
    *,
    actor: str,
    source: str,
    channel_id: str,
    thread_id: str,
) -> list[str]:
    emitted: list[str] = []
    for item in consensus.get("blocked") or []:
        question_id = str(item.get("blocker_question_id") or "")
        if not question_id or _question_exists(channel, question_id):
            continue
        writer.emit(
            "channel.question.opened",
            actor=actor,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "question_id": question_id,
                "question": str(item.get("blocker_question") or ""),
                "category": "blocker",
                "asked_by": str(item.get("member_id") or ""),
                "source": source,
            },
        )
        emitted.append("channel.question.opened")
    return emitted


def _consensus_reached(
    channel: dict[str, Any],
    session: dict[str, Any],
    thread_id: str,
    consensus: dict[str, Any],
) -> bool:
    if not consensus.get("artifact_ref"):
        return False
    required_signers = {
        str(member_id)
        for member_id in consensus.get("required_signers") or []
        if str(member_id)
    }
    roster = required_signers or {
        str(m) for m in session.get("roster") or []
    }
    signed = set((consensus.get("signed") or {}).keys())
    if not roster or not roster <= signed:
        return False
    return bool(consensus.get("human_confirmed"))


def _question_exists(channel: dict[str, Any], question_id: str) -> bool:
    for question in channel.get("open_questions") or []:
        if isinstance(question, dict) and str(question.get("question_id") or "") == question_id:
            return True
    return False


def _question_dedup_already_requested(
    channel: dict[str, Any],
    request_id: str,
) -> bool:
    return any(
        isinstance(item, dict)
        and str(item.get("request_id") or "") == request_id
        for item in channel.get("question_dedup_requests") or []
    )


def _next_synthesis_generation(channel: dict[str, Any], thread_id: str) -> int:
    prior = sum(
        1
        for item in channel.get("synthesis_requests") or []
        if isinstance(item, dict)
        and str(item.get("thread_id") or "main") == thread_id
    )
    return prior + 1


def _thread_has_open_questions(
    channel: dict[str, Any],
    thread_id: str,
) -> bool:
    return any(
        isinstance(question, dict)
        and str(question.get("thread_id") or "main") == thread_id
        and str(question.get("status") or "") == "open"
        for question in channel.get("open_questions") or []
    )


def _reject_invalid_resolutions(
    writer: EventWriter,
    channel: dict[str, Any],
    *,
    actor: str,
    source: str,
    channel_id: str,
    thread_id: str,
) -> list[str]:
    """Emit one rejection event per agent-authored `answered` attempt."""
    emitted: list[str] = []
    already = {
        str(item.get("attempt_event_id") or "")
        for item in channel.get("question_resolve_rejections") or []
        if isinstance(item, dict)
    }
    for attempt in channel.get("rejected_resolutions") or []:
        if not isinstance(attempt, dict):
            continue
        if str(attempt.get("thread_id") or "main") != thread_id:
            continue
        attempt_id = str(attempt.get("event_id") or "")
        if not attempt_id or attempt_id in already:
            continue
        writer.emit(
            "channel.question.resolve.rejected",
            actor=actor,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "question_id": str(attempt.get("question_id") or ""),
                "attempt_event_id": attempt_id,
                "reason": str(
                    attempt.get("reason") or "answered_requires_human"
                ),
                "source": source,
            },
        )
        emitted.append("channel.question.resolve.rejected")
    return emitted


def _stable_synthesis_request_id(
    channel_id: str,
    thread_id: str,
    started_event_id: str,
    *,
    generation: int,
) -> str:
    digest = hashlib.sha1(
        (
            f"synthesis:{channel_id}:{thread_id}:{started_event_id}:"
            f"generation:{generation}"
        ).encode("utf-8"),
    ).hexdigest()[:16]
    return f"synth-{digest}"


# ---------------------------------------------------------------------------
# P1-1: phase deadlines (doc 122 §12-6/8)
# ---------------------------------------------------------------------------

def _phase_deadline_seconds(channel: dict[str, Any] | None, phase: str) -> int:
    raw = discussion_config(channel).get("phase_deadline_seconds")
    if not isinstance(raw, dict):
        return 0
    try:
        return max(0, int(raw.get(phase) or 0))
    except (TypeError, ValueError):
        return 0


def _phase_overdue(
    channel: dict[str, Any] | None,
    session: dict[str, Any],
    *,
    now: datetime | None,
) -> bool:
    phase = str(session.get("state") or "idle")
    deadline = _phase_deadline_seconds(channel, phase)
    if deadline <= 0:
        return False
    anchor = str(session.get("phase_changed_at") or session.get("started_at") or "")
    if not anchor:
        return False
    try:
        anchored = datetime.fromisoformat(anchor)
    except ValueError:
        return False
    current = now or datetime.now(timezone.utc)
    return (current - anchored).total_seconds() > deadline


def _handle_phase1_deadline(
    writer: EventWriter,
    channel: dict[str, Any],
    session: dict[str, Any],
    *,
    actor: str,
    source: str,
    channel_id: str,
    thread_id: str,
) -> list[str]:
    """Quorum (>=2/3 replied) degrades into phase2; below quorum stalls."""
    emitted: list[str] = []
    roster = [str(m) for m in session.get("roster") or []]
    replied = valid_phase1_members(channel, session, thread_id)
    quorum = math.ceil(len(roster) * 2 / 3) if roster else 0
    responders = [m for m in roster if m in replied]
    if roster and len(responders) >= quorum:
        for member in roster:
            if member not in replied:
                writer.emit(
                    "channel.discussion.participant.missed",
                    actor=actor,
                    correlation_id=channel_id,
                    payload={
                        "channel_id": channel_id,
                        "thread_id": thread_id,
                        "member_id": member,
                        "phase": "phase1_blind",
                        "source": source,
                    },
                )
                emitted.append("channel.discussion.participant.missed")
        writer.emit(
            "channel.discussion.phase.changed",
            actor=actor,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "phase": "phase2_relay",
                "reason": "quorum_after_deadline",
                "source": source,
            },
        )
        emitted.append("channel.discussion.phase.changed")
        return emitted
    emitted.extend(_close_stalled(
        writer, actor=actor, source=source,
        channel_id=channel_id, thread_id=thread_id, phase="phase1_blind",
    ))
    return emitted


def _close_stalled(
    writer: EventWriter,
    *,
    actor: str,
    source: str,
    channel_id: str,
    thread_id: str,
    phase: str,
) -> list[str]:
    writer.emit(
        "channel.discussion.closed",
        actor=actor,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": thread_id,
            "outcome": "stalled",
            "stalled_phase": phase,
            "source": source,
        },
    )
    return ["channel.discussion.closed"]


def sweep_discussion_deadlines(
    state_dir: Path,
    writer: EventWriter,
    *,
    now: datetime | None = None,
    config: Any | None = None,
    project_root: Path | None = None,
) -> int:
    """Tick every active discussion (deadline enforcement for silent threads).

    Called from the orchestrator tick services; without it a thread whose
    agents went silent would never see its deadline fire (advance only runs
    on inbound events otherwise)."""
    ticked = 0
    listing = project_channels(Path(state_dir))
    for item in listing.get("channels") or listing.get("items") or []:
        if not isinstance(item, dict):
            continue
        channel_id = str(item.get("channel_id") or "")
        sessions = item.get("discussions")
        if not channel_id or not isinstance(sessions, dict):
            continue
        for thread_id, session in sessions.items():
            if not isinstance(session, dict):
                continue
            if str(session.get("state") or "idle") == "idle":
                continue
            advance_discussion(
                Path(state_dir),
                writer,
                channel_id=channel_id,
                thread_id=str(thread_id),
                source="deadline-sweep",
                now=now,
                config=config,
                project_root=project_root,
            )
            ticked += 1
    return ticked
