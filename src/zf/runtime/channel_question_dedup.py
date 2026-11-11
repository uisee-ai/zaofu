"""Typed, replay-safe question-ledger deduplication for Agent Channels."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from zf.core.events import EventWriter
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_question_graph import (
    normalize_question_payload,
    validate_question_graph,
)


QUESTION_DEDUP_SCHEMA_VERSION = "channel.question.dedup.v1"
MAX_QUESTION_DEDUP_ATTEMPTS = 3


def question_ledger(
    channel: dict[str, Any] | None,
    *,
    thread_id: str,
) -> list[dict[str, Any]]:
    """Return a stable full ledger for one thread."""
    raw = (channel or {}).get("open_questions") or []
    candidates = list(raw.values()) if isinstance(raw, dict) else list(raw)
    items = [
        item
        for item in candidates
        if isinstance(item, dict)
        and str(item.get("thread_id") or "main") == thread_id
    ]
    return sorted(
        [
            {
                "question_id": str(item.get("question_id") or ""),
                "question": str(item.get("question") or ""),
                "category": str(item.get("category") or ""),
                "kind": str(item.get("kind") or "owner_decision"),
                "depends_on": [
                    str(value)
                    for value in item.get("depends_on") or []
                    if str(value)
                ],
                "priority": str(item.get("priority") or "p1"),
                "why_it_matters": str(
                    item.get("why_it_matters") or ""
                ),
                "recommended_answer": str(
                    item.get("recommended_answer") or ""
                ),
                "options": [
                    {
                        "id": str(option.get("id") or ""),
                        "label": str(option.get("label") or ""),
                        "description": str(option.get("description") or ""),
                        "recommended": bool(option.get("recommended")),
                    }
                    for option in item.get("options") or []
                    if isinstance(option, dict)
                ],
                "allow_other": bool(item.get("allow_other", True)),
                "target_member_id": str(
                    item.get("target_member_id") or "owner"
                ),
                "asked_by": str(item.get("asked_by") or ""),
                "status": str(item.get("status") or ""),
                "resolution": str(item.get("resolution") or ""),
                "resolved_by": str(item.get("resolved_by") or ""),
                "answer": str(item.get("answer") or ""),
                "risk_note": str(item.get("risk_note") or ""),
                "merged_into": str(item.get("merged_into") or ""),
                "opened_event_id": str(item.get("opened_event_id") or ""),
                "resolved_event_id": str(item.get("resolved_event_id") or ""),
                "merged_event_id": str(item.get("merged_event_id") or ""),
            }
            for item in items
            if str(item.get("question_id") or "")
        ],
        key=lambda item: item["question_id"],
    )


def question_ledger_digest(
    channel: dict[str, Any] | None,
    *,
    thread_id: str,
) -> str:
    canonical = json.dumps(
        question_ledger(channel, thread_id=thread_id),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stable_question_dedup_request_id(
    channel_id: str,
    thread_id: str,
    discussion_started_event_id: str,
    *,
    generation: int = 1,
) -> str:
    generation_suffix = "" if generation <= 1 else f":generation:{generation}"
    digest = hashlib.sha1(
        (
            f"{channel_id}:{thread_id}:{discussion_started_event_id}:"
            f"question-dedup{generation_suffix}"
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"dedup-{digest}"


def apply_question_dedup_reply(
    *,
    state_dir: Path,
    writer: EventWriter,
    channel_id: str,
    thread_id: str,
    request_id: str,
    payload: dict[str, Any],
    actor: str,
    source: str,
    causation_id: str,
    task_id: str | None = None,
) -> tuple[bool, str]:
    """Validate one semantic merge plan and emit canonical merge events."""
    from zf.runtime.channel_libre import is_libre
    if is_libre(project_channel(Path(state_dir), channel_id)):
        from zf.runtime.channel_libre_dedup import apply_libre_dedup
        return apply_libre_dedup(state_dir=state_dir, writer=writer, channel_id=channel_id, thread_id=thread_id,
                                request_id=request_id, payload=payload, actor=actor, source=source,
                                causation_id=causation_id, task_id=task_id)
    prior = next(
        (
            event
            for event in reversed(writer.event_log.read_all())
            if event.type == "channel.question.dedup.applied"
            and str(event.payload.get("request_id") or "") == request_id
        ),
        None,
    )
    if prior is not None:
        return True, "already_applied"

    channel = project_channel(Path(state_dir), channel_id) or {}
    current_digest = question_ledger_digest(channel, thread_id=thread_id)
    supplied_digest = str(payload.get("ledger_digest") or "").strip()
    if supplied_digest != current_digest:
        return _reject(
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            request_id=request_id,
            actor=actor,
            source=source,
            causation_id=causation_id,
            task_id=task_id,
            reason="stale_ledger_digest",
            details={
                "expected_ledger_digest": current_digest,
                "supplied_ledger_digest": supplied_digest,
            },
        )

    groups = payload.get("groups")
    if not isinstance(groups, list):
        return _reject(
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            request_id=request_id,
            actor=actor,
            source=source,
            causation_id=causation_id,
            task_id=task_id,
            reason="groups_must_be_a_list",
        )

    ledger = {
        item["question_id"]: item
        for item in question_ledger(channel, thread_id=thread_id)
    }
    open_ids = {
        question_id
        for question_id, item in ledger.items()
        if item["status"] == "open"
    }
    merge_map: dict[str, str] = {}
    canonical_ids: set[str] = set()
    normalized_groups: list[dict[str, Any]] = []
    for index, raw_group in enumerate(groups):
        if not isinstance(raw_group, dict):
            return _reject(
                writer=writer,
                channel_id=channel_id,
                thread_id=thread_id,
                request_id=request_id,
                actor=actor,
                source=source,
                causation_id=causation_id,
                task_id=task_id,
                reason=f"groups[{index}]_must_be_an_object",
            )
        canonical_id = str(
            raw_group.get("canonical_question_id") or ""
        ).strip()
        merge_ids = raw_group.get("merge_question_ids")
        if canonical_id not in open_ids:
            return _reject(
                writer=writer,
                channel_id=channel_id,
                thread_id=thread_id,
                request_id=request_id,
                actor=actor,
                source=source,
                causation_id=causation_id,
                task_id=task_id,
                reason=f"canonical_question_not_open:{canonical_id}",
            )
        if canonical_id in canonical_ids:
            return _reject(
                writer=writer,
                channel_id=channel_id,
                thread_id=thread_id,
                request_id=request_id,
                actor=actor,
                source=source,
                causation_id=causation_id,
                task_id=task_id,
                reason=f"canonical_question_reused:{canonical_id}",
            )
        canonical_ids.add(canonical_id)
        if not isinstance(merge_ids, list):
            return _reject(
                writer=writer,
                channel_id=channel_id,
                thread_id=thread_id,
                request_id=request_id,
                actor=actor,
                source=source,
                causation_id=causation_id,
                task_id=task_id,
                reason=f"groups[{index}].merge_question_ids_must_be_a_list",
            )
        normalized_merge_ids: list[str] = []
        for raw_question_id in merge_ids:
            question_id = str(raw_question_id or "").strip()
            if question_id == canonical_id:
                return _reject(
                    writer=writer,
                    channel_id=channel_id,
                    thread_id=thread_id,
                    request_id=request_id,
                    actor=actor,
                    source=source,
                    causation_id=causation_id,
                    task_id=task_id,
                    reason=f"question_cannot_merge_into_itself:{question_id}",
                )
            if question_id not in open_ids:
                return _reject(
                    writer=writer,
                    channel_id=channel_id,
                    thread_id=thread_id,
                    request_id=request_id,
                    actor=actor,
                    source=source,
                    causation_id=causation_id,
                    task_id=task_id,
                    reason=f"merge_question_not_open:{question_id}",
                )
            if question_id in merge_map:
                return _reject(
                    writer=writer,
                    channel_id=channel_id,
                    thread_id=thread_id,
                    request_id=request_id,
                    actor=actor,
                    source=source,
                    causation_id=causation_id,
                    task_id=task_id,
                    reason=f"merge_question_reused:{question_id}",
                )
            merge_map[question_id] = canonical_id
            normalized_merge_ids.append(question_id)
        normalized_groups.append({
            "canonical_question_id": canonical_id,
            "merge_question_ids": normalized_merge_ids,
            "reason": str(raw_group.get("reason") or "").strip(),
        })

    chained = sorted(set(merge_map) & set(merge_map.values()))
    if chained:
        return _reject(
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            request_id=request_id,
            actor=actor,
            source=source,
            causation_id=causation_id,
            task_id=task_id,
            reason="merge_chain_or_cycle:" + ",".join(chained),
        )

    member_ids = {
        str(member.get("member_id") or "")
        for member in channel.get("members") or []
        if isinstance(member, dict)
        and str(member.get("member_id") or "")
    }
    updates = payload.get("question_updates") or []
    if not isinstance(updates, list):
        return _reject(
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            request_id=request_id,
            actor=actor,
            source=source,
            causation_id=causation_id,
            task_id=task_id,
            reason="question_updates_must_be_a_list",
        )
    normalized_updates: list[dict[str, Any]] = []
    proposed_ledger = {key: dict(value) for key, value in ledger.items()}
    updated_ids: set[str] = set()
    for index, raw_update in enumerate(updates):
        if not isinstance(raw_update, dict):
            return _reject(
                writer=writer,
                channel_id=channel_id,
                thread_id=thread_id,
                request_id=request_id,
                actor=actor,
                source=source,
                causation_id=causation_id,
                task_id=task_id,
                reason=f"question_updates[{index}]_must_be_an_object",
            )
        question_id = str(raw_update.get("question_id") or "").strip()
        if question_id not in open_ids:
            return _reject(
                writer=writer,
                channel_id=channel_id,
                thread_id=thread_id,
                request_id=request_id,
                actor=actor,
                source=source,
                causation_id=causation_id,
                task_id=task_id,
                reason=f"question_update_not_open:{question_id}",
            )
        if question_id in updated_ids:
            return _reject(
                writer=writer,
                channel_id=channel_id,
                thread_id=thread_id,
                request_id=request_id,
                actor=actor,
                source=source,
                causation_id=causation_id,
                task_id=task_id,
                reason=f"question_update_reused:{question_id}",
            )
        updated_ids.add(question_id)
        current = ledger[question_id]
        normalized, error = normalize_question_payload(
            {**current, **raw_update},
            question_id=question_id,
            question=str(current.get("question") or ""),
            asked_by=str(current.get("asked_by") or ""),
            member_ids=member_ids,
        )
        if error:
            return _reject(
                writer=writer,
                channel_id=channel_id,
                thread_id=thread_id,
                request_id=request_id,
                actor=actor,
                source=source,
                causation_id=causation_id,
                task_id=task_id,
                reason=error,
            )
        normalized_updates.append(normalized)
        proposed_ledger[question_id] = {
            **current,
            **normalized,
        }
    graph_error = validate_question_graph(proposed_ledger.values())
    if graph_error:
        return _reject(
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            request_id=request_id,
            actor=actor,
            source=source,
            causation_id=causation_id,
            task_id=task_id,
            reason=graph_error,
        )

    cross_review_requests = payload.get("cross_review_requests") or []
    if not isinstance(cross_review_requests, list):
        return _reject(
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            request_id=request_id,
            actor=actor,
            source=source,
            causation_id=causation_id,
            task_id=task_id,
            reason="cross_review_requests_must_be_a_list",
        )
    cross_review_requests = list(cross_review_requests)
    covered_fact_ids = {
        str(item.get("question_id") or "").strip()
        for item in cross_review_requests
        if isinstance(item, dict)
    }
    surviving_ids = sorted(open_ids - set(merge_map))
    for question_id in surviving_ids:
        proposed = proposed_ledger[question_id]
        if str(proposed.get("kind") or "owner_decision") != "fact":
            continue
        target_member_id = str(
            proposed.get("target_member_id") or ""
        ).strip()
        if target_member_id not in member_ids:
            return _reject(
                writer=writer,
                channel_id=channel_id,
                thread_id=thread_id,
                request_id=request_id,
                actor=actor,
                source=source,
                causation_id=causation_id,
                task_id=task_id,
                reason=f"fact_question_requires_member_target:{question_id}",
            )
        if question_id in covered_fact_ids:
            continue
        cross_review_requests.append({
            "question_id": question_id,
            "target_member_ids": [target_member_id],
            "prompt": (
                "Verify this fact against available evidence and report the "
                "strongest counterexample. If the referenced future artifact "
                "does not exist yet, state that explicitly with evidence."
            ),
            "reason": "Every surviving fact requires evidence-bound review.",
            "source_refs": [f"question:{question_id}"],
        })
    normalized_cross_reviews, cross_review_error = _normalize_cross_reviews(
        cross_review_requests,
        open_ids=open_ids,
        merge_map=merge_map,
        member_ids=member_ids,
        request_id=request_id,
    )
    if cross_review_error:
        return _reject(
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            request_id=request_id,
            actor=actor,
            source=source,
            causation_id=causation_id,
            task_id=task_id,
            reason=cross_review_error,
        )

    for update in normalized_updates:
        writer.emit(
            "channel.question.updated",
            actor=actor,
            task_id=task_id,
            causation_id=causation_id,
            correlation_id=channel_id,
            payload={
                "schema_version": QUESTION_DEDUP_SCHEMA_VERSION,
                "channel_id": channel_id,
                "thread_id": thread_id,
                **update,
                "request_id": request_id,
                "source": source,
            },
        )

    for group in normalized_groups:
        canonical_id = str(group["canonical_question_id"])
        for question_id in group["merge_question_ids"]:
            writer.emit(
                "channel.question.merged",
                actor=actor,
                task_id=task_id,
                causation_id=causation_id,
                correlation_id=channel_id,
                payload={
                    "schema_version": QUESTION_DEDUP_SCHEMA_VERSION,
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "question_id": question_id,
                    "into_question_id": canonical_id,
                    "reason": str(group.get("reason") or ""),
                    "request_id": request_id,
                    "source": source,
                },
            )

    for request in normalized_cross_reviews:
        writer.emit(
            "channel.cross_review.requested",
            actor=actor,
            task_id=task_id,
            causation_id=causation_id,
            correlation_id=channel_id,
            payload={
                "schema_version": "channel.cross_review.v1",
                "channel_id": channel_id,
                "thread_id": thread_id,
                **request,
                "source": source,
            },
        )

    updated = project_channel(Path(state_dir), channel_id) or {}
    writer.emit(
        "channel.question.dedup.applied",
        actor=actor,
        task_id=task_id,
        causation_id=causation_id,
        correlation_id=channel_id,
        payload={
            "schema_version": QUESTION_DEDUP_SCHEMA_VERSION,
            "channel_id": channel_id,
            "thread_id": thread_id,
            "request_id": request_id,
            "input_ledger_digest": current_digest,
            "output_ledger_digest": question_ledger_digest(
                updated,
                thread_id=thread_id,
            ),
            "group_count": len(normalized_groups),
            "merge_count": len(merge_map),
            "question_update_count": len(normalized_updates),
            "cross_review_count": len(normalized_cross_reviews),
            "source": source,
        },
    )
    return True, "applied"


def _reject(
    *,
    writer: EventWriter,
    channel_id: str,
    thread_id: str,
    request_id: str,
    actor: str,
    source: str,
    causation_id: str,
    task_id: str | None,
    reason: str,
    details: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    writer.emit(
        "channel.question.dedup.rejected",
        actor=actor,
        task_id=task_id,
        causation_id=causation_id,
        correlation_id=channel_id,
        payload={
            "schema_version": QUESTION_DEDUP_SCHEMA_VERSION,
            "channel_id": channel_id,
            "thread_id": thread_id,
            "request_id": request_id,
            "reason": reason,
            "details": details or {},
            "source": source,
        },
    )
    return False, reason


def _normalize_cross_reviews(
    raw_requests: object,
    *,
    open_ids: set[str],
    merge_map: dict[str, str],
    member_ids: set[str],
    request_id: str,
) -> tuple[list[dict[str, Any]], str]:
    if not isinstance(raw_requests, list):
        return [], "cross_review_requests_must_be_a_list"
    if len(raw_requests) > 8:
        return [], "cross_review_request_limit_exceeded"
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_requests):
        if not isinstance(raw, dict):
            return [], f"cross_review_requests[{index}]_must_be_an_object"
        question_id = str(raw.get("question_id") or "").strip()
        if question_id not in open_ids:
            return [], f"cross_review_question_not_open:{question_id}"
        if question_id in merge_map:
            return [], f"cross_review_question_is_merged:{question_id}"
        prompt = str(raw.get("prompt") or "").strip()
        reason = str(raw.get("reason") or "").strip()
        if not prompt:
            return [], f"cross_review_prompt_required:{question_id}"
        targets = raw.get("target_member_ids")
        if not isinstance(targets, list) or not targets:
            return [], f"cross_review_targets_required:{question_id}"
        if len(targets) > 4:
            return [], f"cross_review_target_limit_exceeded:{question_id}"
        raw_source_refs = raw.get("source_refs") or []
        if not isinstance(raw_source_refs, list):
            return [], f"cross_review_source_refs_must_be_a_list:{question_id}"
        source_refs = list(dict.fromkeys(
            str(item).strip()
            for item in raw_source_refs
            if isinstance(item, str) and str(item).strip()
        ))[:32]
        for target in targets:
            target_member_id = str(target or "").strip()
            if target_member_id not in member_ids:
                return [], f"unknown_cross_review_target:{target_member_id}"
            pair = (question_id, target_member_id)
            if pair in seen:
                return [], (
                    "duplicate_cross_review_target:"
                    f"{question_id}:{target_member_id}"
                )
            seen.add(pair)
            digest = hashlib.sha1(
                (
                    f"{request_id}:{question_id}:{target_member_id}:"
                    "cross-review"
                ).encode("utf-8")
            ).hexdigest()[:16]
            normalized.append({
                "request_id": f"xreview-{digest}",
                "dedup_request_id": request_id,
                "question_id": question_id,
                "target_member_id": target_member_id,
                "prompt": prompt,
                "reason": reason,
                "source_refs": source_refs,
            })
    return normalized, ""


__all__ = [
    "QUESTION_DEDUP_SCHEMA_VERSION",
    "apply_question_dedup_reply",
    "question_ledger",
    "question_ledger_digest",
    "stable_question_dedup_request_id",
]
