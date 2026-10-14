"""Durable, idempotent Channel message ingress."""

from __future__ import annotations

from pathlib import Path, PurePath
import threading
from typing import Any
from urllib.parse import urlparse

from zf.core.events import EventWriter, ZfEvent
from zf.core.events.factory import event_log_from_project
from zf.core.security.redaction import redact_obj
from zf.core.state.locks import locked_path
from zf.runtime.channel_adapter import dispatch_pending_replies
from zf.runtime.channel_dispatch_failure import (
    fail_pending_channel_replies_after_dispatch_failure,
)
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_router import (
    detect_channel_mention_tokens,
    resolve_channel_mentions,
    routable_backing_worker_member,
    route_channel_message,
)
from zf.runtime.channel_provider_preflight import (
    normalize_channel_dispatch_policy,
)
from zf.runtime.channel_sidecar import channel_message_event_payload
from zf.runtime.control_actions_helpers import (
    _normal_channel_id,
    _optional_str,
    _required_text,
    _safe_int,
    _stable_control_id,
    _string_list,
    _task_id_from_payload,
)


_MAX_CHANNEL_ATTACHMENTS = 8
_MAX_CHANNEL_ATTACHMENT_BYTES = 10 * 1024 * 1024
_FORBIDDEN_ATTACHMENT_BODY_KEYS = {
    "base64",
    "body",
    "bytes_body",
    "content",
    "data",
    "secret",
    "token",
}
_FORBIDDEN_ATTACHMENT_MIME = {
    "application/x-dosexec",
    "application/x-executable",
    "application/x-msdownload",
    "application/x-sh",
}


def route_reactor_message_after_ingress(
    runtime: Any,
    *,
    event: ZfEvent,
    payload: dict[str, Any],
) -> None:
    """Serialize reactor routing behind the controlled ingress owner.

    Web/Feishu ingress appends ``channel.message.posted`` while holding this
    lock, then materializes stable reply requests. EventWatcher must wait for
    that critical section before projecting the channel, otherwise both paths
    can route the same message from different snapshots.
    """
    channel_id = str(payload.get("channel_id") or "")
    thread_id = str(payload.get("thread_id") or "main") or "main"
    lock_id = _stable_control_id("channel-ingress", channel_id, thread_id)
    with locked_path(runtime.state_dir / "locks" / lock_id):
        route_channel_message(
            state_dir=runtime.state_dir,
            writer=runtime.event_writer,
            message_event=event,
            message_payload=payload,
            actor="orchestrator-reactor",
            source="runtime",
            project_root=getattr(runtime, "project_root", None),
            config=getattr(runtime, "config", None),
            openclaw_client=getattr(runtime, "openclaw_client", None),
        )


def execute_channel_message_ingress(
    service: Any,
    *,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict,
    emit_completion: bool = True,
) -> dict:
    channel_id = _normal_channel_id(_required_text(payload, "channel_id"))
    thread_id = _optional_str(payload.get("thread_id")) or "main"
    client_message_id = (
        _optional_str(payload.get("client_message_id"))
        or _optional_str(payload.get("message_id"))
        or f"client-{requested.id.removeprefix('evt-')}"
    )
    idempotency_key = (
        _optional_str(payload.get("idempotency_key"))
        or client_message_id
    )
    message_id = (
        _optional_str(payload.get("message_id"))
        or f"msg-{client_message_id.removeprefix('client-')}"
    )
    lock_id = _stable_control_id(
        "channel-ingress",
        channel_id,
        thread_id,
    )
    with locked_path(service.state_dir / "locks" / lock_id):
        prior = _accepted_ingress_event(
            service.writer,
            channel_id=channel_id,
            thread_id=thread_id,
            client_message_id=client_message_id,
            idempotency_key=idempotency_key,
        )
        if prior is not None:
            return _duplicate_ingress_result(
                prior=prior,
                action=action,
                requested_action=requested_action,
                channel_id=channel_id,
                thread_id=thread_id,
                message_id=message_id,
                client_message_id=client_message_id,
                idempotency_key=idempotency_key,
            )
        return _post_channel_message_once(
            service,
            requested=requested,
            action=action,
            requested_action=requested_action,
            payload={
                **payload,
                "channel_id": channel_id,
                "thread_id": thread_id,
                "message_id": message_id,
                "client_message_id": client_message_id,
                "idempotency_key": idempotency_key,
            },
            emit_completion=emit_completion,
        )


