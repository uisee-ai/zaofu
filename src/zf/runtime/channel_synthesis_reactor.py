"""Kernel reactor for a requested Channel synthesis turn."""

from __future__ import annotations

import hashlib

from zf.core.events import ZfEvent
from zf.runtime.channel_adaptive_round_reactor import (
    react_channel_synthesis_next_round_proposed,
)
from zf.runtime.channel_router import route_channel_message
from zf.runtime.channel_sidecar import channel_message_event_payload
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_synthesis_lock import synthesis_request_in_flight


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
    channel = project_channel(host.state_dir, channel_id) or {}
    active = synthesis_request_in_flight(channel, str(payload.get("thread_id") or "main"))
    if not active or active.get("request_id") != request_id:
        # Starting a watcher must not silently retry a rejected or superseded request.
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
                "refs": {"synthesis_request_id": request_id, "prd_document_contract": "full.v1",
                         "prd_quality_contract": payload.get("prd_quality_contract", ""),
                         **({"prd_source_file": payload["prd_source_file"]} if payload.get("prd_source_file") else {}),
                         **({"prd_input_snapshot": payload["prd_input_snapshot"]} if payload.get("prd_input_snapshot") else {})},
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
    _route_runtime_message(host, event, message)


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
    from zf.runtime.channel_libre import is_libre
    repair_channel = project_channel(host.state_dir, channel_id) or {}
    libre = is_libre(repair_channel)
    if libre:
        active = synthesis_request_in_flight(repair_channel, thread_id)
        if not active or active.get("request_id") != synthesis_request_id:
            return
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
                "member_id": repair_channel.get("leader_member_id", "主持人") if libre else "operator",
                "role": "system" if libre else "user",
                "source": "runtime",
                "text": (
                    f"@{target_member_id} Correct synthesis contract revision "
                    f"{revision}. Return one complete channel_synthesis JSON "
                    "object; do not abbreviate or continue the old fragment. "
                    "For full.v1 + prd-spec.v1, return the complete standalone "
                    "prd_markdown. Do not use prd_composition for a conversation "
                    "analysis or incomplete draft; composition is allowed only "
                    "for an already complete formal PRD file source. Ensure the "
                    "outer JSON object is closed. Requirement coverage section "
                    "values must be exact Markdown heading titles, not requirement "
                    "ID lists. "
                    f"Contract diagnostic: {contract_error}. Invalid reply "
                    f"evidence: {invalid_reply_ref.get('ref') or 'unavailable'}."
                ),
                "mentions": [target_member_id],
                "refs": {
                    "synthesis_request_id": synthesis_request_id,
                    **({"libre_control_event_id": event.id, "display_origin": "host_coordination",
                        "display_text": "正在修复本次 PRD 的格式，仍采用原材料；你可以继续聊天。"} if libre else {}),
                    "synthesis_repair_id": repair_id,
                    "prd_document_contract": "full.v1",
                    **_synthesis_source_refs(host, synthesis_request_id, channel_id, thread_id),
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
    _route_runtime_message(host, event, message)


def react_channel_question_dedup_requested(
    host,
    event: ZfEvent,
) -> None:
    if (event.payload or {}).get("libre_round_id"):
        from zf.runtime.channel_libre import _post_control
        channel = project_channel(host.state_dir, event.payload["channel_id"]) or {}
        if (channel.get("libre_rounds", {}).get(event.payload["libre_round_id"]) or {}).get("publication"):
            return
        _post_control(host.state_dir, host.event_writer, channel, event,
                      text="正在整理本轮问题：只合并等价决策，保留不同场景、选项和独立子问。",
                      targets=[event.payload["target_member_id"]],
                      refs={"libre_round_id": event.payload["libre_round_id"],
                            "question_dedup_request_id": event.payload["request_id"],
                            "question_ledger_digest": event.payload["ledger_digest"]},
                      config=getattr(host, "config", None), project_root=getattr(host, "project_root", None))
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
    _route_runtime_message(host, event, message)


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
                    "cross_review_contract": "v2",
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
    if (project_channel(host.state_dir, channel_id) or {}).get("discussion", {}).get("mode") == "libre_discuss":
        return  # Explicit evaluation remains available; no automatic consensus loop.
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
    routed = route_channel_message(
        state_dir=host.state_dir,
        writer=host.event_writer,
        message_event=message,
        message_payload=message.payload,
        actor="orchestrator-reactor",
        source="runtime",
        project_root=getattr(host, "project_root", None),
        config=getattr(host, "config", None),
        openclaw_client=getattr(host, "openclaw_client", None),
        dispatch_inline=False,
    )
    if not routed.reply_requests:
        return
    from zf.runtime.channel_message_ingress import dispatch_channel_replies_background

    dispatch_channel_replies_background(
        state_dir=host.state_dir, channel_id=str(message.payload.get("channel_id") or ""),
        actor="orchestrator-reactor", source="runtime", project_root=getattr(host, "project_root", None),
        config=getattr(host, "config", None), openclaw_client=getattr(host, "openclaw_client", None), max_dispatch=len(routed.reply_requests),
    )


__all__ = [
    "react_channel_consensus_proposed",
    "react_channel_cross_review_requested",
    "react_channel_question_dedup_requested",
    "react_channel_synthesis_requested",
]


def _synthesis_source_refs(host, request_id: str, channel_id: str, thread_id: str) -> dict:
    for event in host.event_log.read_all():
        p = event.payload
        if (event.type == "channel.synthesis.requested" and p.get("request_id") == request_id
                and p.get("channel_id") == channel_id and str(p.get("thread_id") or "main") == thread_id):
            return {key: p[key] for key in ("prd_source_file", "prd_quality_contract", "prd_input_snapshot") if p.get(key)}
    return {}
