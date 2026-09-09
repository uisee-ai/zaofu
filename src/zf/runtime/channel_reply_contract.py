"""Structured contribution and synthesis contracts for Channel replies."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from zf.core.events import EventWriter
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.runtime.channel_contract_artifacts import (
    channel_template_binding,
    persist_channel_contract,
    persist_channel_semantic_coverage,
    persist_channel_source_manifest,
    string_refs,
    typed_items,
    validate_channel_contract,
)
from zf.runtime.channel_contribution_repair import reject_contribution_contract
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_deliberation_contract import (
    apply_consensus_review_reply,
    apply_cross_review_reply,
    reply_question_records,
)
from zf.runtime.channel_prd_revision import (
    consensus_mode_and_required_signers,
    persist_synthesis_prd_revision,
)
from zf.runtime.channel_prd_render import (
    channel_prd_event_refs,
    channel_requirement_text,
    render_channel_prd_artifact,
)
from zf.runtime.channel_semantic_sources import (
    validate_semantic_source_coverage,
)
from zf.runtime.channel_question_dedup import apply_question_dedup_reply
from zf.runtime.channel_reply_prompt import (
    channel_reply_response_contract,
    fake_channel_reply_text,
)
from zf.runtime.channel_reply_parsing import (
    display_value as _display_value,
    reply_question_texts as _reply_question_texts,
    structured_reply_payload as _structured_reply_payload,
    structured_reply_payload_with_error as _structured_reply_payload_with_error,
)
from zf.runtime.channel_synthesis_repair import (
    ignore_stale_synthesis_repair as _ignore_stale_synthesis_repair,
    reject_synthesis_contract as _reject_synthesis_contract,
)
from zf.runtime.channel_synthesis_coverage import (
    contract_coverage_error,
    synthesis_contract_sources,
)
from zf.runtime.channel_synthesis_next_round import (
    emit_synthesis_next_round_proposal,
    validate_synthesis_next_round,
)
from zf.runtime.channel_templates import CHANNEL_TEMPLATES


def emit_structured_reply_events(
    *,
    state_dir: Path,
    writer: EventWriter,
    channel: dict[str, Any],
    request: dict[str, Any],
    message: dict[str, Any],
    reply: str,
    reply_event_id: str,
    actor: str,
    source: str,
) -> None:
    channel_id = str(
        channel.get("channel_id") or request.get("channel_id") or ""
    )
    thread_id = str(request.get("thread_id") or "main")
    member_id = str(request.get("target_member_id") or "")
    refs = (
        message.get("refs")
        if isinstance(message.get("refs"), dict)
        else {}
    )
    adaptive_next_round_id = str(refs.get("adaptive_next_round_id") or "")
    synthesis_request_id = str(refs.get("synthesis_request_id") or "")
    synthesis_repair_id = str(refs.get("synthesis_repair_id") or "")
    try:
        synthesis_repair_revision = max(
            0,
            int(refs.get("synthesis_repair_revision") or 0),
        )
    except (TypeError, ValueError):
        synthesis_repair_revision = 0
    question_dedup_request_id = str(
        refs.get("question_dedup_request_id") or ""
    )
    cross_review_request_id = str(
        refs.get("cross_review_request_id") or ""
    )
    consensus_review_id = str(refs.get("consensus_review_id") or "")
    if cross_review_request_id:
        apply_cross_review_reply(
            state_dir=state_dir,
            writer=writer,
            channel=channel,
            channel_id=channel_id,
            thread_id=thread_id,
            member_id=member_id,
            request=request,
            refs=refs,
            review=_structured_reply_payload(
                reply,
                "channel_cross_review",
            ),
            reply_event_id=reply_event_id,
            actor=actor,
            source=source,
        )
        return
    if consensus_review_id:
        apply_consensus_review_reply(
            writer=writer,
            channel=channel,
            channel_id=channel_id,
            thread_id=thread_id,
            member_id=member_id,
            request=request,
            refs=refs,
            review=_structured_reply_payload(
                reply,
                "channel_consensus_review",
            ),
            reply_event_id=reply_event_id,
            actor=actor,
            source=source,
        )
        return
    if question_dedup_request_id:
        dedup = _structured_reply_payload(
            reply,
            "channel_question_dedup",
        )
        if not dedup:
            writer.emit(
                "channel.question.dedup.rejected",
                actor=member_id or actor,
                task_id=str(request.get("task_id") or "") or None,
                causation_id=reply_event_id,
                correlation_id=channel_id,
                payload={
                    "schema_version": "channel.question.dedup.v1",
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "request_id": question_dedup_request_id,
                    "reason": "invalid_missing_channel_question_dedup",
                    "source": source,
                },
            )
            return
        apply_question_dedup_reply(
            state_dir=state_dir,
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            request_id=question_dedup_request_id,
            payload=dedup,
            actor=member_id or actor,
            source=source,
            causation_id=reply_event_id,
            task_id=str(request.get("task_id") or "") or None,
        )
        return
    if synthesis_request_id and not adaptive_next_round_id:
        _emit_synthesis(
            state_dir=state_dir,
            writer=writer,
            channel=channel,
            channel_id=channel_id,
            thread_id=thread_id,
            member_id=member_id,
            request=request,
            reply=reply,
            reply_event_id=reply_event_id,
            synthesis_request_id=synthesis_request_id,
            synthesis_repair_id=synthesis_repair_id,
            synthesis_repair_revision=synthesis_repair_revision,
            actor=actor,
            source=source,
        )
        return
    if not channel_reply_response_contract(channel, request, message):
        return
    _emit_contribution(
        state_dir=state_dir,
        writer=writer,
        channel=channel,
        channel_id=channel_id,
        thread_id=thread_id,
        member_id=member_id,
        request=request,
        reply=reply,
        reply_event_id=reply_event_id,
        actor=actor,
        source=source,
    )


def _emit_synthesis(
    *,
    state_dir: Path,
    writer: EventWriter,
    channel: dict[str, Any],
    channel_id: str,
    thread_id: str,
    member_id: str,
    request: dict[str, Any],
    reply: str,
    reply_event_id: str,
    synthesis_request_id: str,
    synthesis_repair_id: str,
    synthesis_repair_revision: int,
    actor: str,
    source: str,
) -> None:
    safe_channel_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", channel_id)
    safe_thread_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", thread_id)
    with locked_path(
        Path(state_dir)
        / "locks"
        / f"channel-prd-{safe_channel_id}-{safe_thread_id}"
    ):
        _emit_synthesis_locked(
            state_dir=state_dir,
            writer=writer,
            channel=project_channel(Path(state_dir), channel_id) or channel,
            channel_id=channel_id,
            thread_id=thread_id,
            member_id=member_id,
            request=request,
            reply=reply,
            reply_event_id=reply_event_id,
            synthesis_request_id=synthesis_request_id,
            synthesis_repair_id=synthesis_repair_id,
            synthesis_repair_revision=synthesis_repair_revision,
            actor=actor,
            source=source,
        )


def _emit_synthesis_locked(
    *,
    state_dir: Path,
    writer: EventWriter,
    channel: dict[str, Any],
    channel_id: str,
    thread_id: str,
    member_id: str,
    request: dict[str, Any],
    reply: str,
    reply_event_id: str,
    synthesis_request_id: str,
    synthesis_repair_id: str,
    synthesis_repair_revision: int,
    actor: str,
    source: str,
) -> None:
    if _ignore_stale_synthesis_repair(
        writer=writer,
        channel_id=channel_id,
        thread_id=thread_id,
        synthesis_request_id=synthesis_request_id,
        synthesis_repair_id=synthesis_repair_id,
        synthesis_repair_revision=synthesis_repair_revision,
        reply_event_id=reply_event_id,
        task_id=str(request.get("task_id") or ""),
    ):
        return
    if _ignore_superseded_synthesis_reply(
        writer=writer,
        channel=channel,
        channel_id=channel_id,
        thread_id=thread_id,
        synthesis_request_id=synthesis_request_id,
        reply_event_id=reply_event_id,
        task_id=str(request.get("task_id") or ""),
        source=source,
    ):
        return
    synthesis, parse_error = _structured_reply_payload_with_error(
        reply,
        "channel_synthesis",
    )
    if not synthesis:
        _reject_synthesis_contract(
            state_dir=state_dir,
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            member_id=member_id,
            request=request,
            reply=reply,
            reply_event_id=reply_event_id,
            actor=actor,
            source=source,
            status="invalid_missing_channel_synthesis",
            reason=parse_error,
            synthesis_request_id=synthesis_request_id,
            synthesis_repair_revision=synthesis_repair_revision,
        )
        return
    validation_error = validate_channel_contract(
        synthesis,
        kind="synthesis",
    )
    classification = synthesis.get("classification")
    if isinstance(classification, dict):
        classified_template = str(
            classification.get("template_id") or ""
        ).strip()
        if (
            classified_template
            and classified_template not in CHANNEL_TEMPLATES
        ):
            validation_error = (
                f"unknown classification template_id: {classified_template}"
            )
    if validation_error:
        _reject_synthesis_contract(
            state_dir=state_dir,
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            member_id=member_id,
            request=request,
            reply=reply,
            reply_event_id=reply_event_id,
            actor=actor,
            source=source,
            status="invalid_channel_synthesis",
            reason=validation_error,
            synthesis_request_id=synthesis_request_id,
            synthesis_repair_revision=synthesis_repair_revision,
        )
        return
    coverage, coverage_error = validate_semantic_source_coverage(
        channel,
        request,
        synthesis.get("consumed_message_digests"),
    )
    if coverage_error:
        _reject_synthesis_contract(
            state_dir=state_dir,
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            member_id=member_id,
            request=request,
            reply=reply,
            reply_event_id=reply_event_id,
            actor=actor,
            source=source,
            status="invalid_semantic_source_coverage",
            reason=coverage_error,
            synthesis_request_id=synthesis_request_id,
            synthesis_repair_revision=synthesis_repair_revision,
        )
        return
    contribution_refs, contribution_digests = synthesis_contract_sources(
        channel,
        thread_id=thread_id,
    )
    declared_contribution_refs = string_refs(
        synthesis.get("consumed_contribution_refs")
    )
    declared_contribution_digests = string_refs(
        synthesis.get("consumed_contribution_digests")
    )
    contribution_coverage_error = contract_coverage_error(
        required_refs=contribution_refs,
        required_digests=contribution_digests,
        consumed_refs=declared_contribution_refs,
        consumed_digests=declared_contribution_digests,
    )
    if contribution_coverage_error:
        _reject_synthesis_contract(
            state_dir=state_dir,
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            member_id=member_id,
            request=request,
            reply=reply,
            reply_event_id=reply_event_id,
            actor=actor,
            source=source,
            status="invalid_contribution_coverage",
            reason=contribution_coverage_error,
            synthesis_request_id=synthesis_request_id,
            synthesis_repair_revision=synthesis_repair_revision,
        )
        return
    if any(
        event.type == "channel.synthesis.proposed"
        and isinstance(event.payload, dict)
        and str(event.payload.get("channel_id") or "") == channel_id
        and str(event.payload.get("thread_id") or "main") == thread_id
        and str(event.payload.get("request_id") or "")
        == synthesis_request_id
        for event in writer.event_log.read_all()
    ):
        return
    question_records, question_error = reply_question_records(
        synthesis,
        channel=channel,
        channel_id=channel_id,
        thread_id=thread_id,
        member_id=member_id or actor,
        source_kind="synthesis",
    )
    if question_error:
        _reject_synthesis_contract(
            state_dir=state_dir,
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            member_id=member_id,
            request=request,
            reply=reply,
            reply_event_id=reply_event_id,
            actor=actor,
            source=source,
            status="invalid_channel_synthesis_question_graph",
            reason=question_error,
            synthesis_request_id=synthesis_request_id,
            synthesis_repair_revision=synthesis_repair_revision,
        )
        return
    next_round, next_round_error = validate_synthesis_next_round(
        synthesis,
        channel=channel,
        thread_id=thread_id,
        question_records=question_records,
    )
    if next_round_error:
        _reject_synthesis_contract(
            state_dir=state_dir,
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            member_id=member_id,
            request=request,
            reply=reply,
            reply_event_id=reply_event_id,
            actor=actor,
            source=source,
            status="invalid_channel_synthesis_next_round",
            reason=next_round_error,
            synthesis_request_id=synthesis_request_id,
            synthesis_repair_revision=synthesis_repair_revision,
        )
        return
    summary = str(synthesis.get("summary") or reply).strip()
    safe_channel_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", channel_id)
    safe_request_id = re.sub(
        r"[^A-Za-z0-9_.-]+",
        "-",
        synthesis_request_id,
    )
    artifact_ref = (
        Path("channel-artifacts")
        / safe_channel_id
        / f"{safe_request_id}.md"
    )
    artifact_path = Path(state_dir) / artifact_ref
    source_refs = list(dict.fromkeys([
        *channel_prd_event_refs(channel, thread_id),
        *string_refs(synthesis.get("source_refs")),
        f"event:{reply_event_id}",
        f"channel:{channel_id}/{thread_id}",
    ]))
    evidence_refs = string_refs(synthesis.get("evidence_refs"))
    typed_synthesis = {
        **synthesis,
        "consumed_contribution_refs": declared_contribution_refs,
        "consumed_contribution_digests": declared_contribution_digests,
        "consumed_message_digests": coverage[
            "consumed_message_digests"
        ],
        "source_coverage": coverage["sources"],
        "semantic_source_manifest_digest": coverage[
            "manifest_digest"
        ],
        "next_round": next_round,
    }
    contract_descriptor = persist_channel_contract(
        state_dir,
        channel_id=channel_id,
        thread_id=thread_id,
        identity=synthesis_request_id,
        kind="synthesis",
        body=typed_synthesis,
        created_by=member_id or actor,
        source_event_id=reply_event_id,
        provenance={
            "source_refs": source_refs,
            "evidence_refs": evidence_refs,
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
            identity=synthesis_request_id,
            coverage=coverage,
            created_by=member_id or actor,
            source_event_id=reply_event_id,
        )
        if coverage["required"]
        else {}
    )
    artifact_body = render_channel_prd_artifact(
        channel=channel,
        channel_id=channel_id,
        thread_id=thread_id,
        source_requirement=channel_requirement_text(
            state_dir,
            channel,
            thread_id,
        ),
        synthesis=typed_synthesis,
        summary=summary,
        source_refs=source_refs,
    )
    atomic_write_text(artifact_path, artifact_body)
    revision = persist_synthesis_prd_revision(
        state_dir,
        channel=channel,
        channel_id=channel_id,
        thread_id=thread_id,
        member_id=member_id,
        actor=actor,
        reply_event_id=reply_event_id,
        synthesis=synthesis,
        typed_synthesis=typed_synthesis,
        summary=summary,
        artifact_ref=artifact_ref,
        artifact_body=artifact_body,
        source_refs=source_refs,
        evidence_refs=evidence_refs,
    )
    canonical_artifact_ref = str(revision["artifact_ref"])
    digest = str(revision["artifact_digest"])
    prd_revision = int(revision["prd_revision"])
    synthesis_event = writer.emit(
        "channel.synthesis.proposed",
        actor=member_id or actor,
        task_id=str(request.get("task_id") or "") or None,
        causation_id=reply_event_id,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": thread_id,
            "request_id": synthesis_request_id,
            "decision": str(synthesis.get("decision") or "draft"),
            "summary": summary,
            "open_questions": _reply_question_texts(synthesis),
            "risks": [
                _display_value(item)
                for item in typed_items(synthesis.get("risks"))
                if _display_value(item)
            ][:16],
            "recommended_workflow": (
                synthesis.get("recommended_workflow")
                if isinstance(synthesis.get("recommended_workflow"), dict)
                else {}
            ),
            **revision,
            "contract_ref": contract_descriptor["ref"],
            "contract_digest": contract_descriptor["sha256"],
            "source_refs": source_refs,
            "evidence_refs": evidence_refs,
            "consumed_contribution_refs": typed_synthesis[
                "consumed_contribution_refs"
            ],
            "consumed_contribution_digests": typed_synthesis[
                "consumed_contribution_digests"
            ],
            "consumed_message_digests": typed_synthesis[
                "consumed_message_digests"
            ],
            "source_coverage": typed_synthesis["source_coverage"],
            "semantic_source_manifest_digest": typed_synthesis[
                "semantic_source_manifest_digest"
            ],
            "semantic_coverage_ref": str(
                coverage_descriptor.get("ref") or ""
            ),
            "semantic_coverage_digest": str(
                coverage_descriptor.get("sha256") or ""
            ),
            "confidence": _display_value(synthesis.get("confidence")),
            "dissent": typed_items(synthesis.get("dissent")),
            "next_round": next_round,
            "source": source,
        },
    )
    if synthesis_repair_revision:
        writer.emit(
            "channel.synthesis.repair.completed",
            actor=member_id or actor,
            task_id=str(request.get("task_id") or "") or None,
            causation_id=synthesis_event.id,
            correlation_id=channel_id,
            payload={
                "schema_version": "channel.synthesis.repair.v1",
                "channel_id": channel_id,
                "thread_id": thread_id,
                "request_id": synthesis_request_id,
                "repair_id": synthesis_repair_id,
                "repair_revision": synthesis_repair_revision,
                "source_reply_event_id": reply_event_id,
                "source": source,
            },
        )
    if next_round["action"] == "continue":
        emit_synthesis_next_round_proposal(
            writer=writer,
            channel=channel,
            channel_id=channel_id,
            thread_id=thread_id,
            synthesis_event=synthesis_event,
            synthesis_request_id=synthesis_request_id,
            next_round=next_round,
            actor=member_id or actor,
            source=source,
            task_id=str(request.get("task_id") or ""),
        )
        return
    if question_records:
        for question_record in question_records:
            writer.emit(
                "channel.question.opened",
                actor=member_id or actor,
                task_id=str(request.get("task_id") or "") or None,
                causation_id=synthesis_event.id,
                correlation_id=channel_id,
                payload={
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    **question_record,
                    "source": source,
                },
            )
        return

    if any(
        isinstance(question, dict)
        and str(question.get("thread_id") or "main") == thread_id
        and str(question.get("status") or "") == "open"
        for question in channel.get("open_questions") or []
    ):
        return

    prior_consensus = any(
        event.type == "channel.consensus.proposed"
        and isinstance(event.payload, dict)
        and str(event.payload.get("channel_id") or "") == channel_id
        and str(event.payload.get("thread_id") or "main") == thread_id
        and str(event.payload.get("artifact_digest") or "") == digest
        for event in writer.event_log.read_all()
    )
    if prior_consensus:
        return
    product_mode, required_signers = (
        consensus_mode_and_required_signers(
            channel,
            thread_id=thread_id,
        )
    )
    if member_id and member_id not in required_signers:
        required_signers.append(member_id)
    proposed = writer.emit(
        "channel.consensus.proposed",
        actor=member_id or actor,
        task_id=str(request.get("task_id") or "") or None,
        causation_id=synthesis_event.id,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": thread_id,
            **revision,
            "owner_actor_ref": str(
                channel.get("owner_actor_ref") or ""
            ),
            "product_mode": product_mode,
            "proposed_by": member_id or actor,
            "required_signers": required_signers,
            "dissent": typed_items(synthesis.get("dissent")),
            "source_refs": source_refs,
            "source": source,
        },
    )
    if member_id:
        writer.emit(
            "channel.consensus.signed",
            actor=member_id,
            task_id=str(request.get("task_id") or "") or None,
            causation_id=proposed.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "member_id": member_id,
                "artifact_ref": canonical_artifact_ref,
                "artifact_digest": digest,
                "prd_revision": prd_revision,
                "source": source,
            },
        )


def _ignore_superseded_synthesis_reply(
    *,
    writer: EventWriter,
    channel: dict[str, Any],
    channel_id: str,
    thread_id: str,
    synthesis_request_id: str,
    reply_event_id: str,
    task_id: str,
    source: str,
) -> bool:
    """Record and ignore a late distinct synthesis for the current PRD."""
    discussions = channel.get("discussions")
    session = discussions.get(thread_id, {}) if isinstance(discussions, dict) else {}
    if (
        str(session.get("state") or "") == "phase2_relay"
        and str(session.get("phase_reason") or "")
        in {"synthesis_questions_opened", "consensus_blocked"}
    ):
        return False
    started_event_id = str(session.get("started_event_id") or "")
    started = not started_event_id
    prior = None
    events = writer.event_log.read_all()
    for event in events:
        if event.id == started_event_id:
            started = True
            continue
        if not started or event.type != "channel.synthesis.proposed":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            str(payload.get("channel_id") or "") == channel_id
            and str(payload.get("thread_id") or "main") == thread_id
            and str(payload.get("request_id") or "") != synthesis_request_id
        ):
            prior = event
    if prior is None:
        return False
    if any(
        event.type == "channel.synthesis.stale_ignored"
        and isinstance(event.payload, dict)
        and str(event.payload.get("source_reply_event_id") or "")
        == reply_event_id
        for event in events
    ):
        return True
    prior_payload = prior.payload if isinstance(prior.payload, dict) else {}
    writer.emit(
        "channel.synthesis.stale_ignored",
        actor="zf-kernel",
        task_id=task_id or None,
        causation_id=reply_event_id,
        correlation_id=channel_id,
        payload={
            "schema_version": "channel.synthesis.stale.v1",
            "channel_id": channel_id,
            "thread_id": thread_id,
            "request_id": synthesis_request_id,
            "superseded_by_request_id": str(
                prior_payload.get("request_id") or ""
            ),
            "canonical_artifact_ref": str(
                prior_payload.get("artifact_ref") or ""
            ),
            "canonical_artifact_digest": str(
                prior_payload.get("artifact_digest") or ""
            ),
            "source_reply_event_id": reply_event_id,
            "reason": "prior_synthesis_proposed",
            "source": source,
        },
    )
    return True


def _emit_contribution(
    *,
    state_dir: Path,
    writer: EventWriter,
    channel: dict[str, Any],
    channel_id: str,
    thread_id: str,
    member_id: str,
    request: dict[str, Any],
    reply: str,
    reply_event_id: str,
    actor: str,
    source: str,
) -> None:
    contribution = _structured_reply_payload(
        reply,
        "channel_contribution",
    )
    if not contribution:
        reject_contribution_contract(
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            member_id=member_id,
            request=request,
            reply=reply,
            reply_event_id=reply_event_id,
            actor=actor,
            source=source,
            status="invalid_missing_channel_contribution",
        )
        return
    validation_error = validate_channel_contract(
        contribution,
        kind="contribution",
    )
    if validation_error:
        reject_contribution_contract(
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            member_id=member_id,
            request=request,
            reply=reply,
            reply_event_id=reply_event_id,
            actor=actor,
            source=source,
            status="invalid_channel_contribution",
            reason=validation_error,
        )
        return
    question_records, question_error = reply_question_records(
        contribution,
        channel=channel,
        channel_id=channel_id,
        thread_id=thread_id,
        member_id=member_id or actor,
        source_kind="contribution",
    )
    if question_error:
        reject_contribution_contract(
            writer=writer,
            channel_id=channel_id,
            thread_id=thread_id,
            member_id=member_id,
            request=request,
            reply=reply,
            reply_event_id=reply_event_id,
            actor=actor,
            source=source,
            status="invalid_channel_contribution_question_graph",
            reason=question_error,
        )
        return
    summary = str(contribution.get("summary") or "").strip()
    source_refs = list(dict.fromkeys([
        *string_refs(contribution.get("source_refs")),
        f"event:{reply_event_id}",
    ]))
    evidence_refs = string_refs(contribution.get("evidence_refs"))
    request_identity = str(request.get("request_id") or "reply")
    event_identity = str(reply_event_id or "event").removeprefix("evt-")
    identity = f"{request_identity}-{event_identity}"
    descriptor = persist_channel_contract(
        state_dir,
        channel_id=channel_id,
        thread_id=thread_id,
        identity=identity,
        kind="contribution",
        body=contribution,
        created_by=member_id or actor,
        source_event_id=reply_event_id,
        provenance={
            "template": channel_template_binding(channel),
            "source_refs": source_refs,
            "evidence_refs": evidence_refs,
        },
    )
    source_manifest = persist_channel_source_manifest(
        state_dir,
        channel_id=channel_id,
        thread_id=thread_id,
        identity=identity,
        source_refs=source_refs,
        evidence_refs=evidence_refs,
        created_by=member_id or actor,
        source_event_id=reply_event_id,
    )
    writer.emit(
        "channel.finding.recorded",
        actor=member_id or actor,
        task_id=str(request.get("task_id") or "") or None,
        causation_id=reply_event_id,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": thread_id,
            "member_id": member_id,
            "summary": summary,
            "findings": typed_items(contribution.get("findings")),
            "contradictions": typed_items(
                contribution.get("contradictions")
            ),
            "risks": typed_items(contribution.get("risks")),
            "source_refs": source_refs,
            "evidence_refs": evidence_refs,
            "artifact_ref": descriptor["ref"],
            "artifact_digest": descriptor["sha256"],
            "source_manifest_ref": source_manifest["ref"],
            "source_manifest_digest": source_manifest["sha256"],
            "contract_status": "structured",
            "request_id": str(request.get("request_id") or ""),
            "message_id": str(request.get("message_id") or ""),
            "run_generation": int(request.get("run_generation") or 1),
            "source_reply_event_id": reply_event_id,
            "questions_frozen": contribution.get("freeze", True) is not False,
            "source": source,
        },
    )
    for index, question_record in enumerate(question_records, 1):
        writer.emit(
            "channel.question.opened",
            actor=member_id or actor,
            task_id=str(request.get("task_id") or "") or None,
            causation_id=reply_event_id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                **question_record,
                "ordinal": index,
                "source": source,
            },
        )
    if contribution.get("freeze", True) is not False:
        writer.emit(
            "channel.questions.frozen",
            actor=member_id or actor,
            task_id=str(request.get("task_id") or "") or None,
            causation_id=reply_event_id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "member_id": member_id,
                "source": source,
            },
        )


__all__ = [
    "channel_reply_response_contract",
    "emit_structured_reply_events",
]