def _accepted_ingress_event(
    writer: EventWriter,
    *,
    channel_id: str,
    thread_id: str,
    client_message_id: str,
    idempotency_key: str,
) -> ZfEvent | None:
    for event in reversed(writer.event_log.read_all()):
        if event.type != "channel.message.posted":
            continue
        event_payload = (
            event.payload if isinstance(event.payload, dict) else {}
        )
        if str(event_payload.get("channel_id") or "") != channel_id:
            continue
        if str(event_payload.get("thread_id") or "main") != thread_id:
            continue
        if (
            str(event_payload.get("client_message_id") or "")
            == client_message_id
            or str(event_payload.get("idempotency_key") or "")
            == idempotency_key
        ):
            return event
    return None


def _duplicate_ingress_result(
    *,
    prior: ZfEvent,
    action: str,
    requested_action: str,
    channel_id: str,
    thread_id: str,
    message_id: str,
    client_message_id: str,
    idempotency_key: str,
) -> dict:
    prior_message_id = str(prior.payload.get("message_id") or message_id)
    return {
        "_status_code": 200,
        "ok": True,
        "status": "posted",
        "action": action,
        "requested_action": requested_action,
        "channel_id": channel_id,
        "thread_id": thread_id,
        "message_id": prior_message_id,
        "client_message_id": client_message_id,
        "idempotency_key": idempotency_key,
        "request_id": prior_message_id,
        "event_id": prior.id,
        "duplicate": True,
        "receipt": {
            "schema_version": "channel.message.ingress_receipt.v1",
            "status": "accepted",
            "event_id": prior.id,
            "message_id": prior_message_id,
            "client_message_id": client_message_id,
            "idempotency_key": idempotency_key,
            "duplicate": True,
        },
        "attachment_event_ids": [],
        "target_count": 0,
        "reply_request_count": 0,
        "queued_count": 0,
        "route": {"targets": [], "reply_requests": [], "queued": []},
    }


