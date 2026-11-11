"""Apply an Owner-approved leader invitation through existing controlled actions."""
from __future__ import annotations

import hashlib

from zf.core.state.locks import locked_path
from zf.runtime.channel_member_proposals import catalog_digest
from zf.runtime.channel_owner_authority import channel_owner_authority_error
from zf.runtime.channel_profiles import public_channel_profile_catalog
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_sidecar import channel_message_event_payload
from zf.runtime.sidecar_refs import verify_sidecar_ref, SidecarRefError


def decide_member_invitation(service, *, requested, action, requested_action, payload):
    channel_id = str(payload["channel_id"])
    proposal_id = str(payload["proposal_id"])
    with locked_path(service.state_dir / "locks" / ("channel-invitation-" + hashlib.sha256(channel_id.encode()).hexdigest())):
        channel = project_channel(service.state_dir, channel_id) or {}
        proposal = next((p for p in channel.get("member_invitation_proposals") or []
                         if p.get("proposal_id") == proposal_id), None)
        def fail(reason, code=409):
            return service._failed(requested=requested, action=action, requested_action=requested_action, task_id=requested.task_id,
                                   reason=reason, status_code=code, status="invitation_rejected")
        authority = channel_owner_authority_error(channel, actor=service.actor, capability="invite_member")
        if authority:
            return fail(authority, 403)
        if proposal is None:
            return fail("invitation proposal not found", 404)
        if payload.get("member_id") != proposal["member_id"] or str(payload.get("thread_id") or "main") != proposal["thread_id"]:
            return fail("invitation target differs from the approved proposal")
        decision = str(payload.get("decision") or "approved")
        if decision not in {"approved", "rejected"}:
            return fail("decision must be approved or rejected", 422)
        if proposal["status"] != "pending":
            if proposal["status"] != decision:
                return fail("invitation already has a different decision")
            if decision == "approved":
                invited = next((e for e in service.writer.event_log.read_all()
                                if e.type == "channel.member.invited"
                                and e.payload.get("channel_id") == channel_id
                                and e.payload.get("invitation_proposal_id") == proposal_id), None)
                if invited is None:
                    return fail("approved invitation admission evidence is unavailable")
                complete_member_admission(service, requested=requested, proposal=proposal, invited=invited)
            service._completed(requested=requested, event=requested, action=action, requested_action=requested_action, task_id=requested.task_id,
                               status="already_decided", extra={"channel_id": channel_id, "proposal_id": proposal_id})
            return {"ok": True, "_status_code": 200, "status": "already_decided", "proposal_id": proposal_id}
        if (proposal["leader_member_id"] != channel.get("leader_member_id")
                or proposal["leader_revision"] != int(channel.get("leader_revision") or 1)):
            return fail("Channel leader changed; request a new invitation proposal")
        if decision == "rejected":
            event = _record_decision(service, requested, proposal, decision)
            service._completed(requested=requested, event=event, action=action, requested_action=requested_action, task_id=requested.task_id,
                               status="declined", extra={"channel_id": channel_id, "proposal_id": proposal_id})
            return {"ok": True, "_status_code": 202, "status": "declined", "proposal_id": proposal_id}
        profile = next((p for p in public_channel_profile_catalog(service.config)
                        if p["profile_id"] == proposal["profile_id"]), None)
        if profile is None or catalog_digest(profile) != proposal["profile_catalog_digest"]:
            return fail("Profile catalog changed; request a new invitation proposal")
        try:
            verify_sidecar_ref(service.state_dir, {"ref": proposal["artifact_ref"], "sha256": proposal["artifact_digest"]})
        except SidecarRefError as exc:
            return fail(f"invitation evidence is unavailable: {exc}")
        events = service.writer.event_log.read_all()
        invited = next((e for e in events if e.type == "channel.member.invited"
                        and e.payload.get("invitation_proposal_id") == proposal_id), None)
        if invited:
            complete_member_admission(service, requested=requested, proposal=proposal, invited=invited)
            service._completed(requested=requested, event=invited, action=action, requested_action=requested_action,
                               task_id=requested.task_id, status="admitted",
                               extra={"channel_id": channel_id, "proposal_id": proposal_id})
            return {"ok": True, "_status_code": 200, "status": "admitted", "proposal_id": proposal_id}
        previously_admitted = any(
            e.type in {"channel.member.invited", "channel.member.added"}
            and (e.payload or {}).get("channel_id") == channel_id
            and (e.payload or {}).get("member_id") == proposal["member_id"]
            for e in events
        )
        if any(m.get("member_id") == proposal["member_id"]
               and (m.get("status") != "rejected" or previously_admitted)
               for m in channel.get("members") or []):
            return fail("invitation member_id is already in use")
        return service._channel_invite_member(
            requested=requested, action=action, requested_action=requested_action, invitation=proposal,
            payload={"channel_id": channel_id, "thread_id": proposal["thread_id"],
                     "member_id": proposal["member_id"], "profile_id": proposal["profile_id"],
                     "permission_profile": "read_only", "permissions": ["read", "message", "summarize"],
                     "reason": proposal["reason"], "requirement_ref": proposal["artifact_ref"],
                     "requirement_digest": proposal["artifact_digest"]},
        )


