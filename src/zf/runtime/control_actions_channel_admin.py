"""ChannelAdminActionsMixin — controlled-action handlers (moved verbatim from control_actions.py)."""
from __future__ import annotations

from typing import Any
from zf.core.events import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.runtime.channel_contracts import normalize_channel_role
from zf.runtime.channel_contracts import normalize_channel_skill_refs
from zf.runtime.channel_contracts import normalize_member_type
from zf.runtime.channel_contracts import normalize_permission_profile
from zf.runtime.channel_contracts import normalize_permissions
from zf.runtime.channel_contracts import normalize_provider
from zf.runtime.channel_contracts import normalize_visibility_profile
from zf.runtime.channel_contracts import permission_profile_write_policy
from zf.runtime.channel_openclaw import prepare_openclaw_member_connection
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_roles import normalize_role_context_ref
from zf.runtime.control_actions_helpers import _normal_channel_id
from zf.runtime.control_actions_helpers import _optional_str
from zf.runtime.control_actions_helpers import _provider_binding_id
from zf.runtime.control_actions_helpers import _required_text
from zf.runtime.control_actions_helpers import _task_id_from_payload


class ChannelAdminActionsMixin:
    def _channel_create(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        name = _required_text(payload, "name") or _required_text(payload, "channel_name")
        channel_id = _normal_channel_id(payload.get("channel_id") or payload.get("id") or name)
        event = self.writer.emit(
            "channel.created",
            actor=self.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "name": name,
                "channel_name": name,
                "thread_id": _optional_str(payload.get("thread_id")) or "main",
                "task_id": str(payload.get("task_id") or ""),
                "created_by": str(payload.get("created_by") or self.actor),
                "source": self.surface,
                "scope": payload.get("scope") if isinstance(payload.get("scope"), dict) else {},
            },
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="created",
            task_id=_task_id_from_payload(payload),
            extra={"channel_id": channel_id, "name": name},
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "created",
            "action": action,
            "requested_action": requested_action,
            "reason": "channel created",
            "channel_id": channel_id,
            "name": name,
            "event_id": event.id,
        }
    def _channel_invite_member(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        channel_id = _required_text(payload, "channel_id")
        thread_id = _optional_str(payload.get("thread_id")) or "main"
        member_id = _required_text(payload, "member_id")
        provider = normalize_provider(payload.get("provider") or payload.get("backend") or payload.get("member_type"))
        member_type = normalize_member_type(payload.get("member_type"), backend=provider)
        channel_role = normalize_channel_role(payload.get("channel_role") or payload.get("role"), member_type=member_type)
        visibility_profile = normalize_visibility_profile(
            payload.get("visibility_profile"),
            channel_role=channel_role,
            member_type=member_type,
        )
        permission_profile = normalize_permission_profile(payload.get("permission_profile"))
        write_policy = permission_profile_write_policy(permission_profile)
        permissions = normalize_permissions(payload.get("permissions"), member_type=member_type)
        skill_refs = normalize_channel_skill_refs(payload.get("skill_refs"))
        workflow_role_binding = (
            payload.get("workflow_role_binding")
            if isinstance(payload.get("workflow_role_binding"), dict)
            else {}
        )
        role_context_ref = normalize_role_context_ref(payload.get("role_context_ref"))
        provider_binding_id = _provider_binding_id(payload)
        remote_agent_id = ""
        provider_session_id = ""
        openclaw_capabilities: dict[str, Any] = {}
        if provider == "openclaw":
            connection = prepare_openclaw_member_connection(
                config=self.config,
                state_dir=self.state_dir,
                writer=self.writer,
                actor=self.actor,
                causation_id=requested.id,
                channel_id=channel_id,
                member_id=member_id,
                display_name=str(
                    payload.get("display_name")
                    or payload.get("persona")
                    or member_id
                ),
                channel_role=channel_role,
                permissions=permissions,
                requested_binding_id=provider_binding_id,
                source=self.surface,
                model=str(payload.get("model") or ""),
                client=self.openclaw_client,
            )
            if not connection.ok:
                return self._channel_reject_member(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    payload=payload,
                    channel_id=channel_id,
                    thread_id=thread_id,
                    member_id=member_id,
                    provider=provider,
                    backend=str(payload.get("backend") or provider),
                    member_type=member_type,
                    channel_role=channel_role,
                    visibility_profile=visibility_profile,
                    permissions=permissions,
                    provider_binding_id=connection.provider_binding_id,
                    reason=connection.reason,
                )
            provider_binding_id = connection.provider_binding_id
            remote_agent_id = connection.remote_agent_id
            provider_session_id = connection.provider_session_id
            openclaw_capabilities = connection.capabilities
        event = self.writer.emit(
            "channel.member.invited",
            actor=self.actor,
            causation_id=requested.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "member_id": member_id,
                "persona": str(payload.get("persona") or member_id),
                "display_name": str(payload.get("display_name") or payload.get("persona") or member_id),
                "role": str(payload.get("role") or channel_role),
                "channel_role": channel_role,
                "member_type": member_type,
                "legacy_member_type": str(payload.get("member_type") or ""),
                "provider": provider,
                "backend": str(payload.get("backend") or provider),
                "provider_binding_id": provider_binding_id,
                "remote_agent_id": remote_agent_id,
                "provider_session_id": provider_session_id,
                "visibility_profile": visibility_profile,
                "permission_profile": permission_profile,
                "write_policy": write_policy,
                "role_context_ref": role_context_ref,
                "skill_refs": skill_refs,
                "scope": str(payload.get("scope") or "channel"),
                "permissions": permissions,
                "backing_worker_session_id": str(payload.get("backing_worker_session_id") or ""),
                "workflow_role_binding": workflow_role_binding,
                "discussion_policy": payload.get("discussion_policy") if isinstance(payload.get("discussion_policy"), dict) else {},
                "output_contract": payload.get("output_contract") if isinstance(payload.get("output_contract"), dict) else {},
                "capabilities": openclaw_capabilities,
                "source": self.surface,
            },
        )
        if permission_profile != "read_only":
            self.writer.emit(
                "channel.member.permission_profile.audit",
                actor=self.actor,
                causation_id=event.id,
                correlation_id=channel_id,
                payload=redact_obj({
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "member_id": member_id,
                    "provider": provider,
                    "backend": str(payload.get("backend") or provider),
                    "channel_role": channel_role,
                    "permission_profile": permission_profile,
                    "write_policy": write_policy,
                    "dangerous_ack": bool(
                        payload.get("dangerous_ack")
                        or payload.get("permission_profile_ack")
                        or payload.get("confirm_dangerous")
                    ),
                    "reason": str(payload.get("permission_profile_reason") or payload.get("reason") or ""),
                    "source": self.surface,
                }),
            )
        connected_event = event
        if provider == "openclaw":
            connected_event = self.writer.emit(
                "channel.member.connected",
                actor=self.actor,
                causation_id=event.id,
                correlation_id=channel_id,
                payload=redact_obj({
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "member_id": member_id,
                    "provider": provider,
                    "backend": str(payload.get("backend") or provider),
                    "provider_binding_id": provider_binding_id,
                    "remote_agent_id": remote_agent_id,
                    "provider_session_id": provider_session_id,
                    "channel_role": channel_role,
                    "visibility_profile": visibility_profile,
                    "permission_profile": permission_profile,
                    "capabilities": openclaw_capabilities,
                    "source": self.surface,
                }),
            )
        self._completed(
            requested=requested,
            event=connected_event,
            action=action,
            requested_action=requested_action,
            status="connected" if provider == "openclaw" else "invited",
            task_id=_task_id_from_payload(payload),
            extra={"channel_id": channel_id, "thread_id": thread_id, "member_id": member_id},
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "connected" if provider == "openclaw" else "invited",
            "action": action,
            "requested_action": requested_action,
            "channel_id": channel_id,
            "member_id": member_id,
            "provider_binding_id": provider_binding_id,
            "remote_agent_id": remote_agent_id,
            "event_id": connected_event.id,
        }
    def _channel_update_member_permission(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        channel_id = _required_text(payload, "channel_id")
        thread_id = _optional_str(payload.get("thread_id")) or "main"
        member_id = _required_text(payload, "member_id")
        channel = project_channel(self.state_dir, channel_id) or {}
        member = next(
            (
                item for item in list(channel.get("members") or [])
                if isinstance(item, dict) and str(item.get("member_id") or "") == member_id
            ),
            None,
        )
        if member is None:
            return {
                "_status_code": 404,
                "ok": False,
                "status": "not_found",
                "action": action,
                "requested_action": requested_action,
                "channel_id": channel_id,
                "member_id": member_id,
                "reason": "channel member not found",
            }
        permission_profile = normalize_permission_profile(payload.get("permission_profile"))
        write_policy = permission_profile_write_policy(permission_profile)
        event = self.writer.emit(
            "channel.member.permissions.updated",
            actor=self.actor,
            causation_id=requested.id,
            correlation_id=channel_id,
            payload=redact_obj({
                "channel_id": channel_id,
                "thread_id": thread_id,
                "member_id": member_id,
                "member_type": str(member.get("member_type") or ""),
                "provider": str(member.get("provider") or ""),
                "backend": str(member.get("backend") or ""),
                "provider_binding_id": str(member.get("provider_binding_id") or ""),
                "channel_role": str(member.get("channel_role") or ""),
                "visibility_profile": str(member.get("visibility_profile") or ""),
                "permission_profile": permission_profile,
                "write_policy": write_policy,
                "permissions": normalize_permissions(
                    payload.get("permissions") if "permissions" in payload else member.get("permissions", []),
                    member_type=str(member.get("member_type") or ""),
                ),
                "reason": str(payload.get("permission_profile_reason") or payload.get("reason") or "updated from channel member controls"),
                "source": self.surface,
            }),
        )
        self.writer.emit(
            "channel.member.permission_profile.audit",
            actor=self.actor,
            causation_id=event.id,
            correlation_id=channel_id,
            payload=redact_obj({
                "channel_id": channel_id,
                "thread_id": thread_id,
                "member_id": member_id,
                "provider": str(member.get("provider") or ""),
                "backend": str(member.get("backend") or ""),
                "channel_role": str(member.get("channel_role") or ""),
                "permission_profile": permission_profile,
                "write_policy": write_policy,
                "dangerous_ack": bool(
                    payload.get("dangerous_ack")
                    or payload.get("permission_profile_ack")
                    or payload.get("confirm_dangerous")
                ),
                "reason": str(payload.get("permission_profile_reason") or payload.get("reason") or ""),
                "source": self.surface,
            }),
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="permission_updated",
            task_id=_task_id_from_payload(payload),
            extra={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "member_id": member_id,
                "permission_profile": permission_profile,
            },
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "permission_updated",
            "action": action,
            "requested_action": requested_action,
            "channel_id": channel_id,
            "member_id": member_id,
            "permission_profile": permission_profile,
            "write_policy": write_policy,
            "event_id": event.id,
        }
    def _channel_reject_member(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
        channel_id: str,
        thread_id: str,
        member_id: str,
        provider: str,
        backend: str,
        member_type: str,
        channel_role: str,
        visibility_profile: str,
        permissions: list[str],
        provider_binding_id: str,
        reason: str,
    ) -> dict:
        permission_profile = normalize_permission_profile(payload.get("permission_profile"))
        event = self.writer.emit(
            "channel.member.add.rejected",
            actor=self.actor,
            causation_id=requested.id,
            correlation_id=channel_id,
            payload=redact_obj({
                "channel_id": channel_id,
                "thread_id": thread_id,
                "member_id": member_id,
                "member_type": member_type,
                "provider": provider,
                "backend": backend,
                "provider_binding_id": provider_binding_id,
                "channel_role": channel_role,
                "visibility_profile": visibility_profile,
                "permission_profile": permission_profile,
                "permissions": permissions,
                "reason": reason,
                "source": self.surface,
            }),
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="rejected",
            task_id=_task_id_from_payload(payload),
            extra={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "member_id": member_id,
                "reason": reason,
            },
        )
        return {
            "_status_code": 409,
            "ok": False,
            "status": "rejected",
            "action": action,
            "requested_action": requested_action,
            "channel_id": channel_id,
            "member_id": member_id,
            "provider_binding_id": provider_binding_id,
            "reason": reason,
            "event_id": event.id,
        }
    def _channel_remove_member(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        channel_id = _required_text(payload, "channel_id")
        thread_id = _optional_str(payload.get("thread_id")) or "main"
        member_id = _required_text(payload, "member_id")
        event = self.writer.emit(
            "channel.member.removed",
            actor=self.actor,
            causation_id=requested.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "member_id": member_id,
                "reason": str(payload.get("reason") or "removed from channel"),
                "source": self.surface,
            },
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="removed",
            task_id=_task_id_from_payload(payload),
            extra={"channel_id": channel_id, "thread_id": thread_id, "member_id": member_id},
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "removed",
            "action": action,
            "requested_action": requested_action,
            "channel_id": channel_id,
            "member_id": member_id,
            "event_id": event.id,
        }
    def _channel_delete(
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
            "channel.archived",
            actor=self.actor,
            causation_id=requested.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "reason": str(payload.get("reason") or "deleted from channel list"),
                "source": self.surface,
            },
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="deleted",
            task_id=_task_id_from_payload(payload),
            extra={"channel_id": channel_id, "thread_id": thread_id},
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "deleted",
            "action": action,
            "requested_action": requested_action,
            "channel_id": channel_id,
            "event_id": event.id,
        }
    def _channel_clear_history(
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
            "channel.history.cleared",
            actor=self.actor,
            causation_id=requested.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "reason": str(payload.get("reason") or "clear visible channel history"),
                "source": self.surface,
            },
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="cleared",
            task_id=_task_id_from_payload(payload),
            extra={"channel_id": channel_id, "thread_id": thread_id},
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "cleared",
            "action": action,
            "requested_action": requested_action,
            "channel_id": channel_id,
            "event_id": event.id,
        }