def _post_channel_message_once(
    service: Any,
    *,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict,
    emit_completion: bool,
) -> dict:
    channel_id = _normal_channel_id(_required_text(payload, "channel_id"))
    thread_id = _optional_str(payload.get("thread_id")) or "main"
    message_id = _required_text(payload, "message_id")
    client_message_id = _required_text(payload, "client_message_id")
    idempotency_key = _required_text(payload, "idempotency_key")
    member_id = _optional_str(payload.get("member_id")) or "operator"
    text = str(payload.get("text") or payload.get("message") or "")
    role = _optional_str(payload.get("role")) or "user"
    reply_to_message_id = (
        _optional_str(payload.get("reply_to_message_id")) or ""
    )
    source_refs = (
        payload.get("refs")
        if isinstance(payload.get("refs"), dict)
        else {}
    )
    channel = project_channel(service.state_dir, channel_id) or {}
    ingress_error = _validate_ingress_message(
        channel=channel,
        thread_id=thread_id,
        member_id=member_id,
        actor=service.actor,
        surface=service.surface,
        role=role,
        reply_to_message_id=reply_to_message_id,
        refs=source_refs,
    )
    if not ingress_error:
        from zf.runtime.channel_attachments import URI_PREFIX, read_channel_attachment

        attachments = source_refs.get("attachments")
        for attachment in attachments if isinstance(attachments, list) else []:
            uri = str(attachment.get("uri") or "")
            try:
                if uri.startswith(URI_PREFIX):
                    body, _ = read_channel_attachment(service.state_dir, attachment, channel_id=channel_id, thread_id=thread_id)
                    attachment["body_ref"] = body["body_ref"]
                    attachment["attachment_id"] = "att-" + uri[len(URI_PREFIX):]
                elif attachment.get("source") == "browser-file-picker":
                    raise ValueError("Attachment has no stored content; please upload it again")
            except ValueError as exc:
                ingress_error = str(exc)
                break
    if ingress_error:
        return service._failed(
            requested=requested,
            action=action,
            requested_action=requested_action,
            task_id=_task_id_from_payload(payload),
            reason=ingress_error,
            status_code=422,
            status="message_rejected",
            extra=_rejected_ingress_extra(
                channel_id=channel_id,
                thread_id=thread_id,
                client_message_id=client_message_id,
                idempotency_key=idempotency_key,
                reason=ingress_error,
            ),
        )
    explicit_mentions = _string_list(payload.get("mentions"))
    resolved_mentions = resolve_channel_mentions(
        channel,
        text=text,
        explicit_mentions=explicit_mentions,
        sender_member_id=member_id,
    )
    mention_tokens = detect_channel_mention_tokens(
        text,
        explicit_mentions=explicit_mentions,
    )
    mentions = resolved_mentions or explicit_mentions
    try:
        posted_payload = channel_message_event_payload(
            service.state_dir,
            {
                "channel_id": channel_id,
                "thread_id": thread_id,
                "message_id": message_id,
                "client_message_id": client_message_id,
                "idempotency_key": idempotency_key,
                "reply_to_message_id": reply_to_message_id,
                "member_id": member_id,
                "role": role,
                "source": service.surface,
                "text": text,
                "mentions": mentions,
                "mention_tokens": mention_tokens,
                "refs": source_refs,
            },
            created_by=f"channel-message:{service.surface}",
        )
        event = service.writer.emit(
            "channel.message.posted",
            actor=service.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=channel_id,
            payload=posted_payload,
        )
    except Exception as exc:
        reason = f"Channel message was not durably accepted: {exc}"
        return service._failed(
            requested=requested,
            action=action,
            requested_action=requested_action,
            task_id=_task_id_from_payload(payload),
            reason=reason,
            status_code=503,
            status="message_rejected",
            extra=_rejected_ingress_extra(
                channel_id=channel_id,
                thread_id=thread_id,
                client_message_id=client_message_id,
                idempotency_key=idempotency_key,
                reason=reason,
            ),
        )
    attachment_event_ids = _emit_attachment_events(
        service,
        payload=payload,
        posted_payload=posted_payload,
        event=event,
        channel_id=channel_id,
        thread_id=thread_id,
        message_id=message_id,
        member_id=member_id,
    )
    route_result = route_channel_message(
        state_dir=service.state_dir,
        writer=service.writer,
        message_event=event,
        message_payload={
            **posted_payload,
            "task_id": str(payload.get("task_id") or ""),
        },
        actor=service.actor,
        source=service.surface,
        project_root=service.project_root,
        config=service.config,
        openclaw_client=service.openclaw_client,
        dispatch_inline=False,
    )
    dispatch_policy = normalize_channel_dispatch_policy(
        source_refs.get("dispatch_policy")
    )
    if route_result.reply_requests and dispatch_policy == "auto":
        dispatch_channel_replies_background(
            state_dir=service.state_dir,
            channel_id=channel_id,
            actor=service.actor,
            source=service.surface,
            project_root=service.project_root,
            config=service.config,
            openclaw_client=service.openclaw_client,
            max_dispatch=max(1, len(route_result.reply_requests)),
        )
    _emit_direct_worker_delivery(
        service,
        payload=payload,
        event=event,
        channel_id=channel_id,
        thread_id=thread_id,
        message_id=message_id,
        member_id=member_id,
        text=text,
    )
    if emit_completion:
        service._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="posted",
            task_id=_task_id_from_payload(payload),
            extra={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "message_id": message_id,
                "dispatch_policy": dispatch_policy,
                "reply_dispatch": (
                    "started"
                    if dispatch_policy == "auto"
                    else "deferred"
                ),
            },
        )
    return {
        "_status_code": 202,
        "ok": True,
        "status": "posted",
        "action": action,
        "requested_action": requested_action,
        "channel_id": channel_id,
        "thread_id": thread_id,
        "message_id": message_id,
        "client_message_id": client_message_id,
        "idempotency_key": idempotency_key,
        "request_id": message_id,
        "event_id": event.id,
        "duplicate": False,
        "receipt": {
            "schema_version": "channel.message.ingress_receipt.v1",
            "status": "accepted",
            "event_id": event.id,
            "message_id": message_id,
            "client_message_id": client_message_id,
            "idempotency_key": idempotency_key,
            "duplicate": False,
        },
        "attachment_event_ids": attachment_event_ids,
        "target_count": len(route_result.targets),
        "reply_request_count": len(route_result.reply_requests),
        "queued_count": len(route_result.queued),
        "dispatch_policy": dispatch_policy,
        "reply_dispatch": (
            "started" if dispatch_policy == "auto" else "deferred"
        ),
        "route": route_result.as_dict(),
    }