def _record_decision(service, requested, proposal, decision):
    return service.writer.emit("channel.member.invitation.decided", actor=service.actor,
                               causation_id=requested.id, correlation_id=proposal["channel_id"], payload={
                                   "channel_id": proposal["channel_id"], "thread_id": proposal["thread_id"],
                                   "proposal_id": proposal["proposal_id"], "member_id": proposal["member_id"],
                                   "decision": decision,
                               })


def complete_member_admission(service, *, requested, proposal, invited):
    from zf.runtime.channel_router import route_channel_message
    from zf.runtime.channel_message_ingress import dispatch_channel_replies_background

    channel_id, thread_id = proposal["channel_id"], proposal["thread_id"]
    proposal_id, member_id = proposal["proposal_id"], proposal["member_id"]
    events = service.writer.event_log.read_all()
    decision = next((e for e in events if e.type == "channel.member.invitation.decided"
                     and e.payload.get("channel_id") == channel_id
                     and e.payload.get("proposal_id") == proposal_id
                     and e.payload.get("decision") == "approved"), None)
    if decision is None:
        decision = _record_decision(service, requested, proposal, "approved")
    channel = project_channel(service.state_dir, channel_id) or {}
    if channel.get("discussion", {}).get("mode") == "libre_discuss":
        return  # Admission alone never authorizes a conversation or review round.
    session = channel.get("discussions", {}).get(thread_id) or {}
    if not session:
        # The explicit admission is retained for the first formal discussion.
        from zf.runtime.channel_discussion import start_discussion
        requirement = next((m for m in channel.get("messages") or []
                            if m.get("thread_id", "main") == thread_id and m.get("role") == "user"), {})
        start_discussion(service.writer, channel, actor=service.actor, channel_id=channel_id,
                                 thread_id=thread_id, trigger_message_id=str(requirement.get("message_id") or ""),
                                 trigger="member_admission", source=service.surface, causation_id=invited.id)
        channel = project_channel(service.state_dir, channel_id) or {}
        session = channel["discussions"][thread_id]
    events = service.writer.event_log.read_all()
    continued = next((e for e in events if e.type == "channel.discussion.continued"
                      and e.payload.get("invitation_proposal_id") == proposal_id), None)
    message_id = f"msg-admission-{proposal_id}"
    if continued is None:
        participants = list(dict.fromkeys([*(session.get("participants") or session.get("roster") or []), member_id]))
        reviewers = list(session.get("review_roster") or session.get("roster") or [])
        if proposal.get("required_reviewer") and member_id not in reviewers:
            reviewers.append(member_id)
        unfinished = list(session.get("roster") or []) if session.get("state") == "phase1_blind" else []
        targets = list(dict.fromkeys([*unfinished, member_id]))
        continued = service.writer.emit("channel.discussion.continued", actor=service.actor,
                                        causation_id=invited.id, correlation_id=channel_id, payload={
                                            "channel_id": channel_id, "thread_id": thread_id,
                                            "discussion_id": session.get("discussion_id"),
                                            "revision": int(session.get("revision") or 1) + 1,
                                            "context_digest": hashlib.sha256(f"{session.get('context_digest')}:{proposal_id}".encode()).hexdigest(),
                                            "roster": targets, "participants": participants, "review_roster": reviewers,
                                            "requirement_message_id": session.get("requirement_message_id"),
                                            "adaptive_next_round_message_id": message_id,
                                            "invitation_proposal_id": proposal_id, "reason": proposal["reason"],
                                        })
    targets = continued.payload["roster"]
    if not any(e.type == "channel.discussion.phase.changed" and e.causation_id == continued.id for e in events):
        service.writer.emit("channel.discussion.phase.changed", actor=service.actor, causation_id=continued.id,
                            correlation_id=channel_id, payload={"channel_id": channel_id, "thread_id": thread_id,
                                                                "phase": "phase1_blind", "reason": "member_admitted"})
    message = next((e for e in events if e.type == "channel.message.posted" and e.payload.get("message_id") == message_id), None)
    if message is None:
        body = channel_message_event_payload(service.state_dir, {
            "channel_id": channel_id, "thread_id": thread_id, "message_id": message_id,
            "member_id": "operator", "role": "user", "source": "runtime", "mentions": targets,
            "text": " ".join(f"@{target}" for target in targets)
                    + f" Owner approved member admission: event:{decision.id}; member:{member_id}; proposal:{proposal_id}. "
                    + "Review the original requirement and contribute your assigned perspective. Invitation rationale: "
                    + proposal["reason"],
            "refs": {"adaptive_next_round_id": proposal_id, "invitation_proposal_id": proposal_id,
                     "member_admission_decision_event_id": decision.id, "member_admission_event_id": invited.id,
                     "discussion_revision": continued.payload["revision"]},
        }, created_by=service.actor, source_event_id=continued.id)
        message = service.writer.emit("channel.message.posted", actor=service.actor, causation_id=continued.id,
                                      correlation_id=channel_id, payload=body)
    route_channel_message(state_dir=service.state_dir, writer=service.writer, message_event=message,
                          message_payload=message.payload, actor=service.actor, source=service.surface,
                          project_root=service.project_root, config=service.config,
                          openclaw_client=service.openclaw_client, dispatch_inline=False)
    dispatch_channel_replies_background(state_dir=service.state_dir, channel_id=channel_id,
                                       actor=service.actor, source=service.surface, project_root=service.project_root,
                                       config=service.config, openclaw_client=service.openclaw_client, max_dispatch=len(targets))
