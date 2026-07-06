"""ChannelMessageActionsMixin — controlled-action handlers (moved verbatim from control_actions.py)."""
from __future__ import annotations

import threading

from zf.core.events import EventWriter, ZfEvent
from zf.core.events.factory import event_log_from_project
from zf.core.security.redaction import redact_obj
from zf.runtime.channel_adapter import dispatch_pending_replies
from zf.runtime.channel_contracts import default_debate_max_rounds
from zf.runtime.channel_handoff import request_channel_handoff
from zf.runtime.channel_owner_report import build_owner_report_payload
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_router import detect_channel_mention_tokens
from zf.runtime.channel_router import resolve_channel_mentions
from zf.runtime.channel_router import routable_backing_worker_member
from zf.runtime.channel_router import route_channel_message
from zf.runtime.channel_sidecar import channel_message_event_payload
from zf.runtime.control_actions_helpers import _optional_str
from zf.runtime.control_actions_helpers import _required_text
from zf.runtime.control_actions_helpers import _safe_int
from zf.runtime.control_actions_helpers import _stable_control_id
from zf.runtime.control_actions_helpers import _string_list
from zf.runtime.control_actions_helpers import _synthesis_target_member
from zf.runtime.control_actions_helpers import _task_id_from_payload


def _dispatch_channel_replies_background(
    *,
    state_dir,
    channel_id: str,
    actor: str,
    source: str,
    project_root,
    config,
    openclaw_client,
    max_dispatch: int,
) -> None:
    def run() -> None:
        writer = EventWriter(event_log_from_project(state_dir, config=config))
        try:
            dispatch_pending_replies(
                state_dir=state_dir,
                writer=writer,
                channel_id=channel_id,
                actor=actor,
                source=f"{source}:background",
                max_dispatch=max_dispatch,
                project_root=project_root,
                config=config,
                openclaw_client=openclaw_client,
            )
        except Exception as exc:
            writer.emit(
                "channel.agent.reply.dispatch_failed",
                actor=actor,
                correlation_id=channel_id,
                payload={
                    "channel_id": channel_id,
                    "reason": f"background dispatch crashed: {exc}",
                    "source": f"{source}:background",
                },
            )

    thread = threading.Thread(
        target=run,
        name=f"zf-channel-dispatch-{channel_id}",
        daemon=True,
    )
    thread.start()


