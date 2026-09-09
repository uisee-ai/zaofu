from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.schema import (
    ChannelAgentProfileConfig,
    ChannelConfig,
    ProjectConfig,
    ZfConfig,
)
from zf.core.events import EventWriter, ZfEvent
from zf.core.events.log import EventLog
from zf.core.security.redaction import redact_obj
from zf.core.task.store import TaskStore
from zf.runtime.channel_context import build_channel_context_pack
from zf.runtime.channel_contract_artifacts import (
    persist_channel_prd,
    persist_channel_prd_readiness,
)
from zf.runtime.channel_adapter import dispatch_reply_request
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_workflow_authority import (
    channel_authority_context_from_submit_payload,
    channel_workflow_authority_error,
)
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.kanban_plan_requests import (
    PLAN_REQUESTED_EVENT,
    plan_response_gate,
)
from zf.runtime.provider_permissions import (
    build_provider_permission_snapshot,
    provider_permission_drift,
)
from zf.web.proposal_extraction import extract_action_proposal
from zf.web.plan_extraction import extract_plan_request


ROOT = Path(__file__).resolve().parents[1]


def _runtime(
    tmp_path: Path,
    *,
    config: ZfConfig | None = None,
) -> tuple[Path, EventLog, EventWriter, ControlledActionService]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    service = ControlledActionService(
        state_dir,
        writer,
        actor="web",
        source="channel",
        surface="web",
        config=config,
        project_root=tmp_path,
    )
    return state_dir, log, writer, service


def _execute(
    service: ControlledActionService,
    writer: EventWriter,
    action: str,
    payload: dict,
) -> dict:
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        payload={"action": action, "request": redact_obj(payload)},
    )
    return service.execute(
        action=action,
        requested_action=action,
        payload=payload,
        requested=requested,
    )


