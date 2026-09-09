"""Controlled actions for versioned Channel templates."""

from __future__ import annotations

from zf.core.events import ZfEvent
from zf.runtime.channel_contracts import (
    discussion_engine_mode,
    normalize_channel_role,
    normalize_member_type,
    normalize_permission_profile,
    normalize_permissions,
    normalize_provider,
    normalize_product_discussion_mode,
    normalize_visibility_profile,
    permission_profile_write_policy,
)
from zf.runtime.channel_discussion import discussion_roster
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_profiles import (
    bind_channel_member_profile,
    channel_profile_selection_manifest,
    resolve_channel_role_definition,
    template_profile_binding_payload,
    write_channel_profile_snapshot,
)
from zf.runtime.channel_owner_authority import normalize_owner_delegates
from zf.runtime.channel_templates import materialize_channel_template
from zf.runtime.control_actions_channel_admin import _materialize_channel_skill_refs
from zf.runtime.control_actions_helpers import (
    _normal_channel_id,
    _required_text,
    _stable_control_id,
    _task_id_from_payload,
)


class ChannelTemplateActionsMixin:
    def _channel_create_from_template(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
        emit_completion: bool = True,
    ) -> dict:
        template_id = _required_text(payload, "template_id")
        materialized, error = materialize_channel_template(
            template_id,
            overrides=payload.get("overrides"),
        )
        if error or materialized is None:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason=error or "channel template preflight failed",
                status_code=422,
                status="invalid_template",
            )
        expected_digest = str(
            payload.get("expected_materialization_digest") or ""
        ).strip()
        if (
            expected_digest
            and expected_digest != materialized["materialization_digest"]
        ):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason=(
                    "channel template materialization changed after Plan "
                    "selection was presented"
                ),
                status_code=409,
                status="template_superseded",
            )
        expected_profile_selection_digest = str(
            payload.get("expected_profile_selection_digest") or ""
        ).strip()
        _, profile_selection_digest, profile_error = (
            channel_profile_selection_manifest(
                self.config,
                template_id=template_id,
                members=list(materialized.get("members") or []),
            )
        )
        if profile_error:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason=profile_error,
                status_code=422,
                status="invalid_template",
            )
        if (
            expected_profile_selection_digest
            and expected_profile_selection_digest != profile_selection_digest
        ):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason=(
                    "channel Profile selection changed after Plan selection was "
                    "presented"
                ),
                status_code=409,
                status="profile_selection_superseded",
            )
        name = str(payload.get("name") or materialized["name"])
        channel_id = _normal_channel_id(
            payload.get("channel_id")
            or f"{template_id}-{requested.id.removeprefix('evt-')[:10]}"
        )
        existing = project_channel(self.state_dir, channel_id) or {}
        existing_template: dict = {}
        if existing.get("created_by_event"):
            scope = existing.get("scope") if isinstance(existing.get("scope"), dict) else {}
            existing_template = (
                scope.get("template") if isinstance(scope.get("template"), dict) else {}
            )
            if (
                existing_template.get("materialization_digest")
                != materialized["materialization_digest"]
            ):
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=_task_id_from_payload(payload),
                    reason="channel_id already exists with a different template",
                    status_code=409,
                    status="conflict",
                )

        unresolved_skill_refs: list[str] = []
        bound_members: list[dict] = []
        for raw_member in materialized["members"]:
            member = dict(raw_member)
            bound, bind_error = bind_channel_member_profile(
                self.config,
                template_profile_binding_payload(member, template_id=template_id),
                allow_inline_profile=not bool(
                    member.get("profile_selection_explicit")
                ),
            )
            if bind_error or bound is None:
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=_task_id_from_payload(payload),
                    reason=bind_error or "channel template profile binding failed",
                    status_code=422,
                    status="invalid_template",
                )
            unresolved, resolved = _materialize_channel_skill_refs(
                list(bound.get("skill_refs") or []),
                project_root=self.project_root or self.state_dir.parent,
                state_dir=self.state_dir,
                config=self.config,
            )
            unresolved_skill_refs.extend(unresolved)
            role_definition, role_error = resolve_channel_role_definition(
                bound,
                project_root=self.project_root or self.state_dir.parent,
            )
            if role_error:
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=_task_id_from_payload(payload),
                    reason=role_error,
                    status_code=422,
                    status="invalid_template",
                )
            bound["resolved_skill_refs"] = resolved
            bound["_role_definition_snapshot"] = role_definition
            bound_members.append(bound)
        materialized["members"] = bound_members
        unresolved_skill_refs = list(dict.fromkeys(unresolved_skill_refs))
        if unresolved_skill_refs:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason=(
                    "channel template skill refs could not be resolved: "
                    + ", ".join(unresolved_skill_refs)
                ),
                status_code=422,
                status="invalid_template",
            )
        if existing.get("created_by_event"):
            return {
                "_status_code": 200,
                "ok": True,
                "status": "existing",
                "action": action,
                "requested_action": requested_action,
                "channel_id": channel_id,
                "template": existing_template,
                "template_id": template_id,
                "name": name,
                "member_count": len(materialized["members"]),
                "participants": list(
                    materialized["discussion"]["participants"]
                ),
                "max_rounds": int(materialized["discussion"]["max_rounds"]),
            }

        created = self.writer.emit(
            "channel.created",
            actor=self.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "name": name,
                "channel_name": name,
                "thread_id": str(payload.get("thread_id") or "main"),
                "task_id": str(payload.get("task_id") or ""),
                "created_by": str(payload.get("created_by") or self.actor),
                "owner_actor_ref": str(
                    payload.get("owner_actor_ref") or self.actor
                ),
                "owner_delegates": normalize_owner_delegates(
                    payload.get("owner_delegates")
                ),
                "leader_member_id": str(
                    materialized.get("leader_member_id") or ""
                ),
                "leader_revision": 1,
                "origin_binding": (
                    payload.get("origin_binding")
                    if isinstance(payload.get("origin_binding"), dict)
                    else {
                        "surface": self.surface,
                        "channel_id": channel_id,
                        "thread_id": str(payload.get("thread_id") or "main"),
                    }
                ),
                "source": self.surface,
                "scope": {
                    **(
                        payload.get("scope")
                        if isinstance(payload.get("scope"), dict)
                        else {}
                    ),
                    "template": {
                        "id": template_id,
                        "version": materialized["template_version"],
                        "digest": materialized["template_digest"],
                        "materialization_digest": materialized[
                            "materialization_digest"
                        ],
                        "writer_role": materialized["writer_role"],
                        "writer_scope": materialized["writer_scope"],
                    },
                },
            },
        )
        event = created
        for raw_member in materialized["members"]:
            member = dict(raw_member)
            role = normalize_channel_role(member.get("channel_role"))
            provider = normalize_provider(member.get("provider") or member.get("backend"))
            member_type = normalize_member_type(
                member.get("member_type"),
                backend=provider,
            )
            profile = normalize_permission_profile(member.get("permission_profile"))
            skill_refs = list(member.get("skill_refs") or [])
            profile_snapshot = write_channel_profile_snapshot(
                self.state_dir,
                channel_id=channel_id,
                member_id=str(member["member_id"]),
                binding=member,
                resolved_skill_refs=list(
                    member.get("resolved_skill_refs") or []
                ),
                role_definition=(
                    member.get("_role_definition_snapshot")
                    if isinstance(
                        member.get("_role_definition_snapshot"), dict
                    )
                    else {}
                ),
                created_by=self.actor,
                source_event_id=created.id,
            )
            event = self.writer.emit(
                "channel.member.invited",
                actor=self.actor,
                task_id=_task_id_from_payload(payload),
                causation_id=created.id,
                correlation_id=channel_id,
                payload={
                    "channel_id": channel_id,
                    "thread_id": str(payload.get("thread_id") or "main"),
                    "member_id": str(member["member_id"]),
                    "persona": str(member.get("persona") or member["member_id"]),
                    "display_name": str(
                        member.get("display_name")
                        or str(member["member_id"]).replace("_", " ").title()
                    ),
                    "profile_id": str(member.get("profile_id") or ""),
                    "profile_revision": int(
                        member.get("profile_revision") or 1
                    ),
                    "profile_provenance": str(
                        member.get("profile_provenance")
                        or "template_inline"
                    ),
                    "profile_digest": str(
                        profile_snapshot.get("profile_digest") or ""
                    ),
                    "config_digest": str(
                        profile_snapshot.get("config_digest") or ""
                    ),
                    "skill_set_digest": str(
                        profile_snapshot.get("skill_set_digest") or ""
                    ),
                    "permission_digest": str(
                        profile_snapshot.get("permission_digest") or ""
                    ),
                    "role_definition_digest": str(
                        profile_snapshot.get("role_definition_digest") or ""
                    ),
                    "profile_snapshot_ref": str(
                        profile_snapshot.get("ref") or ""
                    ),
                    "profile_snapshot_sha256": str(
                        profile_snapshot.get("sha256") or ""
                    ),
                    "role": role,
                    "channel_role": role,
                    "member_type": member_type,
                    "provider": provider,
                    "backend": str(member.get("backend") or provider),
                    "model": str(member.get("model") or ""),
                    "visibility_profile": normalize_visibility_profile(
                        member.get("visibility_profile"),
                        channel_role=role,
                        member_type=member_type,
                    ),
                    "permission_profile": profile,
                    "write_policy": permission_profile_write_policy(profile),
                    "role_context_ref": str(member.get("role_context_ref") or ""),
                    "skill_refs": skill_refs,
                    "resolved_skill_refs": list(
                        member.get("resolved_skill_refs") or []
                    ),
                    "scope": "channel-template",
                    "writer_scope": list(member.get("writer_scope") or []),
                    "permissions": normalize_permissions(
                        member.get("permissions"),
                        member_type=member_type,
                    ),
                    "source": self.surface,
                },
            )
            if profile != "read_only":
                self.writer.emit(
                    "channel.member.permission_profile.audit",
                    actor=self.actor,
                    causation_id=event.id,
                    correlation_id=channel_id,
                    payload={
                        "channel_id": channel_id,
                        "thread_id": str(payload.get("thread_id") or "main"),
                        "member_id": str(member["member_id"]),
                        "provider": provider,
                        "backend": str(member.get("backend") or provider),
                        "channel_role": role,
                        "permission_profile": profile,
                        "write_policy": permission_profile_write_policy(profile),
                        "dangerous_ack": False,
                        "reason": f"channel template {template_id}",
                        "source": self.surface,
                    },
                )
        discussion = dict(materialized["discussion"])
        requested_mode = str(payload.get("mode") or "").strip()
        if requested_mode:
            discussion["mode"] = normalize_product_discussion_mode(
                requested_mode
            )
        discussion_event_payload = dict(discussion)
        if discussion.get("max_rounds_explicit") is not True:
            discussion_event_payload.pop("max_rounds", None)
        discussion_event_payload.pop("max_rounds_explicit", None)
        event = self.writer.emit(
            "channel.discussion.mode.set",
            actor=self.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=created.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": str(payload.get("thread_id") or "main"),
                **discussion_event_payload,
                "source": self.surface,
            },
        )
        if emit_completion:
            self._completed(
                requested=requested,
                event=event,
                action=action,
                requested_action=requested_action,
                status="created",
                task_id=_task_id_from_payload(payload),
                extra={
                    "channel_id": channel_id,
                    "template_id": template_id,
                    "template_version": materialized["template_version"],
                    "template_digest": materialized["template_digest"],
                    "materialization_digest": materialized[
                        "materialization_digest"
                    ],
                },
            )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "created",
            "action": action,
            "requested_action": requested_action,
            "channel_id": channel_id,
            "name": name,
            "template_id": template_id,
            "template_version": materialized["template_version"],
            "template_digest": materialized["template_digest"],
            "materialization_digest": materialized[
                "materialization_digest"
            ],
            "member_count": len(materialized["members"]),
            "participants": list(materialized["discussion"]["participants"]),
            "max_rounds": int(materialized["discussion"]["max_rounds"]),
            "round_policy": (
                "explicit_cap"
                if materialized["discussion"].get("max_rounds_explicit") is True
                else "synthesis_adaptive"
            ),
            "mode": str(discussion.get("mode") or "conversation"),
            "engine_mode": discussion_engine_mode(
                discussion.get("mode") or "conversation"
            ),
            "event_id": event.id,
        }

    def _channel_create_and_start(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        created = self._channel_create_from_template(
            requested=requested,
            action=action,
            requested_action=requested_action,
            payload=payload,
            emit_completion=False,
        )
        if not created.get("ok"):
            return created

        channel_id = str(created["channel_id"])
        thread_id = str(payload.get("thread_id") or "main")
        message = str(
            payload.get("message")
            or payload.get("objective")
            or payload.get("text")
            or ""
        ).strip()
        message_id = str(
            payload.get("message_id")
            or f"msg-{requested.id.removeprefix('evt-')}"
        )
        events = self.writer.event_log.read_all()
        prior_message = next(
            (
                event
                for event in reversed(events)
                if event.type == "channel.message.posted"
                and str(event.payload.get("channel_id") or "") == channel_id
                and str(event.payload.get("thread_id") or "main") == thread_id
                and str(event.payload.get("message_id") or "") == message_id
            ),
            None,
        )
        prior_start = next(
            (
                event
                for event in reversed(events)
                if event.type == "channel.discussion.started"
                and str(event.payload.get("channel_id") or "") == channel_id
                and str(event.payload.get("thread_id") or "main") == thread_id
                and str(event.payload.get("requirement_message_id") or "")
                == message_id
            ),
            None,
        )
        if prior_message is not None and prior_start is not None:
            started = {
                "ok": True,
                "status": "existing",
                "event_id": prior_message.id,
                "message_id": message_id,
                "participants": list(created.get("participants") or []),
            }
            completion_event = prior_message
        else:
            started = self._channel_discussion_start(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload={
                    **payload,
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "message_id": message_id,
                    "message": message,
                },
                emit_completion=False,
            )
            if not started.get("ok"):
                return {
                    **started,
                    "channel_id": channel_id,
                    "creation_status": str(created.get("status") or ""),
                }
            completion_event = next(
                (
                    event
                    for event in reversed(self.writer.event_log.read_all())
                    if event.id == str(started.get("event_id") or "")
                ),
                None,
            )
        if completion_event is None:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="channel discussion start event is missing",
                status_code=500,
                status="event_missing",
            )

        participants = list(
            started.get("participants")
            or created.get("participants")
            or []
        )
        self._completed(
            requested=requested,
            event=completion_event,
            action=action,
            requested_action=requested_action,
            status="started",
            task_id=_task_id_from_payload(payload),
            extra={
                "channel_id": channel_id,
                "template_id": str(created.get("template_id") or ""),
                "member_count": int(created.get("member_count") or 0),
                "participants": participants,
                "max_rounds": int(created.get("max_rounds") or 0),
                "mode": str(created.get("mode") or "conversation"),
                "engine_mode": str(created.get("engine_mode") or ""),
                "reply_request_count": int(
                    started.get("reply_request_count") or 0
                ),
                "message_id": message_id,
                "thread_id": thread_id,
            },
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "started",
            "action": action,
            "requested_action": requested_action,
            "channel_id": channel_id,
            "name": str(created.get("name") or ""),
            "template_id": str(created.get("template_id") or ""),
            "creation_status": str(created.get("status") or ""),
            "member_count": int(created.get("member_count") or 0),
            "participants": participants,
            "max_rounds": int(created.get("max_rounds") or 0),
            "mode": str(created.get("mode") or "conversation"),
            "engine_mode": str(created.get("engine_mode") or ""),
            "thread_id": thread_id,
            "message_id": message_id,
            "event_id": completion_event.id,
            "reply_request_count": int(
                started.get("reply_request_count") or 0
            ),
        }

    def _channel_discussion_start(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
        emit_completion: bool = True,
    ) -> dict:
        channel_id = _normal_channel_id(_required_text(payload, "channel_id"))
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
        roster = discussion_roster(channel)
        if not roster:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="channel discussion has no routable participants",
                status_code=422,
                status="invalid_discussion",
            )
        thread_id = str(payload.get("thread_id") or "main")
        discussion_config = (
            channel.get("discussion")
            if isinstance(channel.get("discussion"), dict)
            else {}
        )
        product_mode = normalize_product_discussion_mode(
            payload.get("mode") or discussion_config.get("mode")
        )
        sessions = (
            channel.get("discussions")
            if isinstance(channel.get("discussions"), dict)
            else {}
        )
        session = (
            sessions.get(thread_id)
            if isinstance(sessions.get(thread_id), dict)
            else {}
        )
        session_active = (
            bool(session)
            and str(session.get("state") or "idle") != "idle"
        )
        restarted = payload.get("restart") is True and session_active
        if restarted:
            self.writer.emit(
                "channel.discussion.closed",
                actor=self.actor,
                task_id=_task_id_from_payload(payload),
                causation_id=requested.id,
                correlation_id=channel_id,
                payload={
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "discussion_id": str(
                        session.get("discussion_id") or ""
                    ),
                    "outcome": "cancelled",
                    "reason": "explicit_restart",
                    "revision": int(session.get("revision") or 0),
                    "source": self.surface,
                },
            )
            session = {}
        continuing = not restarted and (
            bool(payload.get("continue")) or session_active
        )
        expected_revision = int(payload.get("expected_revision") or 0)
        current_revision = int(session.get("revision") or 0)
        if expected_revision and expected_revision != current_revision:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason=(
                    "channel discussion revision is stale; "
                    f"current revision is {current_revision}"
                ),
                status_code=409,
                status="stale_revision",
            )
        revision = current_revision + 1 if continuing else 1
        max_rounds = int(discussion_config.get("max_rounds") or 0)
        if (
            continuing
            and discussion_config.get("max_rounds_explicit") is True
            and revision > max_rounds
        ):
            closed = self.writer.emit(
                "channel.discussion.closed",
                actor=self.actor,
                task_id=_task_id_from_payload(payload),
                causation_id=requested.id,
                correlation_id=channel_id,
                payload={
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "discussion_id": str(
                        session.get("discussion_id") or ""
                    ),
                    "outcome": "needs_owner",
                    "reason": "discussion_cap_reached",
                    "revision": current_revision,
                    "source": self.surface,
                },
            )
            return {
                "_status_code": 200,
                "ok": True,
                "status": "settled",
                "outcome": "needs_owner",
                "channel_id": channel_id,
                "thread_id": thread_id,
                "discussion_id": str(
                    session.get("discussion_id") or ""
                ),
                "revision": current_revision,
                "event_id": closed.id,
            }
        message_id = str(
            payload.get("message_id")
            or f"msg-{requested.id.removeprefix('evt-')}"
        )
        discussion_id = str(
            session.get("discussion_id")
            or _stable_control_id(
                "discussion",
                channel_id,
                thread_id,
                message_id,
            )
        )
        message = str(
            payload.get("message")
            or payload.get("objective")
            or payload.get("text")
            or "Continue the discussion."
        ).strip()
        context_digest = _stable_control_id(
            "discussion-context",
            discussion_id,
            revision,
            message,
        )
        if continuing:
            self.writer.emit(
                "channel.discussion.continued",
                actor=self.actor,
                task_id=_task_id_from_payload(payload),
                causation_id=requested.id,
                correlation_id=channel_id,
                payload={
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "discussion_id": discussion_id,
                    "revision": revision,
                    "context_digest": context_digest,
                    "product_mode": product_mode,
                    "source": self.surface,
                },
            )
        elif discussion_engine_mode(product_mode) != "fanout_then_synthesis":
            self.writer.emit(
                "channel.discussion.started",
                actor=self.actor,
                task_id=_task_id_from_payload(payload),
                causation_id=requested.id,
                correlation_id=channel_id,
                payload={
                    "schema_version": "channel.discussion.started.v1",
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "discussion_id": discussion_id,
                    "revision": revision,
                    "context_digest": context_digest,
                    "product_mode": product_mode,
                    "state": "active",
                    "trigger": "explicit_discuss",
                    "roster": roster,
                    "synthesizer": str(
                        discussion_config.get("synthesizer")
                        or discussion_config.get("default_responder_id")
                        or roster[0]
                    ),
                    "requirement_message_id": message_id,
                    "source": self.surface,
                },
            )
        refs = (
            dict(payload.get("refs"))
            if isinstance(payload.get("refs"), dict)
            else {}
        )
        refs.update({
            "discussion_id": discussion_id,
            "discussion_revision": revision,
            "discussion_context_digest": context_digest,
            "discussion_product_mode": product_mode,
        })
        source_requirement_message_id = str(
            payload.get("requirement_message_id") or ""
        ).strip()
        if source_requirement_message_id:
            refs["source_requirement_message_id"] = (
                source_requirement_message_id
            )
        if (
            not continuing
            and discussion_engine_mode(product_mode)
            == "fanout_then_synthesis"
        ):
            refs["explicit_discussion_start"] = True
        result = self._channel_post_message(
            requested=requested,
            action=action,
            requested_action=requested_action,
            payload={
                **payload,
                "channel_id": channel_id,
                "thread_id": thread_id,
                "message_id": message_id,
                "text": message.removeprefix("@all ").strip(),
                "mentions": [],
                "refs": refs,
            },
            emit_completion=emit_completion,
        )
        if result.get("ok"):
            result["status"] = (
                "restarted"
                if restarted
                else "continued" if continuing else "started"
            )
            result["participants"] = roster
            result["discussion_id"] = discussion_id
            result["revision"] = revision
            result["context_digest"] = context_digest
        return result


__all__ = ["ChannelTemplateActionsMixin"]
