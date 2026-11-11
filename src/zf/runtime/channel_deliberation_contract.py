"""Typed reply contracts for Channel deliberation and sign-off."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from zf.core.events import EventWriter
from zf.runtime.channel_contract_artifacts import (
    persist_channel_contract,
    persist_channel_semantic_coverage,
    string_refs,
    typed_items,
    validate_channel_contract,
)
from zf.runtime.channel_semantic_sources import (
    validate_semantic_source_coverage,
)
from zf.runtime.channel_question_graph import (
    normalize_question_payload,
    question_text_identity,
    validate_question_graph,
)
from zf.runtime.channel_cross_review_outcomes import can_retry_rejected_review, cross_review_outcome_error, settle_cross_review_question


def apply_cross_review_reply(
    *,
    state_dir: Path,
    writer: EventWriter,
    channel: dict[str, Any],
    channel_id: str,
    thread_id: str,
    member_id: str,
    request: dict[str, Any],
    refs: dict[str, Any],
    review: dict[str, Any],
    reply_event_id: str,
    actor: str,
    source: str,
) -> None:
    request_id = str(refs.get("cross_review_request_id") or "")
    expected = _cross_review_by_id(channel, request_id)
    expected_target = str(expected.get("target_member_id") or "")
    expected_question = str(expected.get("question_id") or "")
    if not expected:
        _emit_cross_review_rejected(
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            request_id=request_id,
            member_id=member_id,
            reply_event_id=reply_event_id,
            task_id=str(request.get("task_id") or "") or None,
            source=source,
            reason="unknown_cross_review_request",
        )
        return
    if expected_target != member_id:
        _emit_cross_review_rejected(
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            request_id=request_id,
            member_id=member_id,
            reply_event_id=reply_event_id,
            task_id=str(request.get("task_id") or "") or None,
            source=source,
            reason="cross_review_target_mismatch",
        )
        return
    if str(refs.get("question_id") or "") != expected_question:
        _emit_cross_review_rejected(
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            request_id=request_id,
            member_id=member_id,
            reply_event_id=reply_event_id,
            task_id=str(request.get("task_id") or "") or None,
            source=source,
            reason="cross_review_question_mismatch",
        )
        return
    status = str(expected.get("status") or "")
    if status == "completed":
        return
    retrying = status == "rejected" and can_retry_rejected_review(
        writer.event_log.read_all(), expected, request, channel_id,
    )
    if status == "rejected" and not retrying:
        return  # Preserve the existing rejection until a current bounded repair.
    if status not in {"requested", "pending"} and not retrying:
        _emit_cross_review_rejected(
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            request_id=request_id,
            member_id=member_id,
            reply_event_id=reply_event_id,
            task_id=str(request.get("task_id") or "") or None,
            source=source,
            reason=f"cross_review_not_pending:{status}",
        )
        return
    if not review:
        _emit_cross_review_rejected(
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            request_id=request_id,
            member_id=member_id,
            reply_event_id=reply_event_id,
            task_id=str(request.get("task_id") or "") or None,
            source=source,
            reason="invalid_missing_channel_cross_review",
        )
        return
    review_body = {
        "outcome": review.get("outcome", "resolved"),
        "owner_question": str(review.get("owner_question") or "").strip(),
        "recommended_answer": str(review.get("recommended_answer") or "").strip(),
        "summary": str(review.get("summary") or "").strip(),
        "answer": str(review.get("answer") or "").strip(),
        "findings": typed_items(review.get("findings")),
        "contradictions": typed_items(review.get("contradictions")),
        "risks": typed_items(review.get("risks")),
        "questions": [],
        "source_refs": string_refs(review.get("source_refs")),
        "evidence_refs": string_refs(review.get("evidence_refs")),
        "consumed_message_digests": string_refs(
            review.get("consumed_message_digests")
        ),
        "freeze": True,
    }
    validation_error = cross_review_outcome_error(review, required=refs.get("cross_review_contract") == "v2") or validate_channel_contract(
        review_body,
        kind="contribution",
    )
    if validation_error:
        _emit_cross_review_rejected(
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            request_id=request_id,
            member_id=member_id,
            reply_event_id=reply_event_id,
            task_id=str(request.get("task_id") or "") or None,
            source=source,
            reason=validation_error,
        )
        return
    coverage, coverage_error = validate_semantic_source_coverage(
        channel,
        request,
        review_body["consumed_message_digests"],
    )
    if coverage_error:
        _emit_cross_review_rejected(
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            request_id=request_id,
            member_id=member_id,
            reply_event_id=reply_event_id,
            task_id=str(request.get("task_id") or "") or None,
            source=source,
            reason=coverage_error,
        )
        return
    descriptor = persist_channel_contract(
        state_dir,
        channel_id=channel_id,
        thread_id=thread_id,
        identity=request_id,
        kind="contribution",
        body=review_body,
        created_by=member_id or actor,
        source_event_id=reply_event_id,
        provenance={
            "source_refs": review_body["source_refs"],
            "evidence_refs": review_body["evidence_refs"],
            "cross_review_request_id": request_id,
            "semantic_source_manifest_digest": coverage[
                "manifest_digest"
            ],
        },
    )
    coverage_descriptor = (
        persist_channel_semantic_coverage(
            state_dir,
            channel_id=channel_id,
            thread_id=thread_id,
            identity=request_id,
            coverage=coverage,
            created_by=member_id or actor,
            source_event_id=reply_event_id,
        )
        if coverage["required"]
        else {}
    )
    completed = writer.emit(
        "channel.cross_review.completed",
        actor=member_id or actor,
        task_id=str(request.get("task_id") or "") or None,
        causation_id=reply_event_id,
        correlation_id=channel_id,
        payload={
            "schema_version": "channel.cross_review.v1",
            "channel_id": channel_id,
            "thread_id": thread_id,
            "request_id": request_id,
            "question_id": str(refs.get("question_id") or ""),
            "target_member_id": member_id,
            "summary": review_body["summary"],
            "answer": review_body["answer"],
            "outcome": review_body["outcome"],
            "owner_question": review_body["owner_question"],
            "recommended_answer": review_body["recommended_answer"],
            "source_refs": review_body["source_refs"],
            "evidence_refs": review_body["evidence_refs"],
            "artifact_ref": descriptor["ref"],
            "artifact_digest": descriptor["sha256"],
            "consumed_message_digests": coverage[
                "consumed_message_digests"
            ],
            "source_coverage": coverage["sources"],
            "semantic_source_manifest_digest": coverage[
                "manifest_digest"
            ],
            "semantic_coverage_ref": str(
                coverage_descriptor.get("ref") or ""
            ),
            "semantic_coverage_digest": str(
                coverage_descriptor.get("sha256") or ""
            ),
            "source": source,
        },
    )
    writer.emit(
        "channel.finding.recorded",
        actor=member_id or actor,
        task_id=str(request.get("task_id") or "") or None,
        causation_id=completed.id,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": thread_id,
            "member_id": member_id,
            "summary": review_body["summary"],
            "findings": review_body["findings"],
            "contradictions": review_body["contradictions"],
            "risks": review_body["risks"],
            "source_refs": review_body["source_refs"],
            "evidence_refs": review_body["evidence_refs"],
            "artifact_ref": descriptor["ref"],
            "artifact_digest": descriptor["sha256"],
            "consumed_message_digests": coverage[
                "consumed_message_digests"
            ],
            "semantic_coverage_ref": str(
                coverage_descriptor.get("ref") or ""
            ),
            "semantic_coverage_digest": str(
                coverage_descriptor.get("sha256") or ""
            ),
            "contract_status": "cross_review",
            "source": source,
        },
    )
    settle_cross_review_question(state_dir=state_dir, writer=writer, channel_id=channel_id,
                                 thread_id=thread_id, request_id=request_id,
                                 question_id=str(refs.get("question_id") or ""),
                                 completed_event_id=completed.id, source=source)


def apply_consensus_review_reply(
    *,
    state_dir: Path,
    writer: EventWriter,
    channel: dict[str, Any],
    channel_id: str,
    thread_id: str,
    member_id: str,
    request: dict[str, Any],
    refs: dict[str, Any],
    review: dict[str, Any],
    reply_event_id: str,
    actor: str,
    source: str,
) -> None:
    review_id = str(refs.get("consensus_review_id") or "")
    expected_digest = str(refs.get("artifact_digest") or "")
    consensus = _thread_consensus(channel, thread_id)
    current_digest = str(consensus.get("artifact_digest") or "")
    required_signers = {
        str(item)
        for item in consensus.get("required_signers") or []
        if str(item)
    }
    signed = consensus.get("signed")
    blocked = consensus.get("blocked")
    already_settled = (
        isinstance(signed, dict) and member_id in signed
    ) or any(
        isinstance(item, dict)
        and str(item.get("member_id") or "") == member_id
        for item in (blocked if isinstance(blocked, list) else [])
    )
    feedback_id = str(refs.get("evaluation_feedback_id") or "")
    if feedback_id:
        from zf.runtime.channel_evaluation_feedback import validate_feedback_reply

        feedback_error = validate_feedback_reply(
            state_dir=state_dir, writer=writer, refs=refs, review=review,
            channel_id=channel_id, thread_id=thread_id, member_id=member_id,
        )
        if feedback_error:
            writer.emit("channel.consensus.review.rejected", actor=member_id or actor,
                        causation_id=reply_event_id, correlation_id=channel_id,
                        payload={"channel_id": channel_id, "thread_id": thread_id,
                                 "review_id": review_id, "member_id": member_id,
                                 "reason": feedback_error, "source": source})
            return
        if any(row.type in {"channel.consensus.signed", "channel.consensus.blocked"}
               and (row.payload or {}).get("review_id") == review_id
               for row in writer.event_log.read_all()):
            return
    if already_settled and not feedback_id:
        return
    verdict = str(review.get("verdict") or "").strip().lower()
    supplied_digest = str(review.get("artifact_digest") or "").strip()
    if (
        not consensus
        or member_id not in required_signers
        or not review
        or verdict not in {"signed", "blocked"}
        or not expected_digest
        or expected_digest != current_digest
        or str(refs.get("artifact_ref") or "")
        != str(consensus.get("artifact_ref") or "")
        or supplied_digest != expected_digest
    ):
        writer.emit(
            "channel.consensus.review.rejected",
            actor=member_id or actor,
            task_id=str(request.get("task_id") or "") or None,
            causation_id=reply_event_id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "review_id": review_id,
                "member_id": member_id,
                "reason": "invalid_or_stale_consensus_review",
                "expected_artifact_digest": current_digest,
                "supplied_artifact_digest": supplied_digest,
                "source": source,
            },
        )
        return
    common = {
        "channel_id": channel_id,
        "thread_id": thread_id,
        "member_id": member_id,
        "artifact_ref": str(refs.get("artifact_ref") or ""),
        "artifact_digest": expected_digest,
        "review_id": review_id,
        "summary": str(review.get("summary") or "").strip(),
        "evidence_refs": string_refs(review.get("evidence_refs")),
        "source": source,
    }
    if verdict == "signed":
        writer.emit(
            "channel.consensus.signed",
            actor=member_id or actor,
            task_id=str(request.get("task_id") or "") or None,
            causation_id=reply_event_id,
            correlation_id=channel_id,
            payload=common,
        )
        if feedback_id:
            from zf.runtime.channel_evaluation_feedback import record_feedback_reply

            record_feedback_reply(state_dir=state_dir, writer=writer, refs=refs,
                                  review=review, reply_event_id=reply_event_id, member_id=member_id)
        return
    blocker = str(review.get("blocker_question") or "").strip()
    if not blocker:
        writer.emit(
            "channel.consensus.review.rejected",
            actor=member_id or actor,
            task_id=str(request.get("task_id") or "") or None,
            causation_id=reply_event_id,
            correlation_id=channel_id,
            payload={
                **common,
                "reason": "blocked_review_requires_blocker_question",
            },
        )
        return
    blocker_kind = str(review.get("blocker_kind") or "owner_decision").strip()
    if blocker_kind not in {"owner_decision", "revision_request"}:
        writer.emit(
            "channel.consensus.review.rejected",
            actor=member_id or actor,
            task_id=str(request.get("task_id") or "") or None,
            causation_id=reply_event_id,
            correlation_id=channel_id,
            payload={**common, "reason": "invalid_blocker_kind"},
        )
        return
    blocker_id = str(review.get("blocker_question_id") or "").strip()
    if not blocker_id:
        blocker_id = "q-" + hashlib.sha1(
            (
                f"{channel_id}:{thread_id}:{member_id}:"
                f"consensus-blocker:{blocker}"
            ).encode("utf-8")
        ).hexdigest()[:16]
    writer.emit(
        "channel.consensus.blocked",
        actor=member_id or actor,
        task_id=str(request.get("task_id") or "") or None,
        causation_id=reply_event_id,
        correlation_id=channel_id,
        payload={
            **common,
            "blocker_question_id": blocker_id,
            "blocker_question": blocker,
            "blocker_kind": blocker_kind,
            "dissent": str(review.get("summary") or blocker).strip(),
        },
    )
    if feedback_id:
        from zf.runtime.channel_evaluation_feedback import record_feedback_reply

        record_feedback_reply(state_dir=state_dir, writer=writer, refs=refs,
                              review=review, reply_event_id=reply_event_id, member_id=member_id)


def reply_question_records(
    body: dict[str, Any],
    *,
    channel: dict[str, Any],
    channel_id: str,
    thread_id: str,
    member_id: str,
    source_kind: str,
    reuse_by_text: bool = True,
) -> tuple[list[dict[str, Any]], str]:
    raw_questions: list[object] = []
    for key in ("questions", "open_questions"):
        value = body.get(key)
        if isinstance(value, list):
            raw_questions.extend(value)
    if not raw_questions:
        return [], ""
    members = {
        str(item.get("member_id") or "")
        for item in channel.get("members") or []
        if isinstance(item, dict) and str(item.get("member_id") or "")
    }
    existing_raw = channel.get("open_questions") or []
    existing = (
        list(existing_raw.values())
        if isinstance(existing_raw, dict)
        else list(existing_raw)
    )
    existing_by_text: dict[str, str] = {}
    for record in existing:
        if (
            not isinstance(record, dict)
            or str(record.get("thread_id") or "main") != thread_id
        ):
            continue
        identity = question_text_identity(record.get("question"))
        question_id = str(record.get("question_id") or "").strip()
        if not identity or not question_id:
            continue
        if str(record.get("status") or "") == "merged":
            question_id = str(record.get("merged_into") or question_id)
        existing_by_text.setdefault(identity, question_id)
    drafts: list[tuple[dict[str, Any], str, str]] = []
    local_to_global: dict[str, str] = {}
    current_by_text: dict[str, str] = {}
    for index, raw in enumerate(raw_questions, 1):
        item = dict(raw) if isinstance(raw, dict) else {}
        text = str(
            item.get("question")
            or item.get("text")
            or (raw if isinstance(raw, str) else "")
        ).strip()
        if not text:
            continue
        local_id = str(
            item.get("question_id") or item.get("id") or f"q{index}"
        ).strip()
        if local_id in local_to_global:
            return [], f"duplicate_local_question_id:{local_id}"
        identity = question_text_identity(text)
        reused_id = (existing_by_text.get(identity) or current_by_text.get(identity)) if reuse_by_text else ""
        if reused_id:
            local_to_global[local_id] = reused_id
            continue
        digest = hashlib.sha1(
            (f"{channel_id}:{thread_id}:question:{identity}" + (f":{index}" if not reuse_by_text else "")).encode("utf-8")
        ).hexdigest()[:16]
        question_id = f"q-{digest}"
        local_to_global[local_id] = question_id
        current_by_text[identity] = question_id
        drafts.append((item, text, question_id))
    records: list[dict[str, Any]] = []
    for item, text, question_id in drafts:
        raw_dependencies = item.get("depends_on") or []
        if not isinstance(raw_dependencies, list):
            return [], "question_dependencies_must_be_a_list"
        dependencies = [
            local_to_global.get(str(value), str(value))
            for value in raw_dependencies
            if isinstance(value, str) and str(value).strip()
        ]
        normalized, error = normalize_question_payload(
            {
                **item,
                "depends_on": dependencies,
                "category": str(
                    item.get("category")
                    or (
                        "synthesis"
                        if source_kind == "synthesis"
                        else "clarification"
                    )
                ),
            },
            question_id=question_id,
            question=text,
            asked_by=member_id,
            member_ids=members,
        )
        if error:
            return [], error
        records.append(normalized)
    graph_error = validate_question_graph([
        *[
            item
            for item in existing
            if isinstance(item, dict)
            and str(item.get("question_id") or "")
        ],
        *records,
    ])
    if graph_error:
        return [], graph_error
    return _topological_question_order(records), ""


def active_discussion_roster(
    channel: dict[str, Any],
    *,
    thread_id: str,
) -> list[str]:
    sessions = channel.get("discussions")
    session = sessions.get(thread_id) if isinstance(sessions, dict) else {}
    requested = (
        [
            str(item)
            for item in session.get("roster") or []
            if str(item)
        ]
        if isinstance(session, dict)
        else []
    )
    active = {
        str(member.get("member_id") or "")
        for member in channel.get("members") or []
        if isinstance(member, dict)
        and str(member.get("member_id") or "")
        and str(member.get("status") or "").lower()
        not in {"removed", "suspended", "rejected", "failed"}
    }
    return [
        member_id
        for member_id in dict.fromkeys(requested)
        if member_id in active
    ]


def _emit_cross_review_rejected(
    *,
    writer: EventWriter,
    channel_id: str,
    thread_id: str,
    request_id: str,
    member_id: str,
    reply_event_id: str,
    task_id: str | None,
    source: str,
    reason: str,
) -> None:
    writer.emit(
        "channel.cross_review.rejected",
        actor=member_id or "channel-contract",
        task_id=task_id,
        causation_id=reply_event_id,
        correlation_id=channel_id,
        payload={
            "schema_version": "channel.cross_review.v1",
            "channel_id": channel_id,
            "thread_id": thread_id,
            "request_id": request_id,
            "target_member_id": member_id,
            "reason": reason,
            "source": source,
        },
    )


def _topological_question_order(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    pending = {
        str(record.get("question_id") or ""): record
        for record in records
    }
    emitted: set[str] = set()
    ordered: list[dict[str, Any]] = []
    while pending:
        ready = [
            question_id
            for question_id, record in pending.items()
            if all(
                dependency not in pending or dependency in emitted
                for dependency in record.get("depends_on") or []
            )
        ]
        if not ready:
            return records
        for question_id in sorted(ready):
            ordered.append(pending.pop(question_id))
            emitted.add(question_id)
    return ordered


def _question_by_id(
    channel: dict[str, Any],
    question_id: str,
) -> dict[str, Any]:
    raw = channel.get("open_questions") or []
    candidates = list(raw.values()) if isinstance(raw, dict) else list(raw)
    return next(
        (
            item
            for item in candidates
            if isinstance(item, dict)
            and str(item.get("question_id") or "") == question_id
        ),
        {},
    )


def _cross_review_by_id(
    channel: dict[str, Any],
    request_id: str,
) -> dict[str, Any]:
    raw = channel.get("cross_reviews") or []
    candidates = list(raw.values()) if isinstance(raw, dict) else list(raw)
    return next(
        (
            item
            for item in candidates
            if isinstance(item, dict)
            and str(item.get("request_id") or "") == request_id
        ),
        {},
    )


def _thread_consensus(
    channel: dict[str, Any],
    thread_id: str,
) -> dict[str, Any]:
    consensus = channel.get("consensus")
    if not isinstance(consensus, dict):
        return {}
    item = consensus.get(thread_id)
    return item if isinstance(item, dict) else {}


__all__ = [
    "active_discussion_roster",
    "apply_consensus_review_reply",
    "apply_cross_review_reply",
    "reply_question_records",
]
