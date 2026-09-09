"""Kernel reactor for a requested Channel synthesis turn."""

from __future__ import annotations

import hashlib

from zf.core.events import ZfEvent
from zf.runtime.channel_adaptive_round_reactor import (
    react_channel_synthesis_next_round_proposed,
)
from zf.runtime.channel_router import route_channel_message
from zf.runtime.channel_sidecar import channel_message_event_payload


def react_channel_synthesis_requested(
    host,
    event: ZfEvent,
) -> None:
    if event.type == "channel.discussion.next_round.proposed":
        react_channel_synthesis_next_round_proposed(host, event)
        return
    payload = event.payload if isinstance(event.payload, dict) else {}
    channel_id = str(
        payload.get("channel_id") or event.correlation_id or ""
    )
    request_id = str(payload.get("request_id") or "")
    target_member_id = str(payload.get("target_member_id") or "")
    if not channel_id or not request_id or not target_member_id:
        return
    message = None
    for prior in host.event_log.read_all():
        prior_payload = (
            prior.payload if isinstance(prior.payload, dict) else {}
        )
        refs = (
            prior_payload.get("refs")
            if isinstance(prior_payload.get("refs"), dict)
            else {}
        )
        if (
            prior.type == "channel.message.posted"
            and str(refs.get("synthesis_request_id") or "") == request_id
        ):
            message = prior
            break
    thread_id = str(payload.get("thread_id") or "main")
    if message is None:
        prompt = str(
            payload.get("prompt")
            or "Synthesize this discussion into a decision, open questions, "
            "risks, and a recommended workflow."
        )
        message_payload = channel_message_event_payload(
            host.state_dir,
            {
                "channel_id": channel_id,
                "thread_id": thread_id,
                "message_id": f"msg-{request_id}",
                "member_id": "operator",
                "role": "user",
                "source": "runtime",
                "text": f"@{target_member_id} {prompt}",
                "mentions": [target_member_id],
                "refs": {"synthesis_request_id": request_id},
            },
            created_by="channel-synthesis:runtime",
            source_event_id=event.id,
        )
        message = host.event_writer.emit(
            "channel.message.posted",
            actor="orchestrator-reactor",
            task_id=event.task_id,
            causation_id=event.id,
            correlation_id=channel_id,
            payload=message_payload,
        )
    message_id = str((message.payload or {}).get("message_id") or "")
    for prior in host.event_log.read_all():
        prior_payload = (
            prior.payload if isinstance(prior.payload, dict) else {}
        )
        if (
            prior.type == "channel.agent.reply.requested"
            and str(prior_payload.get("message_id") or "") == message_id
        ):
            return
    route_channel_message(
        state_dir=host.state_dir,
        writer=host.event_writer,
        message_event=message,
        message_payload=message.payload,
        actor="orchestrator-reactor",
        source="runtime",
        project_root=getattr(host, "project_root", None),
        config=getattr(host, "config", None),
        openclaw_client=getattr(host, "openclaw_client", None),
        dispatch_inline=True,
    )


def react_channel_synthesis_repair_requested(
    host,
    event: ZfEvent,
) -> None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    channel_id = str(
        payload.get("channel_id") or event.correlation_id or ""
    )
    synthesis_request_id = str(payload.get("request_id") or "")
    repair_id = str(payload.get("repair_id") or "")
    target_member_id = str(payload.get("target_member_id") or "")
    if not channel_id or not synthesis_request_id or not repair_id or not target_member_id:
        return
    message = _message_for_ref(
        host,
        key="synthesis_repair_id",
        value=repair_id,
    )
    thread_id = str(payload.get("thread_id") or "main")
    if message is None:
        revision = int(payload.get("repair_revision") or 0)
        contract_error = str(payload.get("contract_error") or "").strip()
        invalid_reply_ref = (
            payload.get("invalid_reply_ref")
            if isinstance(payload.get("invalid_reply_ref"), dict)
            else {}
        )
        message_payload = channel_message_event_payload(
            host.state_dir,
            {
                "channel_id": channel_id,
                "thread_id": thread_id,
                "message_id": f"msg-{repair_id}",
                "member_id": "operator",
                "role": "user",
                "source": "runtime",
                "text": (
                    f"@{target_member_id} Correct synthesis contract revision "
                    f"{revision}. Return one complete channel_synthesis JSON "
                    "object; do not abbreviate or continue the old fragment. "
                    f"Contract diagnostic: {contract_error}. Invalid reply "
                    f"evidence: {invalid_reply_ref.get('ref') or 'unavailable'}."
                ),
                "mentions": [target_member_id],
                "refs": {
                    "synthesis_request_id": synthesis_request_id,
                    "synthesis_repair_id": repair_id,
                    "synthesis_repair_revision": revision,
                    "synthesis_invalid_reply": invalid_reply_ref,
                    "synthesis_contract_error": contract_error,
                },
            },
            created_by="channel-synthesis-repair:runtime",
            source_event_id=event.id,
        )
        message = host.event_writer.emit(
            "channel.message.posted",
            actor="orchestrator-reactor",
            task_id=event.task_id,
            causation_id=event.id,
            correlation_id=channel_id,
            payload=message_payload,
        )
    message_id = str((message.payload or {}).get("message_id") or "")
    for prior in host.event_log.read_all():
        prior_payload = (
            prior.payload if isinstance(prior.payload, dict) else {}
        )
        if (
            prior.type == "channel.agent.reply.requested"
            and str(prior_payload.get("message_id") or "") == message_id
        ):
            return
    route_channel_message(
        state_dir=host.state_dir,
        writer=host.event_writer,
        message_event=message,
        message_payload=message.payload,
        actor="orchestrator-reactor",
        source="runtime",
        project_root=getattr(host, "project_root", None),
        config=getattr(host, "config", None),
        openclaw_client=getattr(host, "openclaw_client", None),
        dispatch_inline=True,
    )