class ChannelMessageActionsMixin:
    def _channel_post_message(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        channel_id = _required_text(payload, "channel_id")
        thread_id = _optional_str(payload.get("thread_id")) or "main"
        message_id = _optional_str(payload.get("message_id")) or f"msg-{requested.id.removeprefix('evt-')}"
        member_id = _optional_str(payload.get("member_id")) or "operator"
        text = str(payload.get("text") or payload.get("message") or "")
        role = _optional_str(payload.get("role")) or "user"
        explicit_mentions = _string_list(payload.get("mentions"))
        channel = project_channel(self.state_dir, channel_id)
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
        posted_payload = channel_message_event_payload(self.state_dir, {
            "channel_id": channel_id,
            "thread_id": thread_id,
            "message_id": message_id,
            "member_id": member_id,
            "role": role,
            "source": self.surface,
            "text": text,
            "mentions": mentions,
            "mention_tokens": mention_tokens,
            "refs": payload.get("refs") if isinstance(payload.get("refs"), dict) else {},
        }, created_by=f"channel-message:{self.surface}")
        event = self.writer.emit(
            "channel.message.posted",
            actor=self.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=channel_id,
            payload=posted_payload,
        )
        attachment_event_ids: list[str] = []
        refs = posted_payload["refs"] if isinstance(posted_payload.get("refs"), dict) else {}
        attachments = refs.get("attachments") if isinstance(refs.get("attachments"), list) else []
        for index, attachment in enumerate(attachments):
            if not isinstance(attachment, dict):
                continue
            attachment_id = (
                _optional_str(attachment.get("attachment_id"))
                or _optional_str(attachment.get("id"))
                or f"att-{message_id}-{index + 1}"
            )
            uploaded = self.writer.emit(
                "channel.attachment.uploaded",
                actor=self.actor,
                task_id=_task_id_from_payload(payload),
                causation_id=event.id,
                correlation_id=channel_id,
                payload={
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "attachment_id": attachment_id,
                    "message_id": message_id,
                    "member_id": member_id,
                    "name": _optional_str(attachment.get("name") or attachment.get("filename")) or "",
                    "mime": _optional_str(
                        attachment.get("mime")
                        or attachment.get("type")
                        or attachment.get("content_type"),
                    ) or "",
                    "size": _safe_int(attachment.get("size") if attachment.get("size") is not None else attachment.get("bytes")),
                    "hash": _optional_str(attachment.get("hash") or attachment.get("sha256")) or "",
                    "uri": _optional_str(attachment.get("uri")) or "",
                    "refs": redact_obj({
                        "source": attachment.get("source") if isinstance(attachment.get("source"), str) else "",
                        "lastModified": attachment.get("lastModified"),
                    }),
                    "source": self.surface,
                },
            )
            attachment_event_ids.append(uploaded.id)
        route_result = route_channel_message(
            state_dir=self.state_dir,
            writer=self.writer,
            message_event=event,
            message_payload={**posted_payload, "task_id": str(payload.get("task_id") or "")},
            actor=self.actor,
            source=self.surface,
            project_root=self.project_root,
            config=self.config,
            openclaw_client=self.openclaw_client,
            dispatch_inline=False,
        )
        if route_result.reply_requests:
            _dispatch_channel_replies_background(
                state_dir=self.state_dir,
                channel_id=channel_id,
                actor=self.actor,
                source=self.surface,
                project_root=self.project_root,
                config=self.config,
                openclaw_client=self.openclaw_client,
                max_dispatch=max(1, len(route_result.reply_requests)),
            )
        instance_id = str(
            payload.get("instance_id")
            or payload.get("worker")
            or payload.get("backing_worker_session_id")
            or ""
        ).strip()
        if instance_id:
            # 2026-06-10 review P1-7: the raw instance_id path previously
            # bypassed channel membership/permission gating entirely,
            # giving any token holder direct_role_dispatch into a role
            # pane (forbidden by docs 48/75 + operator_contract).
            target_member = routable_backing_worker_member(
                project_channel(self.state_dir, channel_id) or {},
                instance_id,
                sender_member_id=member_id,
            )
            if target_member is None:
                self.writer.emit(
                    "channel.route.blocked",
                    actor=self.actor,
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
                        "source": self.surface,
                    },
                )
            else:
                self.writer.emit(
                    "worker.reply.requested",
                    actor=self.actor,
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
                self.writer.emit(
                    "channel.message.delivered",
                    actor=self.actor,
                    task_id=_task_id_from_payload(payload),
                    causation_id=event.id,
                    correlation_id=channel_id,
                    payload={
                        "channel_id": channel_id,
                        "thread_id": thread_id,
                        "message_id": message_id,
                        "member_id": str(payload.get("member_id") or instance_id),
                        "worker_session_id": instance_id,
                        "source": self.surface,
                    },
                )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="posted",
            task_id=_task_id_from_payload(payload),
            extra={"channel_id": channel_id, "thread_id": thread_id, "message_id": message_id},
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
            "request_id": message_id,
            "event_id": event.id,
            "attachment_event_ids": attachment_event_ids,
            "target_count": len(route_result.targets),
            "reply_request_count": len(route_result.reply_requests),
            "queued_count": len(route_result.queued),
            "route": route_result.as_dict(),
        }
    def _channel_mark_read(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        channel_id = _required_text(payload, "channel_id")
        thread_id = _optional_str(payload.get("thread_id")) or "main"
        member_id = _optional_str(payload.get("member_id")) or "operator"
        message_id = _optional_str(payload.get("message_id")) or ""
        if not message_id:
            channel = project_channel(self.state_dir, channel_id) or {}
            messages = [
                item for item in list(channel.get("messages") or channel.get("recent_messages") or [])
                if isinstance(item, dict) and str(item.get("thread_id") or "main") == thread_id
            ]
            if messages:
                message_id = str(messages[-1].get("message_id") or "")
        if not message_id:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="no channel message found to mark read",
                status_code=404,
                status="not_found",
            )
        event = self.writer.emit(
            "channel.message.read",
            actor=self.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "message_id": message_id,
                "member_id": member_id,
                "source": self.surface,
            },
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="read",
            task_id=_task_id_from_payload(payload),
            extra={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "message_id": message_id,
                "member_id": member_id,
            },
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "read",
            "action": action,
            "requested_action": requested_action,
            "channel_id": channel_id,
            "thread_id": thread_id,
            "message_id": message_id,
            "member_id": member_id,
            "event_id": event.id,
        }
    def _channel_owner_report(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        channel_id = _required_text(payload, "channel_id")
        thread_id = _optional_str(payload.get("thread_id")) or "main"
        owner_id = _required_text(payload, "owner_id")
        member_id = _optional_str(payload.get("member_id")) or ""
        period = _optional_str(payload.get("period")) or "current"
        reason = _optional_str(payload.get("reason")) or "owner report requested"
        channel_view = project_channel(self.state_dir, channel_id) or {}
        authorized = next(
            (m for m in channel_view.get("members") or []
             if str(m.get("member_id") or "") == owner_id
             and "report_owner" in (m.get("permissions") or [])),
            None,
        )
        if authorized is None:
            rejection_reason = (
                f"owner_id {owner_id!r} lacks report_owner permission in channel {channel_id!r}"
            )
            rejected = self.writer.emit(
                "channel.owner_report.rejected",
                actor=self.actor,
                causation_id=requested.id,
                correlation_id=channel_id,
                payload={
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "owner_id": owner_id,
                    "member_id": member_id,
                    "period": period,
                    "reason": rejection_reason,
                    "source": self.surface,
                },
            )
            self._completed(
                requested=requested,
                event=rejected,
                action=action,
                requested_action=requested_action,
                status="rejected",
                task_id=_task_id_from_payload(payload),
                extra={
                    "channel_id": channel_id,
                    "owner_id": owner_id,
                    "reason": rejection_reason,
                },
            )
            return {
                "_status_code": 403,
                "ok": False,
                "status": "rejected",
                "action": action,
                "requested_action": requested_action,
                "channel_id": channel_id,
                "owner_id": owner_id,
                "reason": rejection_reason,
            }
        request_event = self.writer.emit(
            "channel.owner_report.requested",
            actor=self.actor,
            causation_id=requested.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "owner_id": owner_id,
                "member_id": member_id,
                "period": period,
                "reason": reason,
                "source": self.surface,
            },
        )
        report_payload = build_owner_report_payload(
            self.state_dir,
            channel_id=channel_id,
            thread_id=thread_id,
            owner_id=owner_id,
            member_id=member_id,
            period=period,
            reason=reason,
            source=self.surface,
            request_event_id=request_event.id,
        )
        generated = self.writer.emit(
            "channel.owner_report.generated",
            actor=self.actor,
            causation_id=request_event.id,
            correlation_id=channel_id,
            payload=report_payload,
        )
        delivered_event_id = ""
        if bool(payload.get("deliver")):
            delivered = self.writer.emit(
                "channel.owner_report.delivered",
                actor=self.actor,
                causation_id=generated.id,
                correlation_id=channel_id,
                payload={
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "owner_id": owner_id,
                    "member_id": member_id,
                    "report_id": str(report_payload.get("report_id") or ""),
                    "destination": str(payload.get("destination") or "channel"),
                    "summary": str(report_payload.get("summary") or ""),
                    "source": self.surface,
                },
            )
            delivered_event_id = delivered.id
        self._completed(
            requested=requested,
            event=generated,
            action=action,
            requested_action=requested_action,
            status="generated",
            task_id=_task_id_from_payload(payload),
            extra={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "owner_id": owner_id,
                "report_id": str(report_payload.get("report_id") or ""),
                "delivered_event_id": delivered_event_id,
            },
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "generated",
            "action": action,
            "requested_action": requested_action,
            "channel_id": channel_id,
            "thread_id": thread_id,
            "owner_id": owner_id,
            "report_id": str(report_payload.get("report_id") or ""),
            "event_id": generated.id,
            "delivered_event_id": delivered_event_id,
        }
    def _channel_synthesis(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        channel_id = _required_text(payload, "channel_id")
        thread_id = _optional_str(payload.get("thread_id")) or "main"
        event = self.writer.emit(
            "channel.synthesis.proposed",
            actor=self.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "decision": str(payload.get("decision") or ""),
                "summary": str(payload.get("summary") or ""),
                "open_questions": _string_list(payload.get("open_questions")),
                "risks": _string_list(payload.get("risks")),
                "recommended_workflow": (
                    payload.get("recommended_workflow")
                    if isinstance(payload.get("recommended_workflow"), dict) else {}
                ),
                "artifact_ref": str(payload.get("artifact_ref") or ""),
                "spec_path": str(payload.get("spec_path") or ""),
                "source_refs": _string_list(payload.get("source_refs")),
                "evidence_refs": _string_list(payload.get("evidence_refs")),
                "confidence": str(payload.get("confidence") or ""),
                "source": self.surface,
            },
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="proposed",
            task_id=_task_id_from_payload(payload),
            extra={"channel_id": channel_id, "thread_id": thread_id, "synthesis_event_id": event.id},
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "proposed",
            "action": action,
            "requested_action": requested_action,
            "channel_id": channel_id,
            "thread_id": thread_id,
            "event_id": event.id,
        }
    def _channel_synthesis_request(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        channel_id = _required_text(payload, "channel_id")
        thread_id = _optional_str(payload.get("thread_id")) or "main"
        channel = project_channel(self.state_dir, channel_id) or {}
        target_member_id = (
            _optional_str(payload.get("target_member_id"))
            or _synthesis_target_member(channel)
        )
        if not target_member_id:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="target_member_id is required when no synthesizer/facilitator/default responder is available",
                status_code=422,
                status="invalid_payload",
            )
        request_id = (
            _optional_str(payload.get("request_id"))
            or _stable_control_id("synth", requested.id, channel_id, thread_id, target_member_id)
        )
        prompt = (
            _optional_str(payload.get("prompt"))
            or _optional_str(payload.get("reason"))
            or "Synthesize the current channel discussion into a concise decision draft, open questions, risks, and recommended next workflow action."
        )
        request_event = self.writer.emit(
            "channel.synthesis.requested",
            actor=self.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "request_id": request_id,
                "target_member_id": target_member_id,
                "status": "requested",
                "reason": str(payload.get("reason") or "synthesis requested"),
                "prompt": prompt,
                "source": self.surface,
            },
        )
        message_id = f"msg-{request_id}"
        message_payload = channel_message_event_payload(self.state_dir, {
            "channel_id": channel_id,
            "thread_id": thread_id,
            "message_id": message_id,
            "member_id": str(payload.get("member_id") or "operator"),
            "role": "user",
            "source": self.surface,
            "text": f"@{target_member_id} {prompt}",
            "mentions": [target_member_id],
            "refs": {"synthesis_request_id": request_id},
        }, created_by=f"channel-synthesis:{self.surface}")
        message = self.writer.emit(
            "channel.message.posted",
            actor=self.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=request_event.id,
            correlation_id=channel_id,
            payload=message_payload,
        )
        route_result = route_channel_message(
            state_dir=self.state_dir,
            writer=self.writer,
            message_event=message,
            message_payload=message.payload,
            actor=self.actor,
            source=self.surface,
            project_root=self.project_root,
            config=self.config,
            openclaw_client=self.openclaw_client,
            dispatch_inline=False,
        )
        if route_result.reply_requests:
            _dispatch_channel_replies_background(
                state_dir=self.state_dir,
                channel_id=channel_id,
                actor=self.actor,
                source=self.surface,
                project_root=self.project_root,
                config=self.config,
                openclaw_client=self.openclaw_client,
                max_dispatch=max(1, len(route_result.reply_requests)),
            )
        self._completed(
            requested=requested,
            event=request_event,
            action=action,
            requested_action=requested_action,
            status="requested",
            task_id=_task_id_from_payload(payload),
            extra={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "request_id": request_id,
                "target_member_id": target_member_id,
            },
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "requested",
            "action": action,
            "requested_action": requested_action,
            "channel_id": channel_id,
            "thread_id": thread_id,
            "request_id": request_id,
            "target_member_id": target_member_id,
            "event_id": request_event.id,
            "message_event_id": message.id,
            "route": route_result.as_dict(),
        }
    def _channel_drain_replies(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        channel_id = _required_text(payload, "channel_id")
        result = dispatch_pending_replies(
            state_dir=self.state_dir,
            writer=self.writer,
            channel_id=channel_id,
            actor=self.actor,
            source=self.surface,
            target_member_id=str(payload.get("target_member_id") or ""),
            allow_queued=bool(payload.get("allow_queued", True)),
            max_dispatch=int(payload.get("max_dispatch") or 6),
            project_root=self.project_root,
            config=self.config,
            openclaw_client=self.openclaw_client,
        )
        completed_event = self.writer.emit(
            "runtime.action.completed",
            actor=self.actor,
            causation_id=requested.id,
            correlation_id=channel_id,
            payload={
                "action": action,
                "requested_action": requested_action,
                "status": "drained",
                "channel_id": channel_id,
                "result": result.as_dict(),
            },
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "drained",
            "action": action,
            "requested_action": requested_action,
            "channel_id": channel_id,
            "event_id": completed_event.id,
            "result": result.as_dict(),
        }
    def _channel_handoff(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        channel_id = _required_text(payload, "channel_id")
        result = request_channel_handoff(
            state_dir=self.state_dir,
            writer=self.writer,
            channel_id=channel_id,
            thread_id=_optional_str(payload.get("thread_id")) or "main",
            message_id=_required_text(payload, "message_id"),
            member_id=_required_text(payload, "member_id"),
            target_member_id=_required_text(payload, "target_member_id"),
            reason=str(payload.get("reason") or ""),
            actor=self.actor,
            source=self.surface,
            depth=int(payload.get("depth") or 0),
            round_no=int(payload.get("round") or payload.get("round_no") or 0),
            project_root=self.project_root,
            config=self.config,
            openclaw_client=self.openclaw_client,
        )
        ok = not result.skipped
        self.writer.emit(
            "runtime.action.completed" if ok else "runtime.action.rejected",
            actor=self.actor,
            causation_id=requested.id,
            correlation_id=channel_id,
            payload={
                "action": action,
                "requested_action": requested_action,
                "status": "accepted" if ok else "rejected",
                "channel_id": channel_id,
                "result": result.as_dict(),
            },
        )
        return {
            "_status_code": 202 if ok else 422,
            "ok": ok,
            "status": "accepted" if ok else "rejected",
            "action": action,
            "requested_action": requested_action,
            "channel_id": channel_id,
            "result": result.as_dict(),
        }
    def _channel_discussion_mode(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        channel_id = _required_text(payload, "channel_id")
        mode = _required_text(payload, "mode")
        channel = project_channel(self.state_dir, channel_id) or {}
        default_max_rounds = default_debate_max_rounds(len(channel.get("members") or []))
        event = self.writer.emit(
            "channel.discussion.mode.set",
            actor=self.actor,
            causation_id=requested.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": str(payload.get("thread_id") or "main"),
                "mode": mode,
                "max_rounds": int(payload.get("max_rounds") or default_max_rounds),
                "default_responder_id": str(payload.get("default_responder_id") or ""),
                "speaker_policy": payload.get("speaker_policy") if isinstance(payload.get("speaker_policy"), dict) else {},
                "provider_capabilities": (
                    payload.get("provider_capabilities")
                    if isinstance(payload.get("provider_capabilities"), dict) else {}
                ),
                "source": self.surface,
            },
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="set",
            task_id=_task_id_from_payload(payload),
            extra={"channel_id": channel_id, "mode": mode},
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "set",
            "action": action,
            "requested_action": requested_action,
            "channel_id": channel_id,
            "mode": mode,
            "event_id": event.id,
        }