def _rejected_ingress_extra(
    *,
    channel_id: str,
    thread_id: str,
    client_message_id: str,
    idempotency_key: str,
    reason: str,
) -> dict:
    return {
        "channel_id": channel_id,
        "thread_id": thread_id,
        "client_message_id": client_message_id,
        "idempotency_key": idempotency_key,
        "receipt": {
            "schema_version": "channel.message.ingress_receipt.v1",
            "status": "rejected",
            "client_message_id": client_message_id,
            "idempotency_key": idempotency_key,
            "reason": reason,
        },
    }


def _emit_attachment_events(
    service: Any,
    *,
    payload: dict,
    posted_payload: dict,
    event: ZfEvent,
    channel_id: str,
    thread_id: str,
    message_id: str,
    member_id: str,
) -> list[str]:
    refs = (
        posted_payload["refs"]
        if isinstance(posted_payload.get("refs"), dict)
        else {}
    )
    attachments = (
        refs.get("attachments")
        if isinstance(refs.get("attachments"), list)
        else []
    )
    event_ids: list[str] = []
    for index, attachment in enumerate(attachments):
        if not isinstance(attachment, dict):
            continue
        from zf.runtime.channel_attachments import URI_PREFIX
        if not str(attachment.get("uri") or "").startswith(URI_PREFIX):
            continue
        attachment_id = (
            _optional_str(attachment.get("attachment_id"))
            or _optional_str(attachment.get("id"))
            or f"att-{message_id}-{index + 1}"
        )
        uploaded = service.writer.emit(
            "channel.attachment.uploaded",
            actor=service.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=event.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "attachment_id": attachment_id,
                "message_id": message_id,
                "member_id": member_id,
                "name": _optional_str(
                    attachment.get("name") or attachment.get("filename")
                )
                or "",
                "mime": _optional_str(
                    attachment.get("mime")
                    or attachment.get("type")
                    or attachment.get("content_type")
                )
                or "",
                "size": _safe_int(
                    attachment.get("size")
                    if attachment.get("size") is not None
                    else attachment.get("bytes")
                ),
                "hash": _optional_str(
                    attachment.get("hash") or attachment.get("sha256")
                )
                or "",
                "uri": _optional_str(attachment.get("uri")) or "",
                "body_ref": attachment.get("body_ref") or {},
                "refs": redact_obj({
                    "source": (
                        attachment.get("source")
                        if isinstance(attachment.get("source"), str)
                        else ""
                    ),
                    "lastModified": attachment.get("lastModified"),
                }),
                "source": service.surface,
            },
        )
        event_ids.append(uploaded.id)
    return event_ids


def _emit_direct_worker_delivery(
    service: Any,
    *,
    payload: dict,
    event: ZfEvent,
    channel_id: str,
    thread_id: str,
    message_id: str,
    member_id: str,
    text: str,
) -> None:
    instance_id = str(
        payload.get("instance_id")
        or payload.get("worker")
        or payload.get("backing_worker_session_id")
        or ""
    ).strip()
    if not instance_id:
        return
    target_member = routable_backing_worker_member(
        project_channel(service.state_dir, channel_id) or {},
        instance_id,
        sender_member_id=member_id,
    )
    if target_member is None:
        service.writer.emit(
            "channel.route.blocked",
            actor=service.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=event.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "message_id": message_id,
                "member_id": member_id,
                "instance_id": instance_id,
                "reason": "worker_not_channel_member",
                "source": service.surface,
            },
        )
        return
    service.writer.emit(
        "worker.reply.requested",
        actor=service.actor,
        task_id=_task_id_from_payload(payload),
        causation_id=event.id,
        correlation_id=channel_id,
        payload={
            "instance_id": instance_id,
            "message": text,
            "task_id": str(payload.get("task_id") or ""),
            "channel_id": channel_id,
            "thread_id": thread_id,
            "message_id": message_id,
        },
    )
    service.writer.emit(
        "channel.message.delivered",
        actor=service.actor,
        task_id=_task_id_from_payload(payload),
        causation_id=event.id,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": thread_id,
            "message_id": message_id,
            "member_id": str(payload.get("member_id") or instance_id),
            "worker_session_id": instance_id,
            "source": service.surface,
        },
    )


