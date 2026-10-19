"""ChannelAdminActionsMixin — controlled-action handlers (moved verbatim from control_actions.py)."""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any
from zf.core.events import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.core.skills.provenance import (
    resolve_builtin_skill_source,
    resolve_skill_source,
)
from zf.runtime.channel_contracts import normalize_channel_role
from zf.runtime.channel_contracts import normalize_channel_skill_refs
from zf.runtime.channel_contracts import normalize_member_type
from zf.runtime.channel_contracts import normalize_permission_profile
from zf.runtime.channel_contracts import normalize_permissions
from zf.runtime.channel_contracts import normalize_provider
from zf.runtime.channel_contracts import normalize_visibility_profile
from zf.runtime.channel_contracts import permission_profile_write_policy
from zf.runtime.control_actions_channel_creation import ChannelCreationActionsMixin
from zf.runtime.channel_openclaw import prepare_openclaw_member_connection
from zf.runtime.channel_profiles import (
    bind_channel_member_profile,
    resolve_channel_role_definition,
    write_channel_profile_snapshot,
)
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_roles import normalize_role_context_ref
from zf.runtime.control_actions_helpers import _normal_channel_id
from zf.runtime.control_actions_helpers import _optional_str
from zf.runtime.control_actions_helpers import _provider_binding_id
from zf.runtime.control_actions_helpers import _required_text
from zf.runtime.control_actions_helpers import _task_id_from_payload
from zf.runtime.control_actions_helpers import _stable_control_id


def _materialize_channel_skill_refs(
    skill_refs: list[str],
    *,
    project_root: Path,
    state_dir: Path,
    config: Any,
) -> tuple[list[str], list[dict[str, str]]]:
    """Resolve logical refs without mutating the target project's source."""
    unresolved: list[str] = []
    resolved: list[dict[str, str]] = []
    for ref in skill_refs:
        parts = ref.split("/")
        if len(parts) != 3 or parts[0] != "skills" or parts[2] != "SKILL.md":
            unresolved.append(ref)
            continue
        name = parts[1]
        project_target = project_root / "skills" / name / "SKILL.md"
        if project_target.exists():
            resolved.append(_resolved_channel_skill(
                logical_ref=ref,
                path=project_target,
                source="project",
            ))
            continue
        source = resolve_skill_source(
            project_root=project_root,
            state_dir=state_dir,
            name=name,
            config=config,
        )
        if source is None:
            source = resolve_builtin_skill_source(name)
        if source is None or not source.is_file():
            unresolved.append(ref)
            continue
        body = source.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        target = state_dir / "runtime-skills" / name / digest / "SKILL.md"
        target.parent.parent.mkdir(parents=True, exist_ok=True)
        # An invitation must not replace a method pinned by existing members.
        # Publish the complete bundle before emitting its member binding.
        with locked_path(target.parent):
            if not target.parent.exists():
                with tempfile.TemporaryDirectory(dir=target.parent.parent) as staging:
                    bundle = Path(staging) / "bundle"
                    shutil.copytree(source.parent, bundle)
                    (bundle / "SKILL.md").write_bytes(body)
                    bundle.replace(target.parent)
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError(f"channel skill snapshot digest mismatch: {ref}")
        resolved.append(_resolved_channel_skill(
            logical_ref=ref,
            path=target,
            source=str(source),
        ))
    if resolved:
        _write_runtime_skill_manifest(state_dir, resolved)
    return unresolved, resolved


def _resolved_channel_skill(
    *,
    logical_ref: str,
    path: Path,
    source: str,
) -> dict[str, str]:
    return {
        "logical_ref": logical_ref,
        "resolved_path": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source": source,
    }


