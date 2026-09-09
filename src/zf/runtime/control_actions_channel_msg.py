"""ChannelMessageActionsMixin — controlled-action handlers (moved verbatim from control_actions.py)."""
from __future__ import annotations

from zf.core.events import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.channel_adapter import dispatch_pending_replies
from zf.runtime.channel_synthesis_lock import (
    channel_synthesis_lock_path,
    synthesis_request_in_flight,
)
from zf.runtime.channel_handoff import request_channel_handoff
from zf.runtime.channel_message_ingress import (
    dispatch_channel_replies_background,
    execute_channel_message_ingress,
)
from zf.runtime.channel_owner_report import build_owner_report_payload
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_router import route_channel_message
from zf.runtime.channel_sidecar import channel_message_event_payload
from zf.runtime.control_actions_helpers import _normal_channel_id
from zf.runtime.control_actions_helpers import _optional_str
from zf.runtime.control_actions_helpers import _required_text
from zf.runtime.control_actions_helpers import _stable_control_id
from zf.runtime.control_actions_helpers import _string_list
from zf.runtime.control_actions_helpers import _synthesis_target_member
from zf.runtime.control_actions_helpers import _task_id_from_payload


class ChannelMessageActionsMixin:
    def _channel_post_message(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
        emit_completion: bool = True,
    ) -> dict:
        return execute_channel_message_ingress(
            self,
            requested=requested,
            action=action,
            requested_action=requested_action,
            payload=payload,
            emit_completion=emit_completion,
        )

    def _channel_mark_read(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        channel_id = _normal_channel_id(_required_text(payload, "channel_id"))
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

    def _channel_pin_message(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        channel_id = _normal_channel_id(_required_text(payload, "channel_id"))
        thread_id = _optional_str(payload.get("thread_id")) or "main"
        message_id = _required_text(payload, "message_id")
        member_id = _optional_str(payload.get("member_id")) or "operator"
        channel = project_channel(self.state_dir, channel_id) or {}
        message = next(
            (
                item
                for item in channel.get("messages") or []
                if isinstance(item, dict)
                and str(item.get("message_id") or "") == message_id
                and str(item.get("thread_id") or "main") == thread_id
            ),
            None,
        )
        if message is None:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="Channel message does not exist in the exact thread",
                status_code=404,
                status="not_found",
            )
        owner_actor_ref = str(channel.get("owner_actor_ref") or "")
        owner_aliases = {
            owner_actor_ref,
            owner_actor_ref.removeprefix("owner:"),
        }
        member = next(
            (
                item
                for item in channel.get("members") or []
                if isinstance(item, dict)
                and str(item.get("member_id") or "") == member_id
            ),
            None,
        )
        member_allowed = (
            isinstance(member, dict)
            and str(member.get("status") or "active").lower()
            not in {"failed", "rejected", "removed", "suspended"}
            and (
                not member.get("permissions")
                or "message" in set(member.get("permissions") or [])
            )
        )
        if (
            member_id not in owner_aliases
            and self.actor not in owner_aliases
            and not member_allowed
        ):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="pin requires the Channel Owner or an active messaging member",
                status_code=403,
                status="forbidden",
            )
        pinned = bool(payload.get("pinned", True))
        event = self.writer.emit(
            "channel.message.pinned",
            actor=self.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "message_id": message_id,
                "member_id": member_id,
                "pinned": pinned,
                "reason": str(payload.get("reason") or ""),
                "idempotency_key": str(
                    payload.get("idempotency_key") or requested.id
                ),
                "source": self.surface,
            },
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="pinned" if pinned else "unpinned",
            task_id=_task_id_from_payload(payload),
            extra={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "message_id": message_id,
                "pinned": pinned,
            },
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "pinned" if pinned else "unpinned",
            "action": action,
            "requested_action": requested_action,
            "channel_id": channel_id,
            "thread_id": thread_id,
            "message_id": message_id,
            "pinned": pinned,
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
        channel_id = _normal_channel_id(_required_text(payload, "channel_id"))
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
        channel_id = _normal_channel_id(_required_text(payload, "channel_id"))
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
        channel_id = _normal_channel_id(_required_text(payload, "channel_id"))
        thread_id = _optional_str(payload.get("thread_id")) or "main"
        with locked_path(
            channel_synthesis_lock_path(self.state_dir, channel_id, thread_id)
        ):
            channel = project_channel(self.state_dir, channel_id) or {}
            active_request = synthesis_request_in_flight(channel, thread_id)
            if active_request is not None:
                active_request_id = str(active_request.get("request_id") or "")
                active_target_member_id = str(
                    active_request.get("target_member_id") or ""
                )
                self._completed(
                    requested=requested,
                    event=requested,
                    action=action,
                    requested_action=requested_action,
                    status="already_requested",
                    task_id=_task_id_from_payload(payload),
                    extra={
                        "channel_id": channel_id,
                        "thread_id": thread_id,
                        "request_id": active_request_id,
                        "target_member_id": active_target_member_id,
                    },
                )
                return {
                    "_status_code": 200,
                    "ok": True,
                    "status": "already_requested",
                    "action": action,
                    "requested_action": requested_action,
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "request_id": active_request_id,
                    "target_member_id": active_target_member_id,
                }
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
            dispatch_channel_replies_background(
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
        channel_id = _normal_channel_id(_required_text(payload, "channel_id"))
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
        channel_id = _normal_channel_id(_required_text(payload, "channel_id"))
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
        channel_id = _normal_channel_id(_required_text(payload, "channel_id"))
        mode = _required_text(payload, "mode")
        channel = project_channel(self.state_dir, channel_id) or {}
        event_payload = {
            "channel_id": channel_id,
            "thread_id": str(payload.get("thread_id") or "main"),
            "mode": mode,
            "default_responder_id": str(payload.get("default_responder_id") or ""),
            "speaker_policy": payload.get("speaker_policy") if isinstance(payload.get("speaker_policy"), dict) else {},
            "provider_capabilities": (
                payload.get("provider_capabilities")
                if isinstance(payload.get("provider_capabilities"), dict) else {}
            ),
            "source": self.surface,
        }
        if payload.get("max_rounds") is not None:
            try:
                max_rounds = int(payload.get("max_rounds") or 0)
            except (TypeError, ValueError):
                max_rounds = 0
            if max_rounds > 0:
                event_payload["max_rounds"] = max_rounds
        # Forward the discussion-config fields the projection folds
        # (channel_projection._apply_discussion_mode) but that were previously
        # dropped here, so the operator could never tune the relay depth cap,
        # blind roster, synthesizer, or phase deadlines through this action.
        if payload.get("max_relay_depth") is not None:
            event_payload["max_relay_depth"] = payload.get("max_relay_depth")
        if payload.get("max_parallel_replies") is not None:
            event_payload["max_parallel_replies"] = payload.get(
                "max_parallel_replies"
            )
        if payload.get("participants") is not None:
            event_payload["participants"] = payload.get("participants")
        if payload.get("synthesizer") is not None:
            event_payload["synthesizer"] = str(payload.get("synthesizer"))
        if isinstance(payload.get("phase_deadline_seconds"), dict):
            event_payload["phase_deadline_seconds"] = payload.get("phase_deadline_seconds")
        event = self.writer.emit(
            "channel.discussion.mode.set",
            actor=self.actor,
            causation_id=requested.id,
            correlation_id=channel_id,
            payload=event_payload,
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