def dispatch_channel_replies_background(
    *,
    state_dir: Path,
    channel_id: str,
    actor: str,
    source: str,
    project_root: Path,
    config: Any,
    openclaw_client: Any,
    max_dispatch: int,
) -> None:
    candidates = (project_channel(Path(state_dir), channel_id) or {}).get("reply_requests") or []
    writer = EventWriter(event_log_from_project(state_dir, config=config))

    def fail(exc: Exception) -> None:
        fail_pending_channel_replies_after_dispatch_failure(
            state_dir=state_dir, writer=writer, channel_id=channel_id,
            actor=actor, source=f"{source}:background", candidates=candidates,
            reason=f"background dispatch crashed: {exc}",
        )

    def run() -> None:
        try:
            dispatch_pending_replies(
                state_dir=state_dir,
                writer=writer,
                channel_id=channel_id,
                actor=actor,
                source=f"{source}:background",
                max_dispatch=max_dispatch,
                allow_queued=True,
                project_root=project_root,
                config=config,
                openclaw_client=openclaw_client,
            )
        except Exception as exc:
            fail(exc)

    thread = threading.Thread(
        target=run,
        name=f"zf-channel-dispatch-{channel_id}",
        daemon=True,
    )
    try:
        thread.start()
    except RuntimeError as exc:
        fail(exc)


def _validate_ingress_message(
    *,
    channel: dict,
    thread_id: str,
    member_id: str,
    actor: str,
    surface: str,
    role: str,
    reply_to_message_id: str,
    refs: dict,
) -> str:
    if "evaluation_source_document" in refs:
        owner = str(channel.get("owner_actor_ref") or "")
        effective_actor = "owner:operator" if surface == "web" and owner == "owner:operator" else actor
        if refs["evaluation_source_document"] is not True or role != "user" or effective_actor != owner:
            return "only the Channel Owner may mark an original evaluation source"
    members = [
        item
        for item in channel.get("members") or []
        if isinstance(item, dict)
    ]
    if members and member_id not in {"operator", "owner:operator"}:
        member = next(
            (
                item
                for item in members
                if str(item.get("member_id") or "") == member_id
            ),
            None,
        )
        if member is None:
            return "sender is not an active Channel member"
        if str(member.get("status") or "active").lower() in {
            "failed",
            "rejected",
            "removed",
            "suspended",
        }:
            return "sender Channel membership is inactive"
        permissions = {
            str(value) for value in member.get("permissions") or []
        }
        if permissions and "message" not in permissions:
            return "sender lacks Channel message permission"
    if reply_to_message_id:
        reply_target = next(
            (
                item
                for item in (
                    channel.get("messages")
                    or channel.get("recent_messages")
                    or []
                )
                if isinstance(item, dict)
                and str(item.get("message_id") or "")
                == reply_to_message_id
                and str(item.get("thread_id") or "main") == thread_id
            ),
            None,
        )
        if reply_target is None:
            return "reply target does not exist in the same Channel thread"
    attachments = (
        refs.get("attachments")
        if isinstance(refs.get("attachments"), list)
        else []
    )
    if len(attachments) > _MAX_CHANNEL_ATTACHMENTS:
        return (
            f"at most {_MAX_CHANNEL_ATTACHMENTS} attachments are allowed "
            "per Channel message"
        )
    for item in attachments:
        error = _validate_ingress_attachment(item)
        if error:
            return error
    return ""


def _validate_ingress_attachment(value: object) -> str:
    if not isinstance(value, dict):
        return "Channel attachment must be an object"
    if _FORBIDDEN_ATTACHMENT_BODY_KEYS.intersection(value):
        return "Channel attachment must contain metadata/ref only, not raw body"
    name = str(value.get("name") or value.get("filename") or "").strip()
    if not name or len(name) > 255:
        return "Channel attachment name must be 1-255 characters"
    if (
        PurePath(name).name != name
        or "/" in name
        or "\\" in name
        or ".." in name
    ):
        return "Channel attachment name must not contain a path"
    try:
        size = int(
            value.get("size")
            if value.get("size") is not None
            else value.get("bytes") or 0
        )
    except (TypeError, ValueError):
        return "Channel attachment size must be an integer"
    if size < 0 or size > _MAX_CHANNEL_ATTACHMENT_BYTES:
        return (
            "Channel attachment exceeds the 10 MiB metadata ingress limit"
        )
    mime = str(
        value.get("mime")
        or value.get("type")
        or value.get("content_type")
        or ""
    ).strip().lower()
    if mime in _FORBIDDEN_ATTACHMENT_MIME:
        return f"Channel attachment MIME {mime!r} is not allowed"
    uri = str(value.get("uri") or "").strip()
    if uri:
        parsed = urlparse(uri)
        if parsed.scheme not in {"artifact", "channel", "https"}:
            return (
                "Channel attachment URI must use artifact, channel, or https"
            )
    return ""