def react_channel_question_dedup_requested(
    host,
    event: ZfEvent,
) -> None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    channel_id = str(
        payload.get("channel_id") or event.correlation_id or ""
    )
    request_id = str(payload.get("request_id") or "")
    target_member_id = str(payload.get("target_member_id") or "")
    if not channel_id or not request_id or not target_member_id:
        return
    message = None
    for prior in host.event_log.read_all():
        prior_payload = (
            prior.payload if isinstance(prior.payload, dict) else {}
        )
        refs = (
            prior_payload.get("refs")
            if isinstance(prior_payload.get("refs"), dict)
            else {}
        )
        if (
            prior.type == "channel.message.posted"
            and str(refs.get("question_dedup_request_id") or "")
            == request_id
        ):
            message = prior
            break
    thread_id = str(payload.get("thread_id") or "main")
    if message is None:
        repair_reason = str(payload.get("repair_reason") or "").strip()
        repair_instruction = (
            f" The prior merge plan was rejected: {repair_reason}. Repair "
            "that contract error against the current ledger."
            if repair_reason
            else ""
        )
        message_payload = channel_message_event_payload(
            host.state_dir,
            {
                "channel_id": channel_id,
                "thread_id": thread_id,
                "message_id": f"msg-{request_id}",
                "member_id": "operator",
                "role": "user",
                "source": "runtime",
                "text": (
                    f"@{target_member_id} Deduplicate the complete question "
                    "ledger in the bound context pack. Preserve the strongest "
                    "canonical question in each semantic group."
                    f"{repair_instruction}"
                ),
                "mentions": [target_member_id],
                "refs": {
                    "question_dedup_request_id": request_id,
                    "question_ledger_digest": str(
                        payload.get("ledger_digest") or ""
                    ),
                    "question_dedup_generation": int(
                        payload.get("generation") or 1
                    ),
                    "question_dedup_prior_request_id": str(
                        payload.get("prior_request_id") or ""
                    ),
                    "question_dedup_repair_reason": repair_reason,
                },
            },
            created_by="channel-question-dedup:runtime",
            source_event_id=event.id,
        )
        message = host.event_writer.emit(
            "channel.message.posted",
            actor="orchestrator-reactor",
            task_id=event.task_id,
            causation_id=event.id,
            correlation_id=channel_id,
            payload=message_payload,
        )
    message_id = str((message.payload or {}).get("message_id") or "")
    for prior in host.event_log.read_all():
        prior_payload = (
            prior.payload if isinstance(prior.payload, dict) else {}
        )
        if (
            prior.type == "channel.agent.reply.requested"
            and str(prior_payload.get("message_id") or "") == message_id
        ):
            return
    route_channel_message(
        state_dir=host.state_dir,
        writer=host.event_writer,
        message_event=message,
        message_payload=message.payload,
        actor="orchestrator-reactor",
        source="runtime",
        project_root=getattr(host, "project_root", None),
        config=getattr(host, "config", None),
        openclaw_client=getattr(host, "openclaw_client", None),
        dispatch_inline=True,
    )