def test_channel_profile_catalog_loads_exact_revision_and_ceilings(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "zf.yaml"
    config_path.write_text(
        """\
version: "1.0"
project:
  name: product
  state_dir: .zf
channel:
  agent_profiles:
    product-pm:
      revision: 3
      persona: Product facilitator
      display_name: Product PM
      channel_role: product_pm
      provider: claude-code
      backend: claude-code
      skill_refs:
        - skills/zf-channel-discussion-participant/SKILL.md
      visibility_ceiling: planner
      permission_ceiling: read_only
      lifecycle: persistent
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    profile = config.channel.agent_profiles["product-pm"]
    assert profile.revision == 3
    assert profile.skill_refs == [
        "skills/zf-channel-discussion-participant/SKILL.md"
    ]
    assert profile.visibility_ceiling == "planner"
    assert profile.permission_ceiling == "read_only"

    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "revision: 3",
            "revision: 0",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="revision"):
        load_config(config_path)


def test_message_ingress_ack_retry_reply_pin_read_and_restart(
    tmp_path: Path,
) -> None:
    state_dir, log, writer, service = _runtime(tmp_path)
    assert _execute(
        service,
        writer,
        "channel-create",
        {"channel_id": "ch-product", "name": "Product"},
    )["ok"]

    first = _execute(
        service,
        writer,
        "channel-post-message",
        {
            "channel_id": "ch-product",
            "thread_id": "main",
            "client_message_id": "client-1",
            "text": "Define the release requirement.",
        },
    )
    duplicate = _execute(
        service,
        writer,
        "channel-post-message",
        {
            "channel_id": "ch-product",
            "thread_id": "main",
            "client_message_id": "client-1",
            "text": "This retry must not replace the accepted body.",
        },
    )
    invalid_reply = _execute(
        service,
        writer,
        "channel-post-message",
        {
            "channel_id": "ch-product",
            "thread_id": "main",
            "client_message_id": "client-invalid-reply",
            "reply_to_message_id": "msg-missing",
            "text": "Reply",
        },
    )
    assistant = _execute(
        service,
        writer,
        "channel-post-message",
        {
            "channel_id": "ch-product",
            "thread_id": "main",
            "client_message_id": "client-agent-1",
            "member_id": "product-pm",
            "role": "assistant",
            "text": "The first PRD draft is ready.",
        },
    )
    assert _execute(
        service,
        writer,
        "channel-pin-message",
        {
            "channel_id": "ch-product",
            "thread_id": "main",
            "message_id": assistant["message_id"],
            "member_id": "operator",
        },
    )["ok"]

    before_read = project_channel(state_dir, "ch-product") or {}
    assert before_read["unread_count"] == 1
    assert assistant["message_id"] in before_read["pinned_message_ids"]
    assert first["receipt"]["status"] == "accepted"
    assert duplicate["receipt"]["status"] == "accepted"
    assert duplicate["duplicate"] is True
    assert duplicate["event_id"] == first["event_id"]
    assert invalid_reply["receipt"]["status"] == "rejected"
    assert sum(
        event.type == "channel.message.posted"
        and event.payload.get("client_message_id") == "client-1"
        for event in log.read_all()
    ) == 1

    assert _execute(
        service,
        writer,
        "channel-mark-read",
        {
            "channel_id": "ch-product",
            "thread_id": "main",
            "member_id": "operator",
            "message_id": assistant["message_id"],
        },
    )["ok"]
    restarted_log = EventLog(state_dir / "events.jsonl")
    restarted_writer = EventWriter(restarted_log)
    restarted = ControlledActionService(
        state_dir,
        restarted_writer,
        actor="web",
        source="channel",
        surface="web",
        project_root=tmp_path,
    )
    replay = _execute(
        restarted,
        restarted_writer,
        "channel-post-message",
        {
            "channel_id": "ch-product",
            "thread_id": "main",
            "client_message_id": "client-1",
            "text": "Retry after restart.",
        },
    )
    detail = project_channel(state_dir, "ch-product") or {}
    assert replay["duplicate"] is True
    assert detail["unread_count"] == 0
    assert assistant["message_id"] in detail["pinned_message_ids"]


def test_concurrent_same_thread_ingress_is_durable_and_unique(
    tmp_path: Path,
) -> None:
    state_dir, log, writer, service = _runtime(tmp_path)
    assert _execute(
        service,
        writer,
        "channel-create",
        {"channel_id": "ch-product", "name": "Product"},
    )["ok"]

    def post(index: int) -> dict:
        local_writer = EventWriter(EventLog(state_dir / "events.jsonl"))
        local_service = ControlledActionService(
            state_dir,
            local_writer,
            actor="web",
            source="channel",
            surface="web",
            project_root=tmp_path,
        )
        return _execute(
            local_service,
            local_writer,
            "channel-post-message",
            {
                "channel_id": "ch-product",
                "thread_id": "main",
                "client_message_id": f"client-{index}",
                "text": f"message {index}",
            },
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(post, range(12)))

    assert all(item["ok"] for item in results)
    assert len({item["event_id"] for item in results}) == 12
    posted = [
        event
        for event in log.read_all()
        if event.type == "channel.message.posted"
        and event.payload.get("channel_id") == "ch-product"
    ]
    assert len(posted) == 12
    assert len({
        event.payload["client_message_id"] for event in posted
    }) == 12
    detail = project_channel(state_dir, "ch-product") or {}
    assert len(detail["messages"]) == 12
    assert len(detail["threads"][0]["message_ids"]) == 12


def test_message_ingress_append_failure_returns_nack_without_timeline_entry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir, log, writer, service = _runtime(tmp_path)
    assert _execute(
        service,
        writer,
        "channel-create",
        {"channel_id": "ch-product", "name": "Product"},
    )["ok"]
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        payload={"action": "channel-post-message", "request": {}},
    )
    original_emit = service.writer.emit

    def fail_posted(event_type: str, *args, **kwargs):
        if event_type == "channel.message.posted":
            raise OSError("simulated append failure")
        return original_emit(event_type, *args, **kwargs)

    monkeypatch.setattr(service.writer, "emit", fail_posted)
    result = service.execute(
        action="channel-post-message",
        requested_action="channel-post-message",
        payload={
            "channel_id": "ch-product",
            "thread_id": "main",
            "client_message_id": "client-failed",
            "text": "This message must not be accepted.",
        },
        requested=requested,
    )

    assert result["_status_code"] == 503
    assert result["receipt"]["status"] == "rejected"
    assert "simulated append failure" in result["receipt"]["reason"]
    assert not any(
        event.type == "channel.message.posted"
        and event.payload.get("client_message_id") == "client-failed"
        for event in log.read_all()
    )
    assert not (project_channel(state_dir, "ch-product") or {})["messages"]


def test_profile_binding_pins_context_and_blocks_ceiling_drift(
    tmp_path: Path,
) -> None:
    role_dir = tmp_path / "channel_roles"
    role_dir.mkdir()
    role_path = role_dir / "product-pm.md"
    role_path.write_text(
        "# Product PM\n\nClarify the product requirement, then hand off a PRD.\n",
        encoding="utf-8",
    )
    profile = ChannelAgentProfileConfig(
        revision=3,
        persona="Product facilitator",
        display_name="Product PM",
        channel_role="product_pm",
        provider="fake",
        backend="fake",
        visibility_ceiling="planner",
        permission_ceiling="read_only",
        lifecycle="persistent",
        role_context_ref="channel_roles/product-pm.md",
    )
    config = ZfConfig(
        project=ProjectConfig(name="product"),
        channel=ChannelConfig(agent_profiles={"product-pm": profile}),
    )
    state_dir, _log, writer, service = _runtime(tmp_path, config=config)
    assert _execute(
        service,
        writer,
        "channel-create",
        {"channel_id": "ch-profile", "name": "Product"},
    )["ok"]
    invited = _execute(
        service,
        writer,
        "channel-invite-member",
        {
            "channel_id": "ch-profile",
            "member_id": "pm-1",
            "profile_id": "product-pm",
        },
    )
    rejected = _execute(
        service,
        writer,
        "channel-invite-member",
        {
            "channel_id": "ch-profile",
            "member_id": "pm-writer",
            "profile_id": "product-pm",
            "permission_profile": "project_writer",
        },
    )

    assert invited["ok"] is True
    assert rejected["ok"] is False
    assert "exceeds profile ceiling" in rejected["reason"]
    detail = project_channel(state_dir, "ch-profile") or {}
    member = next(
        item for item in detail["members"]
        if item["member_id"] == "pm-1"
    )
    assert member["profile_id"] == "product-pm"
    assert member["profile_revision"] == 3
    assert len(member["profile_digest"]) == 64
    assert len(member["config_digest"]) == 64
    assert len(member["permission_digest"]) == 64
    assert len(member["role_definition_digest"]) == 64
    assert Path(state_dir / member["profile_snapshot_ref"]).is_file()
    snapshot = json.loads(
        (state_dir / member["profile_snapshot_ref"]).read_text(
            encoding="utf-8"
        )
    )
    assert snapshot["role_definition"]["source"] == "project"
    assert "Clarify the product requirement" in snapshot["role_definition"][
        "excerpt"
    ]
    role_path.unlink()
    context = build_channel_context_pack(
        detail,
        channel_id="ch-profile",
        thread_id="main",
        target_member_id="pm-1",
        trigger_message_id="",
        profile_binding=member,
        role_context_ref=member["role_context_ref"],
        state_dir=state_dir,
        project_root=tmp_path,
    )
    assert context["profile_revision"] == 3
    assert context["profile_snapshot_ref"] == member["profile_snapshot_ref"]
    assert context["role_definition"] == snapshot["role_definition"]

    previous = build_provider_permission_snapshot(
        backend="fake",
        permission_profile="read_only",
        cwd=tmp_path,
        profile_id="product-pm",
        profile_revision=3,
        profile_digest=member["profile_digest"],
        config_digest=member["config_digest"],
        skill_set_digest=member["skill_set_digest"],
        permission_digest=member["permission_digest"],
        profile_snapshot_ref=member["profile_snapshot_ref"],
        profile_snapshot_sha256=member["profile_snapshot_sha256"],
    )
    current = {
        **previous,
        "profile_revision": 4,
        "profile_digest": "f" * 64,
    }
    drift = provider_permission_drift(previous, current)
    assert drift["status"] == "blocking"
    assert {item["field"] for item in drift["items"]} >= {
        "profile_revision",
        "profile_digest",
    }


def test_project_profile_with_missing_role_definition_is_rejected_before_invite(
    tmp_path: Path,
) -> None:
    profile = ChannelAgentProfileConfig(
        revision=1,
        persona="Missing role",
        display_name="Missing role",
        channel_role="critic",
        provider="fake",
        backend="fake",
        permission_ceiling="read_only",
        role_context_ref="channel_roles/missing-role.md",
    )
    config = ZfConfig(
        project=ProjectConfig(name="product"),
        channel=ChannelConfig(agent_profiles={"missing-role": profile}),
    )
    state_dir, _log, writer, service = _runtime(tmp_path, config=config)
    assert _execute(
        service,
        writer,
        "channel-create",
        {"channel_id": "ch-profile-missing", "name": "Product"},
    )["ok"]

    result = _execute(
        service,
        writer,
        "channel-invite-member",
        {
            "channel_id": "ch-profile-missing",
            "member_id": "critic-1",
            "profile_id": "missing-role",
        },
    )

    assert result["ok"] is False
    assert result["status"] == "rejected"
    assert "role definition is missing" in result["reason"]
    rejected_member = (project_channel(
        state_dir,
        "ch-profile-missing",
    ) or {})["members"][0]
    assert rejected_member["status"] == "rejected"
    assert not rejected_member.get("profile_snapshot_ref")


def test_roster_proposal_uses_shared_kanban_contract() -> None:
    answer = json.dumps({
        "action_proposal": {
            "action": "channel.add_member",
            "reason": "Add a focused product reviewer.",
            "payload": {
                "channel_id": "ch-product",
                "member_id": "critic-1",
                "profile_id": "product-critic",
                "provider": "claude-code",
                "channel_role": "critic",
                "permission_profile": "read_only",
            },
        },
    })

    proposal = extract_action_proposal(answer)

    assert proposal is not None
    assert proposal["valid"] is True
    assert proposal["action"] == "channel-invite-member"
    assert proposal["payload"]["profile_id"] == "product-critic"


def test_roster_proposal_requires_catalog_profile_identity() -> None:
    answer = json.dumps({
        "action_proposal": {
            "action": "channel.add_member",
            "reason": "Add an unbound reviewer.",
            "payload": {
                "channel_id": "ch-product",
                "member_id": "critic-1",
                "provider": "claude-code",
            },
        },
    })

    proposal = extract_action_proposal(answer)

    assert proposal is not None
    assert proposal["valid"] is False
    assert "profile_id is required" in proposal["validation_error"]


def test_removed_member_cannot_consume_a_preexisting_reply_request(
    tmp_path: Path,
) -> None:
    state_dir, log, writer, service = _runtime(tmp_path)
    assert _execute(
        service,
        writer,
        "channel-create",
        {"channel_id": "ch-product", "name": "Product"},
    )["ok"]
    assert _execute(
        service,
        writer,
        "channel-invite-member",
        {
            "channel_id": "ch-product",
            "member_id": "critic-1",
            "provider": "fake",
            "channel_role": "critic",
            "permission_profile": "read_only",
            "permissions": ["read", "message"],
        },
    )["ok"]
    message = writer.emit(
        "channel.message.posted",
        actor="operator",
        correlation_id="ch-product",
        payload={
            "channel_id": "ch-product",
            "thread_id": "main",
            "message_id": "msg-queued",
            "member_id": "operator",
            "role": "user",
            "source": "test",
            "text": "@critic-1 review this",
        },
    )
    writer.emit(
        "channel.agent.reply.requested",
        actor="test",
        causation_id=message.id,
        correlation_id="ch-product",
        payload={
            "channel_id": "ch-product",
            "thread_id": "main",
            "request_id": "reply-queued",
            "message_id": "msg-queued",
            "target_member_id": "critic-1",
            "context_pack_id": "ctx-queued",
            "source": "test",
        },
    )
    assert _execute(
        service,
        writer,
        "channel-remove-member",
        {"channel_id": "ch-product", "member_id": "critic-1"},
    )["ok"]

    result = dispatch_reply_request(
        state_dir=state_dir,
        writer=writer,
        channel_id="ch-product",
        request_id="reply-queued",
        actor="test",
        source="test",
    )

    assert result.failed == ["reply-queued"]
    assert result.skipped[0]["reason"] == "target_member_inactive"
    events = log.read_all()
    assert not [
        event
        for event in events
        if event.type == "channel.agent.reply.started"
        and event.payload.get("request_id") == "reply-queued"
    ]
    failure = next(
        event
        for event in events
        if event.type == "channel.agent.reply.failed"
        and event.payload.get("request_id") == "reply-queued"
    )
    assert failure.payload["reason"] == "target_member_inactive"


@pytest.mark.parametrize(
    ("attachment", "reason"),
    [
        (
            {
                "name": "secret.txt",
                "mime": "text/plain",
                "size": 8,
                "content": "token=should-not-enter-events",
            },
            "metadata/ref only",
        ),
        (
            {
                "name": "../outside.txt",
                "mime": "text/plain",
                "size": 8,
                "uri": "file:///tmp/outside.txt",
            },
            "must not contain a path",
        ),
    ],
)
def test_illegal_attachment_fails_before_message_or_provider_dispatch(
    tmp_path: Path,
    attachment: dict,
    reason: str,
) -> None:
    state_dir, log, writer, service = _runtime(tmp_path)
    assert _execute(
        service,
        writer,
        "channel-create",
        {"channel_id": "ch-product", "name": "Product"},
    )["ok"]

    result = _execute(
        service,
        writer,
        "channel-post-message",
        {
            "channel_id": "ch-product",
            "client_message_id": "client-illegal",
            "text": "Inspect the attachment.",
            "refs": {"attachments": [attachment]},
        },
    )

    assert result["ok"] is False
    assert result["status"] == "message_rejected"
    assert reason in result["reason"]
    assert (
        result["receipt"]["schema_version"]
        == "channel.message.ingress_receipt.v1"
    )
    events = log.read_all()
    assert not [
        event
        for event in events
        if event.type
        in {
            "channel.message.posted",
            "channel.agent.reply.requested",
            "channel.attachment.uploaded",
        }
        and event.payload.get("client_message_id") == "client-illegal"
    ]
    assert "should-not-enter-events" not in (
        state_dir / "events.jsonl"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("scope", "expires_at", "allowed", "reason"),
    [
        (
            ["channel.consensus.confirm"],
            "2999-01-01T00:00:00Z",
            True,
            "",
        ),
        (
            ["channel.consensus.confirm"],
            "2000-01-01T00:00:00Z",
            False,
            "expired",
        ),
        (
            ["channel.consensus.block"],
            "2999-01-01T00:00:00Z",
            False,
            "does not include",
        ),
    ],
)
def test_owner_delegate_requires_exact_scope_expiry_and_authorization(
    tmp_path: Path,
    scope: list[str],
    expires_at: str,
    allowed: bool,
    reason: str,
) -> None:
    state_dir, _log, writer, owner_service = _runtime(tmp_path)
    assert _execute(
        owner_service,
        writer,
        "channel-create",
        {
            "channel_id": "ch-product",
            "name": "Product",
            "owner_actor_ref": "web",
            "owner_delegates": [{
                "actor_ref": "delegate:alice",
                "scope": scope,
                "expires_at": expires_at,
                "authorization_ref": "owner-authz:delegate-alice",
            }],
        },
    )["ok"]
    writer.emit(
        "channel.consensus.proposed",
        actor="product-pm",
        correlation_id="ch-product",
        payload={
            "channel_id": "ch-product",
            "thread_id": "main",
            "artifact_ref": "channels/ch-product/prd/r1.json",
            "artifact_digest": "a" * 64,
            "prd_ref": "channels/ch-product/prd/r1.json",
            "prd_digest": "a" * 64,
            "prd_revision": 1,
            "readiness_verdict": "ready",
            "source": "test",
        },
    )
    delegate_service = ControlledActionService(
        state_dir,
        writer,
        actor="delegate:alice",
        source="channel",
        surface="web",
        project_root=tmp_path,
    )

    result = _execute(
        delegate_service,
        writer,
        "channel-consensus-confirm",
        {
            "channel_id": "ch-product",
            "thread_id": "main",
            "artifact_ref": "channels/ch-product/prd/r1.json",
            "artifact_digest": "a" * 64,
            "prd_revision": 1,
        },
    )

    assert result["ok"] is allowed
    if allowed:
        assert result["status"] == "confirmed"
        detail = project_channel(state_dir, "ch-product") or {}
        assert (
            detail["consensus"]["main"]["human_confirmed_by"]
            == "delegate:alice"
        )
    else:
        assert result["status"] == "forbidden"
        assert reason in result["reason"]


def test_owner_cas_leader_binding_controls_exact_prd_workflow_authority(
    tmp_path: Path,
) -> None:
    state_dir, _log, writer, service = _runtime(tmp_path)
    assert _execute(
        service,
        writer,
        "channel-create",
        {
            "channel_id": "ch-product",
            "name": "Product",
            "owner_actor_ref": "web",
        },
    )["ok"]
    for member_id in ("pm-1", "pm-2"):
        assert _execute(
            service,
            writer,
            "channel-invite-member",
            {
                "channel_id": "ch-product",
                "member_id": member_id,
                "provider": "fake",
                "channel_role": "product_pm",
                "permission_profile": "read_only",
                "permissions": [
                    "read",
                    "message",
                    "summarize",
                    "propose_workflow",
                ],
            },
        )["ok"]
    first_leader = _execute(
        service,
        writer,
        "channel-set-leader",
        {
            "channel_id": "ch-product",
            "leader_member_id": "pm-1",
            "expected_revision": 0,
            "idempotency_key": "leader-r1",
        },
    )
    assert first_leader["leader_revision"] == 1
    writer.emit(
        "channel.consensus.proposed",
        actor="pm-1",
        correlation_id="ch-product",
        payload={
            "channel_id": "ch-product",
            "thread_id": "main",
            "artifact_ref": "channels/ch-product/prd/r1.json",
            "artifact_digest": "a" * 64,
            "prd_ref": "channels/ch-product/prd/r1.json",
            "prd_digest": "a" * 64,
            "prd_revision": 1,
            "owner_actor_ref": "web",
            "proposed_by": "pm-1",
            "source": "test",
        },
    )
    writer.emit(
        "channel.consensus.reached",
        actor="web",
        correlation_id="ch-product",
        payload={
            "channel_id": "ch-product",
            "thread_id": "main",
            "prd_ref": "channels/ch-product/prd/r1.json",
            "prd_digest": "a" * 64,
            "prd_revision": 1,
            "confirmed_by": "web",
            "source": "test",
        },
    )
    exact = {
        "channel_id": "ch-product",
        "thread_id": "main",
        "channel_member_id": "pm-1",
        "leader_revision": 1,
        "prd_revision": 1,
        "source_ref": "channels/ch-product/prd/r1.json",
        "source_digest": "a" * 64,
    }
    assert channel_workflow_authority_error(state_dir, exact) == ""
    assert "exact Channel Leader" in channel_workflow_authority_error(
        state_dir,
        {**exact, "channel_member_id": "pm-2"},
    )
    assert "digest is stale" in channel_workflow_authority_error(
        state_dir,
        {**exact, "source_digest": "b" * 64},
    )
    assert _execute(
        service,
        writer,
        "channel-remove-member",
        {"channel_id": "ch-product", "member_id": "pm-1"},
    )["status"] == "leader_binding_conflict"

    second_leader = _execute(
        service,
        writer,
        "channel-set-leader",
        {
            "channel_id": "ch-product",
            "leader_member_id": "pm-2",
            "expected_revision": 1,
            "idempotency_key": "leader-r2",
        },
    )
    assert second_leader["leader_revision"] == 2
    assert "exact Channel Leader" in channel_workflow_authority_error(
        state_dir,
        exact,
    )
    assert "revision is stale" in channel_workflow_authority_error(
        state_dir,
        {
            **exact,
            "channel_member_id": "pm-2",
        },
    )
    assert channel_workflow_authority_error(
        state_dir,
        {
            **exact,
            "channel_member_id": "pm-2",
            "leader_revision": 2,
        },
    ) == ""


def _leader_plan_fixture(
    tmp_path: Path,
) -> tuple[Path, EventWriter, ControlledActionService, dict[str, object]]:
    state_dir, _log, writer, service = _runtime(tmp_path)
    assert _execute(
        service,
        writer,
        "channel-create",
        {"channel_id": "ch-plan", "name": "Plan", "owner_actor_ref": "web"},
    )["ok"]
    assert _execute(
        service,
        writer,
        "channel-invite-member",
        {
            "channel_id": "ch-plan",
            "member_id": "leader-1",
            "provider": "fake",
            "channel_role": "product_pm",
            "permissions": [
                "read",
                "message",
                "summarize",
                "propose_workflow",
            ],
        },
    )["ok"]
    assert _execute(
        service,
        writer,
        "channel-set-leader",
        {
            "channel_id": "ch-plan",
            "leader_member_id": "leader-1",
            "expected_revision": 0,
            "idempotency_key": "leader-plan-r1",
        },
    )["ok"]
    spec_ref = "channel-artifacts/ch-plan/spec-r1.md"
    spec_path = state_dir / spec_ref
    spec_path.parent.mkdir(parents=True)
    spec_body = (
        "# Plan PRD\n\n## Acceptance Criteria\n"
        "- Delivery passes `python -m pytest -q`.\n"
    )
    spec_path.write_text(spec_body, encoding="utf-8")
    spec_digest = hashlib.sha256(spec_body.encode("utf-8")).hexdigest()
    readiness = persist_channel_prd_readiness(
        state_dir,
        channel_id="ch-plan",
        thread_id="main",
        revision=1,
        body={
            "verdict": "ready",
            "implementation_start": True,
            "gaps": [],
        },
        created_by="leader-1",
        source_event_id="evt-plan-synthesis",
    )
    prd = persist_channel_prd(
        state_dir,
        channel_id="ch-plan",
        thread_id="main",
        revision=1,
        previous_ref="",
        previous_digest="",
        body={
            "summary": "Deliver the confirmed Channel PRD.",
            "title": "Plan PRD",
            "synthesis": {
                "acceptance_criteria": ["The declared verification passes."],
                "out_of_scope": ["Unconfirmed scope"],
            },
            "markdown": spec_body,
            "spec_path": spec_ref,
            "spec_digest": spec_digest,
        },
        readiness_descriptor=readiness,
        created_by="leader-1",
        source_event_id="evt-plan-synthesis",
    )
    consensus_payload = {
        "channel_id": "ch-plan",
        "thread_id": "main",
        "artifact_ref": prd["ref"],
        "artifact_digest": prd["sha256"],
        "prd_ref": prd["ref"],
        "prd_digest": prd["sha256"],
        "prd_revision": 1,
        "readiness_ref": readiness["ref"],
        "readiness_digest": readiness["sha256"],
        "readiness_verdict": "ready",
        "implementation_start": True,
        "spec_path": spec_ref,
        "spec_digest": spec_digest,
    }
    writer.emit(
        "channel.synthesis.proposed",
        actor="leader-1",
        correlation_id="ch-plan",
        payload={
            **consensus_payload,
            "summary": "Deliver the confirmed Channel PRD.",
            "open_questions": [],
        },
    )
    writer.emit(
        "channel.consensus.proposed",
        actor="leader-1",
        correlation_id="ch-plan",
        payload={**consensus_payload, "proposed_by": "leader-1"},
    )
    writer.emit(
        "channel.consensus.reached",
        actor="web",
        correlation_id="ch-plan",
        payload={**consensus_payload, "confirmed_by": "web"},
    )
    authority: dict[str, object] = {
        "channel_id": "ch-plan",
        "thread_id": "main",
        "channel_member_id": "leader-1",
        "leader_revision": 1,
        "prd_revision": 1,
        "source_ref": prd["ref"],
        "source_digest": prd["sha256"],
    }
    return state_dir, writer, service, authority


def _task_create_plan_request(
    authority: dict[str, object],
    *,
    workflow_plan: dict | None = None,
) -> dict:
    request = extract_plan_request(
        json.dumps({
            "plan_request": {
                "subject_type": "task_create",
                "header": "Create PRD Task",
                "id": "create-prd-task",
                "question": "How should the confirmed PRD become work?",
                "options": [
                    {
                        "id": "full",
                        "label": "Full delivery (Recommended)",
                        "recommended": True,
                        "description": "Create the complete delivery Task.",
                        "effect": {
                            "mode": "propose",
                            "action": "create-task",
                            "payload": {
                                "title": "Deliver the full Channel PRD",
                                "objective": "Implement the complete confirmed PRD.",
                                "acceptance": "All acceptance checks pass.",
                                "priority": 2,
                                **({"workflow_plan": workflow_plan}
                                   if workflow_plan is not None else {}),
                            },
                        },
                    },
                    {
                        "id": "focused",
                        "label": "Focused delivery",
                        "description": "Create a focused delivery Task.",
                        "effect": {
                            "mode": "propose",
                            "action": "create-task",
                            "payload": {
                                "title": "Deliver the focused Channel PRD",
                                "objective": "Implement the confirmed core scope.",
                                "acceptance": "Focused acceptance checks pass.",
                                "priority": 3,
                            },
                        },
                    },
                    {
                        "id": "continue",
                        "label": "Continue discussion",
                        "description": "Keep refining before creating work.",
                        "effect": {"mode": "continue"},
                    },
                ],
                "allow_other": False,
            },
        }),
        plan_context={"workflow_parameters": authority},
    )
    assert request is not None and request["valid"] is True
    return request


def test_every_channel_task_create_plan_option_is_selectable(
    tmp_path: Path,
) -> None:
    _state_dir, writer, _service, authority = _leader_plan_fixture(tmp_path)
    request = _task_create_plan_request(authority)
    event = ZfEvent(
        type=PLAN_REQUESTED_EVENT,
        actor="kanban-agent",
        payload={"request": request, "plan_request": request},
    )
    writer.append(event)

    modes = {}
    for option in request["options"]:
        gate = plan_response_gate(
            [event],
            request_event_id=event.id,
            request_id=request["request_id"],
            revision=request["revision"],
            question_id=request["question_id"],
            option_id=option["id"],
            answer=option["label"],
        )
        assert gate["ok"] is True, gate
        modes[option["id"]] = gate["submit_mode"]

    assert modes == {
        "full": "propose",
        "focused": "propose",
        "continue": "continue",
    }


@pytest.mark.parametrize(
    ("option_id", "expected_title"),
    [
        ("full", "Deliver the full Channel PRD"),
        ("focused", "Deliver the focused Channel PRD"),
    ],
)
def test_each_executable_channel_task_option_requires_approve_then_creates(
    tmp_path: Path,
    option_id: str,
    expected_title: str,
) -> None:
    state_dir, writer, service, authority = _leader_plan_fixture(tmp_path)
    request = _task_create_plan_request(authority)
    event = ZfEvent(
        type=PLAN_REQUESTED_EVENT,
        actor="kanban-agent",
        correlation_id="ch-plan",
    )
    request["request_event_id"] = event.id
    event.payload = {"request": request, "plan_request": request}
    writer.append(event)

    proposed = _execute(service, writer, "kanban-plan-apply", {
        "plan_response": {
            "request_event_id": event.id,
            "request_id": request["request_id"],
            "revision": request["revision"],
            "question_id": request["question_id"],
            "option_id": option_id,
            "answer": expected_title,
        },
    })
    assert proposed["status"] == "proposal_ready", proposed.get("reason")
    assert TaskStore(state_dir / "kanban.json").list_all() == []
    proposal_event = next(
        item for item in writer.event_log.read_all()
        if item.type == "operator.action.proposed"
    )
    proposal = proposal_event.payload["proposal"]

    approved = _execute(service, writer, "create-task", {
        **proposal["payload"],
        "proposal_event_id": proposal_event.id,
    })

    assert approved["ok"] is True, approved
    task = TaskStore(state_dir / "kanban.json").get(approved["task_id"])
    assert task is not None
    assert task.title == expected_title


def test_channel_task_proposal_reject_has_no_task_side_effect(
    tmp_path: Path,
) -> None:
    state_dir, writer, service, authority = _leader_plan_fixture(tmp_path)
    request = _task_create_plan_request(authority)
    event = ZfEvent(type=PLAN_REQUESTED_EVENT, actor="kanban-agent")
    request["request_event_id"] = event.id
    event.payload = {"request": request, "plan_request": request}
    writer.append(event)
    proposed = _execute(service, writer, "kanban-plan-apply", {
        "plan_response": {
            "request_event_id": event.id,
            "request_id": request["request_id"],
            "revision": request["revision"],
            "question_id": request["question_id"],
            "option_id": "full",
            "answer": "Full delivery (Recommended)",
        },
    })
    assert proposed["status"] == "proposal_ready", proposed.get("reason")
    proposal_event = next(
        item for item in writer.event_log.read_all()
        if item.type == "operator.action.proposed"
    )

    dismissed = _execute(service, writer, "kanban-proposal-dismiss", {
        "proposal_event_id": proposal_event.id,
        "reason": "operator rejected the option",
    })

    assert dismissed["ok"] is True, dismissed
    assert TaskStore(state_dir / "kanban.json").list_all() == []


def _channel_delivery_workflow_plan() -> dict:
    return {
        "header": "Choose delivery workflow",
        "question_id": "channel-prd-delivery",
        "question": "How should the confirmed Channel PRD execute?",
        "options": [
            {
                "id": "delivery",
                "label": "PRD delivery (Recommended)",
                "description": "Run the standard PRD delivery route.",
                "recommended": True,
                "route_id": "delivery:prd:standard",
                "objective": "Deliver the confirmed Channel PRD.",
                "parameters": {"target_root": "."},
            },
            {
                "id": "defer",
                "label": "Do not start yet",
                "description": "Keep the new Task tracked.",
                "mode": "defer",
            },
        ],
        "allow_other": False,
    }


def test_channel_prd_handoff_creates_task_then_task_bound_workflow_plan(
    tmp_path: Path,
) -> None:
    state_dir, writer, service, authority = _leader_plan_fixture(tmp_path)
    service.config = load_config(ROOT / "zf.yaml")
    service.project_root = ROOT
    request = _task_create_plan_request(
        authority,
        workflow_plan=_channel_delivery_workflow_plan(),
    )
    requested_plan = ZfEvent(
        type=PLAN_REQUESTED_EVENT,
        actor="kanban-agent",
        correlation_id="ch-plan",
    )
    request["request_event_id"] = requested_plan.id
    requested_plan.payload = {"request": request, "plan_request": request}
    writer.append(requested_plan)

    proposed = _execute(service, writer, "kanban-plan-apply", {
        "plan_response": {
            "request_event_id": requested_plan.id,
            "request_id": request["request_id"],
            "revision": request["revision"],
            "question_id": request["question_id"],
            "option_id": "full",
            "answer": "Full delivery (Recommended)",
        },
    })
    assert proposed["status"] == "proposal_ready", proposed
    create_proposal = next(
        event.payload["proposal"]
        for event in writer.event_log.read_all()
        if event.type == "operator.action.proposed"
        and event.payload["proposal"]["action"] == "create-task"
    )

    created = _execute(service, writer, "create-task", {
        **create_proposal["payload"],
        "proposal_event_id": create_proposal["proposal_event_id"],
    })
    assert created["ok"] is True, created
    assert created["workflow_plan_event_id"]

    events = writer.event_log.read_all()
    task_created = next(
        event for event in events
        if event.type == "task.created" and event.task_id == created["task_id"]
    )
    workflow_event = next(
        event for event in events
        if event.id == created["workflow_plan_event_id"]
    )
    workflow_request = workflow_event.payload["request"]
    assert workflow_event.type == PLAN_REQUESTED_EVENT
    assert workflow_event.causation_id == task_created.id
    assert workflow_request["subject_type"] == "task_workflow"
    assert workflow_request["task_id"] == created["task_id"]
    assert workflow_request["task_contract_digest"]
    delivery = workflow_request["options"][0]
    assert delivery["submit_action"] == "workflow-start"
    assert delivery["submit_details"]["preflight"] == "ready"
    assert delivery["submit_details"]["input_keys"] == ["target_root"]
    assert not any(
        event.type == "workflow.invoke.requested" for event in events
    )

    workflow_proposed = _execute(service, writer, "kanban-plan-apply", {
        "plan_response": {
            "request_event_id": workflow_event.id,
            "request_id": workflow_request["request_id"],
            "revision": workflow_request["revision"],
            "question_id": workflow_request["question_id"],
            "option_id": "delivery",
            "answer": "PRD delivery (Recommended)",
        },
    })
    assert workflow_proposed["status"] == "proposal_ready", workflow_proposed
    assert workflow_proposed["proposed_action"] == "workflow-start"
    assert not any(
        event.type == "workflow.invoke.requested"
        for event in writer.event_log.read_all()
    )


def test_channel_prd_handoff_fails_closed_when_workflow_plan_is_invalid(
    tmp_path: Path,
) -> None:
    state_dir, writer, service, authority = _leader_plan_fixture(tmp_path)
    service.config = load_config(ROOT / "zf.yaml")
    service.project_root = ROOT
    invalid_plan = _channel_delivery_workflow_plan()
    invalid_plan["options"][0]["route_id"] = "delivery:missing"
    request = _task_create_plan_request(authority, workflow_plan=invalid_plan)
    requested_plan = ZfEvent(type=PLAN_REQUESTED_EVENT, actor="kanban-agent")
    request["request_event_id"] = requested_plan.id
    requested_plan.payload = {"request": request, "plan_request": request}
    writer.append(requested_plan)

    proposed = _execute(service, writer, "kanban-plan-apply", {
        "plan_response": {
            "request_event_id": requested_plan.id,
            "request_id": request["request_id"],
            "revision": request["revision"],
            "question_id": request["question_id"],
            "option_id": "full",
            "answer": "Full delivery (Recommended)",
        },
    })
    assert proposed["status"] == "proposal_ready", proposed
    proposal = next(
        event.payload["proposal"]
        for event in writer.event_log.read_all()
        if event.type == "operator.action.proposed"
    )

    rejected = _execute(service, writer, "create-task", {
        **proposal["payload"],
        "proposal_event_id": proposal["proposal_event_id"],
    })
    assert rejected["ok"] is False
    assert rejected["status"] == "channel_prd_handoff_invalid"
    assert TaskStore(state_dir / "kanban.json").list_all() == []


def test_generic_source_ref_does_not_activate_channel_authority() -> None:
    assert channel_authority_context_from_submit_payload({
        "parameters": {
            "source_ref": "docs/prd/example.md",
            "source_digest": "a" * 64,
        },
    }) == {}


def test_clear_history_preserves_audit_required_product_results(
    tmp_path: Path,
) -> None:
    state_dir, _log, writer, service = _runtime(tmp_path)
    assert _execute(
        service,
        writer,
        "channel-create",
        {"channel_id": "ch-product", "name": "Product"},
    )["ok"]
    posted = _execute(
        service,
        writer,
        "channel-post-message",
        {
            "channel_id": "ch-product",
            "client_message_id": "client-1",
            "text": "Approved requirement.",
        },
    )
    writer.emit(
        "channel.consensus.proposed",
        actor="product-pm",
        correlation_id="ch-product",
        payload={
            "channel_id": "ch-product",
            "thread_id": "main",
            "artifact_ref": "channels/ch-product/prd/r1.json",
            "artifact_digest": "a" * 64,
            "prd_ref": "channels/ch-product/prd/r1.json",
            "prd_digest": "a" * 64,
            "prd_revision": 1,
            "source": "test",
        },
    )
    writer.emit(
        "channel.result.receipt.recorded",
        actor="zf-channel-result-reconciler",
        correlation_id="ch-product",
        payload={
            "schema_version": "channel-result-receipt.v1",
            "channel_id": "ch-product",
            "thread_id": "main",
            "receipt_id": "receipt-1",
            "receipt_kind": "task_created",
            "status": "created",
            "source_event_id": "evt-task",
            "source_event_type": "task.created",
            "receipt_ref": "channels/ch-product/receipts/receipt-1.json",
            "receipt_digest": "b" * 64,
            "artifact_ref": "event:evt-task",
            "artifact_digest": "c" * 64,
            "revision": 1,
            "task_id": "TASK-1",
            "idempotency_key": "receipt-1",
            "source": "runtime",
        },
    )

    cleared = _execute(
        service,
        writer,
        "channel-clear-history",
        {
            "channel_id": "ch-product",
            "thread_id": "main",
            "reason": "retention projection cleanup",
        },
    )

    assert cleared["ok"] is True
    detail = project_channel(state_dir, "ch-product") or {}
    assert detail["messages"] == []
    assert detail["pinned_message_ids"] == []
    assert detail["result_receipts"][0]["receipt_id"] == "receipt-1"
    assert detail["consensus"]["main"]["artifact_ref"].endswith("r1.json")
    assert (
        state_dir / next(
            event.payload["body_ref"]
            for event in EventLog(state_dir / "events.jsonl").read_all()
            if event.id == posted["event_id"]
        )
    ).is_file()