def _write_runtime_skill_manifest(
    state_dir: Path,
    resolved: list[dict[str, str]],
) -> None:
    manifest_path = state_dir / "runtime-skills" / "manifest.json"
    existing: list[dict[str, str]] = []
    if manifest_path.is_file():
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            existing = [
                item
                for item in raw.get("skills") or []
                if isinstance(item, dict)
            ]
        except (OSError, ValueError):
            existing = []
    by_ref = {
        str(item.get("logical_ref") or ""): item
        for item in [*existing, *resolved]
        if str(item.get("logical_ref") or "")
    }
    atomic_write_text(
        manifest_path,
        json.dumps(
            {
                "schema_version": "channel.runtime_skills.v1",
                "skills": [
                    by_ref[key] for key in sorted(by_ref)
                ],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


class ChannelAdminActionsMixin(ChannelCreationActionsMixin):
    def _channel_invite_member(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
        invitation: dict | None = None,
    ) -> dict:
        if payload.get("proposal_id"):
            from zf.runtime.channel_member_admission import decide_member_invitation
            return decide_member_invitation(self, requested=requested, action=action,
                                            requested_action=requested_action, payload=payload)
        channel_id = _normal_channel_id(_required_text(payload, "channel_id"))
        thread_id = _optional_str(payload.get("thread_id")) or "main"
        member_id = _required_text(payload, "member_id")
        bound_payload, profile_error = bind_channel_member_profile(
            self.config,
            payload,
            allow_inline_profile=bool(payload.get("template_id")),
        )
        if profile_error or bound_payload is None:
            return self._channel_reject_member(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
                channel_id=channel_id,
                thread_id=thread_id,
                member_id=member_id,
                provider=normalize_provider(
                    payload.get("provider") or payload.get("backend")
                ),
                backend=str(payload.get("backend") or ""),
                member_type=normalize_member_type(
                    payload.get("member_type"),
                    backend=payload.get("provider") or payload.get("backend"),
                ),
                channel_role=normalize_channel_role(
                    payload.get("channel_role") or payload.get("role")
                ),
                visibility_profile=normalize_visibility_profile(
                    payload.get("visibility_profile")
                ),
                permissions=normalize_permissions(payload.get("permissions")),
                provider_binding_id=_provider_binding_id(payload),
                reason=profile_error or "channel profile binding failed",
            )
        payload = bound_payload
        provider = normalize_provider(
            payload.get("provider")
            or payload.get("backend")
            or payload.get("member_type")
        )
        member_type = normalize_member_type(
            payload.get("member_type"),
            backend=provider,
        )
        channel_role = normalize_channel_role(
            payload.get("channel_role") or payload.get("role"),
            member_type=member_type,
        )
        visibility_profile = normalize_visibility_profile(
            payload.get("visibility_profile"),
            channel_role=channel_role,
            member_type=member_type,
        )
        permission_profile = normalize_permission_profile(
            payload.get("permission_profile")
        )
        write_policy = permission_profile_write_policy(permission_profile)
        permissions = normalize_permissions(payload.get("permissions"), member_type=member_type)
        skill_refs = normalize_channel_skill_refs(payload.get("skill_refs"))
        (
            unresolved_skill_refs,
            resolved_skill_refs,
        ) = _materialize_channel_skill_refs(
            skill_refs,
            project_root=self.project_root or self.state_dir.parent,
            state_dir=self.state_dir,
            config=self.config,
        )
        workflow_role_binding = (
            payload.get("workflow_role_binding")
            if isinstance(payload.get("workflow_role_binding"), dict)
            else {}
        )
        role_context_ref = normalize_role_context_ref(payload.get("role_context_ref"))
        provider_binding_id = _provider_binding_id(payload)
        if unresolved_skill_refs:
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
                provider_binding_id=provider_binding_id,
                reason=(
                    "channel skill refs could not be resolved: "
                    + ", ".join(unresolved_skill_refs)
                ),
            )
        role_definition, role_error = resolve_channel_role_definition(
            payload,
            project_root=self.project_root or self.state_dir.parent,
        )
        if role_error:
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
                provider_binding_id=provider_binding_id,
                reason=role_error,
            )
        profile_snapshot = write_channel_profile_snapshot(
            self.state_dir,
            channel_id=channel_id,
            member_id=member_id,
            binding=payload,
            resolved_skill_refs=resolved_skill_refs,
            role_definition=role_definition,
            created_by=self.actor,
            source_event_id=requested.id,
        )
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
                "invitation_proposal_id": (invitation or {}).get("proposal_id", ""),
                "thread_id": thread_id,
                "member_id": member_id,
                "persona": str(payload.get("persona") or member_id),
                "display_name": str(payload.get("display_name") or payload.get("persona") or member_id),
                "profile_id": str(payload.get("profile_id") or ""),
                "profile_revision": int(payload.get("profile_revision") or 1),
                "profile_provenance": str(
                    payload.get("profile_provenance") or "legacy_inline"
                ),
                "profile_digest": str(payload.get("profile_digest") or ""),
                "config_digest": str(payload.get("config_digest") or ""),
                "skill_set_digest": str(
                    profile_snapshot.get("skill_set_digest")
                    or payload.get("skill_set_digest")
                    or ""
                ),
                "permission_digest": str(
                    payload.get("permission_digest") or ""
                ),
                "role_definition_digest": str(
                    profile_snapshot.get("role_definition_digest") or ""
                ),
                "profile_snapshot_ref": str(profile_snapshot.get("ref") or ""),
                "profile_snapshot_sha256": str(
                    profile_snapshot.get("sha256") or ""
                ),
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
                "resolved_skill_refs": resolved_skill_refs,
                "scope": str(payload.get("scope") or "channel"),
                "permissions": permissions,
                "backing_worker_session_id": str(payload.get("backing_worker_session_id") or ""),
                "workflow_role_binding": workflow_role_binding,
                "discussion_policy": payload.get("discussion_policy") if isinstance(payload.get("discussion_policy"), dict) else {},
                "output_contract": payload.get("output_contract") if isinstance(payload.get("output_contract"), dict) else {},
                "role_selection_reason": str(
                    payload.get("role_selection_reason")
                    or payload.get("reason")
                    or ""
                ),
                "requirement_ref": str(payload.get("requirement_ref") or ""),
                "requirement_digest": str(
                    payload.get("requirement_digest") or ""
                ),
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
        if invitation:
            from zf.runtime.channel_member_admission import complete_member_admission
            complete_member_admission(self, requested=requested, proposal=invitation, invited=connected_event)
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
        channel_id = _normal_channel_id(_required_text(payload, "channel_id"))
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
        channel_id = _normal_channel_id(_required_text(payload, "channel_id"))
        thread_id = _optional_str(payload.get("thread_id")) or "main"
        member_id = _required_text(payload, "member_id")
        channel = project_channel(self.state_dir, channel_id) or {}
        if str(channel.get("leader_member_id") or "") == member_id:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason=(
                    "current Channel Leader must be replaced or revoked "
                    "before member removal"
                ),
                status_code=409,
                status="leader_binding_conflict",
            )
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

    def _channel_set_leader(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        channel_id = _normal_channel_id(_required_text(payload, "channel_id"))
        thread_id = _optional_str(payload.get("thread_id")) or "main"
        leader_member_id = str(payload.get("leader_member_id") or "").strip()
        idempotency_key = str(
            payload.get("idempotency_key")
            or _stable_control_id(
                "channel-leader",
                channel_id,
                leader_member_id,
                payload.get("expected_revision"),
            )
        )
        with locked_path(
            self.state_dir
            / "locks"
            / _stable_control_id("channel-leader", channel_id)
        ):
            for prior in reversed(self.writer.event_log.read_all()):
                if prior.type != "channel.leader.updated":
                    continue
                prior_payload = (
                    prior.payload if isinstance(prior.payload, dict) else {}
                )
                if (
                    str(prior_payload.get("channel_id") or "") == channel_id
                    and str(prior_payload.get("idempotency_key") or "")
                    == idempotency_key
                ):
                    return {
                        "_status_code": 200,
                        "ok": True,
                        "status": "existing",
                        "action": action,
                        "requested_action": requested_action,
                        "channel_id": channel_id,
                        "leader_member_id": str(
                            prior_payload.get("leader_member_id") or ""
                        ),
                        "leader_revision": int(
                            prior_payload.get("leader_revision") or 0
                        ),
                        "event_id": prior.id,
                        "idempotency_key": idempotency_key,
                    }
            channel = project_channel(self.state_dir, channel_id) or {}
            if not channel.get("created_by_event"):
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=_task_id_from_payload(payload),
                    reason="channel not found",
                    status_code=404,
                    status="not_found",
                )
            owner_actor_ref = str(channel.get("owner_actor_ref") or "")
            if not owner_actor_ref or self.actor != owner_actor_ref:
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=_task_id_from_payload(payload),
                    reason="only the canonical Channel Owner may update Leader",
                    status_code=403,
                    status="forbidden",
                )
            current_revision = int(channel.get("leader_revision") or 0)
            expected_revision = int(
                payload.get("expected_revision")
                if payload.get("expected_revision") is not None
                else current_revision
            )
            if expected_revision != current_revision:
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=_task_id_from_payload(payload),
                    reason=(
                        "Channel Leader revision is stale; current revision "
                        f"is {current_revision}"
                    ),
                    status_code=409,
                    status="stale_revision",
                )
            if leader_member_id:
                member = next(
                    (
                        item
                        for item in channel.get("members") or []
                        if isinstance(item, dict)
                        and str(item.get("member_id") or "")
                        == leader_member_id
                    ),
                    None,
                )
                if (
                    member is None
                    or str(member.get("status") or "")
                    in {"removed", "suspended", "rejected"}
                    or "propose_workflow"
                    not in set(member.get("permissions") or [])
                ):
                    return self._failed(
                        requested=requested,
                        action=action,
                        requested_action=requested_action,
                        task_id=_task_id_from_payload(payload),
                        reason=(
                            "Leader must be an active Channel member with "
                            "propose_workflow permission"
                        ),
                        status_code=422,
                        status="invalid_leader",
                    )
            event = self.writer.emit(
                "channel.leader.updated",
                actor=self.actor,
                task_id=_task_id_from_payload(payload),
                causation_id=requested.id,
                correlation_id=channel_id,
                payload={
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "previous_leader_member_id": str(
                        channel.get("leader_member_id") or ""
                    ),
                    "leader_member_id": leader_member_id,
                    "leader_revision": current_revision + 1,
                    "owner_actor_ref": owner_actor_ref,
                    "reason": str(
                        payload.get("reason") or "Owner updated Channel Leader"
                    ),
                    "idempotency_key": idempotency_key,
                    "source": self.surface,
                },
            )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="leader_updated",
            task_id=_task_id_from_payload(payload),
            extra={
                "channel_id": channel_id,
                "leader_member_id": leader_member_id,
                "leader_revision": current_revision + 1,
            },
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "leader_updated",
            "action": action,
            "requested_action": requested_action,
            "channel_id": channel_id,
            "leader_member_id": leader_member_id,
            "leader_revision": current_revision + 1,
            "event_id": event.id,
            "idempotency_key": idempotency_key,
        }
    def _channel_delete(
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
        channel_id = _normal_channel_id(_required_text(payload, "channel_id"))
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