def react_channel_cross_review_requested(
    host,
    event: ZfEvent,
) -> None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    channel_id = str(
        payload.get("channel_id") or event.correlation_id or ""
    )
    request_id = str(payload.get("request_id") or "")
    target_member_id = str(payload.get("target_member_id") or "")
    if not channel_id or not request_id or not target_member_id:
        return
    thread_id = str(payload.get("thread_id") or "main")
    message = _message_for_ref(
        host,
        key="cross_review_request_id",
        value=request_id,
    )
    if message is None:
        prompt = str(payload.get("prompt") or "").strip()
        message_payload = channel_message_event_payload(
            host.state_dir,
            {
                "channel_id": channel_id,
                "thread_id": thread_id,
                "message_id": f"msg-{request_id}",
                "member_id": "operator",
                "role": "user",
                "source": "runtime",
                "text": f"@{target_member_id} {prompt}",
                "mentions": [target_member_id],
                "refs": {
                    "cross_review_request_id": request_id,
                    "question_id": str(payload.get("question_id") or ""),
                    "dedup_request_id": str(
                        payload.get("dedup_request_id") or ""
                    ),
                },
            },
            created_by="channel-cross-review:runtime",
            source_event_id=event.id,
        )
        message = host.event_writer.emit(
            "channel.message.posted",
            actor="orchestrator-reactor",
            task_id=event.task_id,
            causation_id=event.id,
            correlation_id=channel_id,
            payload=message_payload,
        )
    _route_runtime_message(host, event, message)


def react_channel_consensus_proposed(
    host,
    event: ZfEvent,
) -> None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    channel_id = str(
        payload.get("channel_id") or event.correlation_id or ""
    )
    thread_id = str(payload.get("thread_id") or "main")
    proposer = str(payload.get("proposed_by") or event.actor or "")
    required_signers = list(dict.fromkeys(
        str(member_id).strip()
        for member_id in payload.get("required_signers") or []
        if str(member_id).strip()
    ))
    if not channel_id:
        return
    for target_member_id in required_signers:
        if target_member_id == proposer:
            continue
        digest = hashlib.sha1(
            (
                f"{event.id}:{target_member_id}:consensus-review"
            ).encode("utf-8")
        ).hexdigest()[:16]
        review_id = f"creview-{digest}"
        message = _message_for_ref(
            host,
            key="consensus_review_id",
            value=review_id,
        )
        if message is None:
            message_payload = channel_message_event_payload(
                host.state_dir,
                {
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "message_id": f"msg-{review_id}",
                    "member_id": "operator",
                    "role": "user",
                    "source": "runtime",
                    "text": (
                        f"@{target_member_id} Review the exact synthesis "
                        "artifact. Sign only if your lens is preserved; "
                        "otherwise return one material blocker."
                    ),
                    "mentions": [target_member_id],
                    "refs": {
                        "consensus_review_id": review_id,
                        "consensus_event_id": event.id,
                        "artifact_ref": str(
                            payload.get("artifact_ref") or ""
                        ),
                        "artifact_digest": str(
                            payload.get("artifact_digest") or ""
                        ),
                    },
                },
                created_by="channel-consensus-review:runtime",
                source_event_id=event.id,
            )
            message = host.event_writer.emit(
                "channel.message.posted",
                actor="orchestrator-reactor",
                task_id=event.task_id,
                causation_id=event.id,
                correlation_id=channel_id,
                payload=message_payload,
            )
        _route_runtime_message(host, event, message)


def _message_for_ref(host, *, key: str, value: str):
    for prior in host.event_log.read_all():
        prior_payload = (
            prior.payload if isinstance(prior.payload, dict) else {}
        )
        refs = (
            prior_payload.get("refs")
            if isinstance(prior_payload.get("refs"), dict)
            else {}
        )
        if (
            prior.type == "channel.message.posted"
            and str(refs.get(key) or "") == value
        ):
            return prior
    return None


def _route_runtime_message(host, event: ZfEvent, message: ZfEvent) -> None:
    message_id = str((message.payload or {}).get("message_id") or "")
    for prior in host.event_log.read_all():
        prior_payload = (
            prior.payload if isinstance(prior.payload, dict) else {}
        )
        if (
            prior.type == "channel.agent.reply.requested"
            and str(prior_payload.get("message_id") or "") == message_id
        ):
            return
    route_channel_message(
        state_dir=host.state_dir,
        writer=host.event_writer,
        message_event=message,
        message_payload=message.payload,
        actor="orchestrator-reactor",
        source="runtime",
        project_root=getattr(host, "project_root", None),
        config=getattr(host, "config", None),
        openclaw_client=getattr(host, "openclaw_client", None),
        dispatch_inline=True,
    )


__all__ = [
    "react_channel_consensus_proposed",
    "react_channel_cross_review_requested",
    "react_channel_question_dedup_requested",
    "react_channel_synthesis_requested",
]
