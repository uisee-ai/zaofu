from __future__ import annotations

import time
from pathlib import Path

from zf.runtime import channel_adapter
from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.runtime.channel_adapter import dispatch_pending_replies, dispatch_reply_request
from zf.runtime.channel_contracts import (
    normalize_channel_skill_refs,
    normalize_channel_role,
    normalize_permission_profile,
    normalize_visibility_profile,
    permission_profile_write_policy,
    validate_channel_member_contract,
)
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.channel_handoff import request_channel_handoff
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_router import (
    detect_channel_mention_tokens,
    resolve_channel_mentions,
    route_channel_message,
)
from zf.runtime.channel_roles import (
    load_role_definition_excerpt,
    normalize_role_context_ref,
)
from zf.web.headless_agent import HeadlessMessage, HeadlessTurnResult


class _FakeHeadlessBackend:
    backend_id = "codex-headless"

    def __init__(self, *, available: bool = True, reply: str = "codex channel reply") -> None:
        self._available = available
        self.reply = reply
        self.calls: list[dict] = []

    def available(self) -> bool:
        return self._available

    def run_turn(self, **kwargs) -> HeadlessTurnResult:
        self.calls.append(kwargs)
        session_id = "codex-session-1"
        kwargs["on_session_id"](session_id)
        on_message = kwargs.get("on_message")
        if on_message is not None:
            on_message(HeadlessMessage(type="status", content="started", session_id=session_id))
            on_message(HeadlessMessage(type="text", content=self.reply, session_id=session_id))
        return HeadlessTurnResult(
            ok=True,
            status="completed",
            backend=self.backend_id,
            thread_id=str(kwargs.get("thread_id") or ""),
            provider_session_id=session_id,
            reply=self.reply,
            messages=[HeadlessMessage(type="text", content=self.reply)],
            usage={"total_tokens": 12},
        )


class _FailingHeadlessBackend(_FakeHeadlessBackend):
    def __init__(self, *, error: str) -> None:
        super().__init__(available=True)
        self.error = error

    def run_turn(self, **kwargs) -> HeadlessTurnResult:
        self.calls.append(kwargs)
        return HeadlessTurnResult(
            ok=False,
            status="failed",
            backend=self.backend_id,
            thread_id=str(kwargs.get("thread_id") or ""),
            provider_session_id="",
            reply="",
            messages=[],
            usage={},
            error=self.error,
        )


def test_channel_provider_headless_timeout_defaults_to_long_channel_turn(monkeypatch) -> None:
    monkeypatch.delenv("ZF_CHANNEL_PROVIDER_HEADLESS_TIMEOUT_S", raising=False)
    monkeypatch.delenv("ZF_KANBAN_AGENT_HEADLESS_TIMEOUT_S", raising=False)
    assert channel_adapter._channel_provider_headless_timeout_s() == 300.0

    monkeypatch.setenv("ZF_KANBAN_AGENT_HEADLESS_TIMEOUT_S", "180")
    assert channel_adapter._channel_provider_headless_timeout_s() == 180.0

    monkeypatch.setenv("ZF_CHANNEL_PROVIDER_HEADLESS_TIMEOUT_S", "420")
    assert channel_adapter._channel_provider_headless_timeout_s() == 420.0

    monkeypatch.setenv("ZF_CHANNEL_PROVIDER_HEADLESS_TIMEOUT_S", "bad")
    assert channel_adapter._channel_provider_headless_timeout_s() == 300.0


def test_channel_role_context_ref_is_repo_local_and_loads_excerpt() -> None:
    assert normalize_role_context_ref("channel_roles/tech-leader.md") == "channel_roles/tech-leader.md"
    assert normalize_role_context_ref("channel_roles/arch.md") == "channel_roles/arch.md"
    assert normalize_role_context_ref("../AGENTS.md") == ""
    assert normalize_role_context_ref("/tmp/role.md") == ""
    excerpt = load_role_definition_excerpt("channel_roles/tech-leader.md")
    assert excerpt["status"] == "loaded"
    assert "Forbidden / Stop Rule" in excerpt["excerpt"]
    arch_excerpt = load_role_definition_excerpt("channel_roles/arch.md")
    assert arch_excerpt["status"] == "loaded"
    assert "architecture" in arch_excerpt["excerpt"]
    assert "proposal" in arch_excerpt["excerpt"]
    spine_excerpt = load_role_definition_excerpt("channel_roles/spine-reviewer.md")
    assert spine_excerpt["status"] == "loaded"
    assert "Design Spine" in spine_excerpt["excerpt"]
    assert "second control plane" in spine_excerpt["excerpt"]


def test_channel_contract_accepts_arch_role_with_planner_visibility() -> None:
    assert normalize_channel_role("arch") == "arch"
    assert normalize_visibility_profile("", channel_role="arch") == "planner"
    assert normalize_permission_profile("") == "read_only"
    assert validate_channel_member_contract({
        "member_id": "arch-1",
        "member_type": "provider_agent",
        "provider": "codex",
        "channel_role": "arch",
        "role_context_ref": "channel_roles/arch.md",
        "permissions": ["read", "message", "summarize", "propose_workflow"],
    }) == ""


def test_channel_contract_accepts_facilitator_synthesizer_and_project_skill_refs() -> None:
    assert normalize_channel_role("facilitator") == "facilitator"
    assert normalize_channel_role("synthesizer") == "synthesizer"
    assert normalize_visibility_profile("", channel_role="facilitator") == "planner"
    assert normalize_channel_skill_refs("zf-fmea-risk-gate") == ["skills/zf-fmea-risk-gate/SKILL.md"]
    assert normalize_channel_skill_refs(["skills/zf-fmea-risk-gate/SKILL.md"]) == ["skills/zf-fmea-risk-gate/SKILL.md"]
    assert normalize_channel_skill_refs("../.codex/skills/x/SKILL.md") == []
    assert validate_channel_member_contract({
        "member_id": "synth-1",
        "member_type": "provider_agent",
        "provider": "codex",
        "channel_role": "synthesizer",
        "role_context_ref": "channel_roles/synthesizer.md",
        "skill_refs": ["skills/zf-fmea-risk-gate/SKILL.md"],
        "permissions": ["read", "message", "summarize", "propose_workflow"],
    }) == ""
    reason = validate_channel_member_contract({
        "member_id": "bad-1",
        "member_type": "provider_agent",
        "provider": "codex",
        "skill_refs": ["../.codex/skills/x/SKILL.md"],
    })
    assert "skill_refs" in reason


def test_channel_contract_profiles_require_explicit_dangerous_ack() -> None:
    assert permission_profile_write_policy("artifact-writer")["mode"] == "artifact_writer"
    project_policy = permission_profile_write_policy("project-writer")
    assert project_policy["mode"] == "project_writer"
    assert "skills/" in project_policy["allowed_write_paths"]
    assert ".zf/skills/" not in project_policy["allowed_write_paths"]
    assert validate_channel_member_contract({
        "member_id": "researcher-1",
        "member_type": "provider_agent",
        "provider": "codex",
        "permission_profile": "artifact_writer",
    }) == ""
    reason = validate_channel_member_contract({
        "member_id": "danger-1",
        "member_type": "provider_agent",
        "provider": "codex",
        "permission_profile": "dangerous_full",
    })
    assert "dangerous_ack" in reason
    assert validate_channel_member_contract({
        "member_id": "danger-1",
        "member_type": "provider_agent",
        "provider": "codex",
        "permission_profile": "dangerous_full",
        "dangerous_ack": True,
    }) == ""


def test_channel_contract_accepts_spine_reviewer_as_proposal_only_role() -> None:
    assert normalize_channel_role("spine-reviewer") == "spine_reviewer"
    assert normalize_visibility_profile("", channel_role="spine_reviewer") == "reviewer"
    assert validate_channel_member_contract({
        "member_id": "spine-1",
        "member_type": "provider_agent",
        "provider": "codex",
        "channel_role": "spine_reviewer",
        "role_context_ref": "channel_roles/spine-reviewer.md",
        "permissions": ["read", "message", "summarize", "read_reports"],
    }) == ""


def test_channel_spine_reviewer_mention_creates_intent_not_agent_reply(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.emit(
        "channel.member.invited",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "member_id": "spine-1",
            "member_type": "provider_agent",
            "provider": "codex",
            "backend": "codex",
            "channel_role": "spine_reviewer",
            "visibility_profile": "reviewer",
            "role_context_ref": "channel_roles/spine-reviewer.md",
            "permissions": ["read", "message", "summarize", "read_reports"],
            "source": "web",
        },
    )
    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-spine-1",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "@spine-reviewer review the project spine",
        },
    )

    result = route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=message,
        message_payload=message.payload,
        actor="web",
        source="web",
        project_root=tmp_path,
    )
    events = EventLog(state_dir / "events.jsonl").read_all()
    types = [event.type for event in events]
    detail = project_channel(state_dir, "ch-zaofu")

    assert result.targets == ["spine-1"]
    assert result.intent_requests
    assert result.reply_requests == []
    assert "channel.spine_review.requested" in types
    assert "channel.agent.reply.requested" not in types
    assert "workflow.invoke.requested" not in types
    assert detail is not None
    assert detail["reply_requests"] == []
    assert any(
        item["type"] == "channel.spine_review.requested"
        for item in detail["linked_events"]
    )


def test_structured_debate_round_guard_blocks_after_max_rounds(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.emit(
        "channel.created",
        actor="web",
        correlation_id="ch-zaofu",
        payload={"channel_id": "ch-zaofu", "name": "zaofu", "source": "web"},
    )
    writer.emit(
        "channel.member.invited",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "member_id": "arch-1",
            "member_type": "provider_agent",
            "provider": "codex",
            "backend": "codex",
            "permissions": ["read", "message", "summarize"],
            "source": "web",
        },
    )
    writer.emit(
        "channel.discussion.mode.set",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "mode": "debate_judge",
            "max_rounds": 1,
            "speaker_policy": {"enforce_max_rounds": True},
            "source": "web",
        },
    )
    writer.emit(
        "channel.agent.reply.completed",
        actor="router",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "request_id": "reply-existing",
            "message_id": "msg-existing",
            "target_member_id": "arch-1",
            "status": "completed",
            "source": "test",
        },
    )
    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-2",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "@arch-1 continue debate",
        },
    )

    result = route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=message,
        message_payload=message.payload,
        actor="router",
        source="web",
        headless_backends={},
    )

    channel = project_channel(state_dir, "ch-zaofu")
    assert result.reply_requests == []
    assert result.skipped == [{"reason": "debate_round_limit_reached"}]
    assert channel is not None
    assert channel["routes"][-1]["reason"] == "debate_round_limit_reached"


def test_channel_router_matches_member_role_backend_and_chinese_punctuation() -> None:
    channel = {
        "members": [
            {
                "member_id": "codex-1",
                "role": "dev",
                "channel_role": "tech_leader",
                "backend": "codex",
                "status": "invited",
                "permissions": ["read", "message"],
            },
        ],
    }

    assert resolve_channel_mentions(
        channel,
        text="@codex，请 review",
        sender_member_id="operator",
    ) == ["codex-1"]
    assert resolve_channel_mentions(
        channel,
        text="@dev review",
        sender_member_id="operator",
    ) == ["codex-1"]
    assert resolve_channel_mentions(
        channel,
        text="@tech-leader plan",
        sender_member_id="operator",
    ) == ["codex-1"]


def test_channel_router_avoids_email_paths_sender_and_caps_all() -> None:
    channel = {
        "members": [
            {"member_id": "dev-1", "status": "connected", "permissions": ["read", "message"]},
            {"member_id": "dev-2", "status": "connected", "permissions": ["read", "message"]},
            {"member_id": "dev-3", "status": "connected", "permissions": ["read", "message"]},
            {"member_id": "qa-1", "status": "suspended", "permissions": ["read", "message"]},
        ],
    }

    assert resolve_channel_mentions(
        channel,
        text="mail a@dev.com and path /tmp/@dev-1",
        sender_member_id="operator",
    ) == []
    assert resolve_channel_mentions(
        channel,
        text="direct@dev-1 should not look like a mention",
        sender_member_id="operator",
    ) == []
    assert resolve_channel_mentions(
        channel,
        text="中文@dev-1 应该能触发 mention",
        sender_member_id="operator",
    ) == ["dev-1"]
    assert resolve_channel_mentions(
        channel,
        text="@all",
        sender_member_id="dev-1",
        max_targets=1,
    ) == ["dev-2"]
    assert resolve_channel_mentions(
        channel,
        text="@ALL",
        sender_member_id="operator",
        max_targets=6,
    ) == ["dev-1", "dev-2", "dev-3"]
    assert detect_channel_mention_tokens("@ALL 请同步") == ["all"]
    assert detect_channel_mention_tokens("中文@dev-1 请同步") == ["dev1"]
    assert detect_channel_mention_tokens("中文@ALL 请同步") == ["all"]


def test_channel_router_reports_all_no_receivers_for_empty_group(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-empty",
        payload={
            "channel_id": "ch-empty",
            "thread_id": "main",
            "message_id": "msg-all-empty",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "@ALL 请各自回复",
        },
    )

    result = route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=message,
        message_payload=message.payload,
        actor="web",
        source="web",
        project_root=tmp_path,
    )

    assert result.skipped == [{"reason": "all_no_receivers"}]


def test_channel_router_dispatches_fake_provider_to_completed_reply(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.emit(
        "channel.member.invited",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "member_id": "fake-1",
            "persona": "fake-1",
            "member_type": "persona_agent",
            "backend": "fake",
            "channel_role": "researcher",
            "visibility_profile": "minimal",
            "role_context_ref": "channel_roles/researcher.md",
            "permissions": ["read", "message"],
            "source": "web",
        },
    )
    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-1",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "@fake please respond",
            "mentions": ["fake-1"],
        },
    )

    result = route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=message,
        message_payload=message.payload,
        actor="web",
        source="web",
    )
    detail = project_channel(state_dir, "ch-zaofu")

    assert result.targets == ["fake-1"]
    assert detail is not None
    assert detail["reply_requests"][0]["status"] == "completed"
    assert detail["context_packs"][0]["visibility_profile"] == "minimal"
    assert detail["context_packs"][0]["channel_role"] == "researcher"
    assert detail["context_packs"][0]["role_context_ref"] == "channel_roles/researcher.md"
    assert detail["context_packs"][0]["role_definition"]["status"] == "loaded"
    assert detail["reply_requests"][0]["run_id"].startswith("run-")
    assert detail["provider_runs"][0]["run_id"] == detail["reply_requests"][0]["run_id"]
    assert detail["provider_runs"][0]["request_id"] == detail["reply_requests"][0]["request_id"]
    assert detail["members"][0]["presence"] == "ready"
    assert detail["members"][0]["latest_run_status"] == "completed"
    assert any(item["role"] == "assistant" for item in detail["messages"])


def test_channel_router_routes_unmentioned_message_to_default_responder(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.emit(
        "channel.member.invited",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "member_id": "facilitator-1",
            "persona": "facilitator-1",
            "member_type": "persona_agent",
            "backend": "fake",
            "channel_role": "facilitator",
            "visibility_profile": "planner",
            "role_context_ref": "channel_roles/facilitator.md",
            "permissions": ["read", "message", "summarize", "propose_workflow"],
            "source": "web",
        },
    )
    writer.emit(
        "channel.discussion.mode.set",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "mode": "manual_mention",
            "default_responder_id": "facilitator-1",
            "source": "web",
        },
    )
    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-default-1",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "帮我把刚刚的讨论收敛成方案",
        },
    )

    result = route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=message,
        message_payload=message.payload,
        actor="web",
        source="web",
        project_root=tmp_path,
    )
    events = EventLog(state_dir / "events.jsonl").read_all()
    detail = project_channel(state_dir, "ch-zaofu")

    assert result.targets == ["facilitator-1"]
    assert detail is not None
    assert detail["discussion"]["default_responder_id"] == "facilitator-1"
    assert detail["routes"][0]["routing_reason"] == "default_responder"
    assert detail["reply_requests"][0]["routing_reason"] == "default_responder"
    assert detail["context_packs"][0]["routing_reason"] == "default_responder"
    assert detail["members"][0]["is_default_responder"] is True
    assert "channel.route.defaulted" in [event.type for event in events]
    assert "channel.mention.detected" not in [event.type for event in events]


def test_channel_router_explicit_mention_overrides_default_responder(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    for member_id, role in [("facilitator-1", "facilitator"), ("reviewer-1", "dev_reviewer")]:
        writer.emit(
            "channel.member.invited",
            actor="web",
            correlation_id="ch-zaofu",
            payload={
                "channel_id": "ch-zaofu",
                "thread_id": "main",
                "member_id": member_id,
                "persona": member_id,
                "member_type": "persona_agent",
                "backend": "fake",
                "channel_role": role,
                "permissions": ["read", "message"],
                "source": "web",
            },
        )
    writer.emit(
        "channel.discussion.mode.set",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "mode": "manual_mention",
            "default_responder_id": "facilitator-1",
            "source": "web",
        },
    )
    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-explicit-1",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "@reviewer-1 看一下风险",
            "mentions": ["reviewer-1"],
        },
    )

    result = route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=message,
        message_payload=message.payload,
        actor="web",
        source="web",
        project_root=tmp_path,
    )
    event_types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
    detail = project_channel(state_dir, "ch-zaofu")

    assert result.targets == ["reviewer-1"]
    assert "channel.mention.detected" in event_types
    assert "channel.route.defaulted" not in event_types
    assert detail is not None
    assert detail["reply_requests"][0]["target_member_id"] == "reviewer-1"
    assert detail["reply_requests"][0]["routing_reason"] == "mention"


def test_channel_synthesis_request_action_routes_to_synthesizer(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.emit(
        "channel.member.invited",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "member_id": "synth-1",
            "persona": "synth-1",
            "member_type": "persona_agent",
            "backend": "fake",
            "channel_role": "synthesizer",
            "visibility_profile": "planner",
            "role_context_ref": "channel_roles/synthesizer.md",
            "permissions": ["read", "message", "summarize", "propose_workflow"],
            "source": "web",
        },
    )
    requested = writer.emit(
        "runtime.action.requested",
        actor="web",
        correlation_id="ch-zaofu",
        payload={"action": "channel-synthesis-request"},
    )

    result = ControlledActionService(
        state_dir,
        writer,
        project_root=tmp_path,
        actor="web",
        source="web",
        surface="web",
    ).execute(
        action="channel-synthesis-request",
        requested_action="channel.synthesis.request",
        requested=requested,
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "prompt": "总结当前讨论并给出下一步 workflow 建议",
        },
    )
    detail = project_channel(state_dir, "ch-zaofu")
    event_types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]

    assert result["ok"] is True
    assert result["target_member_id"] == "synth-1"
    assert detail is not None
    assert detail["synthesis_requests"][0]["target_member_id"] == "synth-1"
    assert detail["reply_requests"][0]["target_member_id"] == "synth-1"
    assert detail["context_packs"][0]["role_context_ref"] == "channel_roles/synthesizer.md"
    assert "channel.synthesis.requested" in event_types
    for _ in range(20):
        if any(event.type == "channel.agent.reply.completed" for event in EventLog(state_dir / "events.jsonl").read_all()):
            break
        time.sleep(0.05)
    event_types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
    assert "channel.agent.reply.completed" in event_types


def test_channel_router_dispatches_codex_provider_through_headless_backend(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    backend = _FakeHeadlessBackend(reply="codex reviewed the channel message")
    writer.emit(
        "channel.member.invited",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "member_id": "codex-1",
            "member_type": "provider_agent",
            "backend": "codex",
            "channel_role": "tech_leader",
            "visibility_profile": "full",
            "permissions": ["read", "message"],
            "source": "web",
        },
    )
    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-codex-1",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "@codex review the architecture",
            "mentions": ["codex-1"],
        },
    )

    result = route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=message,
        message_payload=message.payload,
        actor="web",
        source="web",
        project_root=tmp_path,
        headless_backends={"codex-headless": backend},
    )
    detail = project_channel(state_dir, "ch-zaofu")

    assert result.targets == ["codex-1"]
    assert detail is not None
    assert detail["reply_requests"][0]["status"] == "completed"
    assert detail["reply_requests"][0]["reason"] == "headless provider completed"
    assert any(item["text"] == "codex reviewed the channel message" for item in detail["messages"])
    assert backend.calls
    assert backend.calls[0]["cwd"] == tmp_path
    assert "channel_role: tech_leader" in backend.calls[0]["prompt"]
    assert "ZaoFu Agent Channel" in backend.calls[0]["system_prompt"]
    event_types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
    assert "agent.session.run.started" in event_types
    assert "agent.session.part.delta" in event_types
    assert "agent.session.run.completed" in event_types
    assert "provider.permission.snapshot.recorded" in event_types
    detail = project_channel(state_dir, "ch-zaofu")
    assert detail is not None
    assert detail["provider_runs"][0]["parts"]


def test_channel_project_writer_legacy_policy_allows_project_skills(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    backend = _FakeHeadlessBackend(reply="skill draft ready")
    writer.emit(
        "channel.member.invited",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "member_id": "codex-1",
            "member_type": "provider_agent",
            "backend": "codex",
            "channel_role": "tech_leader",
            "permission_profile": "project_writer",
            "skill_refs": ["skills/zf-fmea-risk-gate/SKILL.md"],
            "write_policy": {
                "mode": "project_writer",
                "allowed_write_paths": [
                    "docs/design/",
                    "docs/plans/",
                    "docs/impl/",
                    "tasks/",
                    "backlogs/",
                ],
                "requires_gate": True,
            },
            "permissions": ["read", "message"],
            "source": "web",
        },
    )
    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-skill-1",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "@codex generate a project skill draft",
            "mentions": ["codex-1"],
        },
    )

    result = route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=message,
        message_payload=message.payload,
        actor="web",
        source="web",
        project_root=tmp_path,
        headless_backends={"codex-headless": backend},
    )
    detail = project_channel(state_dir, "ch-zaofu")
    snapshots = [
        event.payload["snapshot"]
        for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == "provider.permission.snapshot.recorded"
    ]

    assert result.targets == ["codex-1"]
    assert detail is not None
    member = detail["members"][0]
    assert "skills/" in member["write_policy"]["allowed_write_paths"]
    assert member["skill_refs"] == ["skills/zf-fmea-risk-gate/SKILL.md"]
    assert detail["context_packs"][0]["skill_refs"] == ["skills/zf-fmea-risk-gate/SKILL.md"]
    assert backend.calls
    assert "skills/zf-fmea-risk-gate/SKILL.md" in backend.calls[0]["prompt"]
    assert "skills/zf-fmea-risk-gate/SKILL.md" in backend.calls[0]["system_prompt"]
    assert "skills/" in backend.calls[0]["prompt"]
    assert "skills/" in backend.calls[0]["system_prompt"]
    assert snapshots
    assert "skills/" in snapshots[-1]["write_policy"]["allowed_write_paths"]


def test_channel_router_reports_unavailable_codex_headless_backend(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.emit(
        "channel.member.invited",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "member_id": "codex-1",
            "member_type": "provider_agent",
            "backend": "codex",
            "permissions": ["read", "message"],
            "source": "web",
        },
    )
    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-codex-2",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "@codex review",
            "mentions": ["codex-1"],
        },
    )

    result = route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=message,
        message_payload=message.payload,
        actor="web",
        source="web",
        project_root=tmp_path,
        headless_backends={"codex-headless": _FakeHeadlessBackend(available=False)},
    )
    detail = project_channel(state_dir, "ch-zaofu")

    assert result.targets == ["codex-1"]
    assert detail is not None
    assert detail["reply_requests"][0]["status"] == "failed"
    assert detail["reply_requests"][0]["reason"] == "codex-headless command is unavailable"


def test_channel_router_reports_codex_sandbox_failure(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.emit(
        "channel.member.invited",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "member_id": "codex-1",
            "member_type": "provider_agent",
            "backend": "codex",
            "permissions": ["read", "message"],
            "source": "web",
        },
    )
    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-codex-sandbox",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "@codex review",
            "mentions": ["codex-1"],
        },
    )

    result = route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=message,
        message_payload=message.payload,
        actor="web",
        source="web",
        project_root=tmp_path,
        headless_backends={
            "codex-headless": _FailingHeadlessBackend(
                error="bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted"
            )
        },
    )
    detail = project_channel(state_dir, "ch-zaofu")

    assert result.targets == ["codex-1"]
    assert detail is not None
    assert detail["reply_requests"][0]["status"] == "failed"
    assert "bwrap: loopback" in detail["reply_requests"][0]["reason"]


def test_channel_adapter_skips_dispatch_when_target_already_running(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.emit(
        "channel.member.invited",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "member_id": "fake-1",
            "persona": "fake-1",
            "member_type": "persona",
            "backend": "fake",
            "permissions": ["read", "message"],
            "source": "web",
        },
    )
    writer.emit(
        "channel.agent.reply.requested",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "request_id": "reply-running",
            "message_id": "msg-running",
            "target_member_id": "fake-1",
            "member_id": "operator",
            "status": "pending",
            "source": "web",
        },
    )
    writer.emit(
        "channel.agent.reply.started",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "request_id": "reply-running",
            "message_id": "msg-running",
            "target_member_id": "fake-1",
            "source": "web",
        },
    )
    writer.emit(
        "channel.agent.reply.requested",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "request_id": "reply-new",
            "message_id": "msg-new",
            "target_member_id": "fake-1",
            "member_id": "operator",
            "status": "pending",
            "source": "web",
        },
    )

    result = dispatch_reply_request(
        state_dir=state_dir,
        writer=writer,
        channel_id="ch-zaofu",
        request_id="reply-new",
        actor="web",
        source="web",
    )
    detail = project_channel(state_dir, "ch-zaofu")

    assert result.skipped == [{
        "request_id": "reply-new",
        "reason": "target_busy",
        "active_request_id": "reply-running",
    }]
    assert detail is not None
    statuses = {item["request_id"]: item["status"] for item in detail["reply_requests"]}
    assert statuses == {"reply-running": "running", "reply-new": "pending"}
    assert detail["members"][0]["presence"] == "running"


def test_channel_router_rejects_context_pack_over_source_budget(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.emit(
        "channel.member.invited",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "member_id": "fake-1",
            "persona": "fake-1",
            "member_type": "persona",
            "backend": "fake",
            "permissions": ["read", "message"],
            "source": "web",
        },
    )
    writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-big",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "x" * 61000,
            "mentions": [],
        },
    )
    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-2",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "@fake handle this",
            "mentions": ["fake-1"],
        },
    )

    result = route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=message,
        message_payload=message.payload,
        actor="web",
        source="web",
    )
    detail = project_channel(state_dir, "ch-zaofu")

    assert result.skipped[0]["reason"] == "context_pack_rejected"
    assert detail is not None
    assert detail["context_packs"][0]["status"] == "rejected"
    assert detail["reply_requests"] == []


def test_channel_handoff_guard_accepts_and_rejects(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.emit(
        "channel.member.invited",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "member_id": "qa-1",
            "persona": "qa-1",
            "member_type": "persona",
            "backend": "fake",
            "permissions": ["read", "message"],
            "source": "web",
        },
    )
    rejected = request_channel_handoff(
        state_dir=state_dir,
        writer=writer,
        channel_id="ch-zaofu",
        thread_id="main",
        message_id="msg-1",
        member_id="dev-1",
        target_member_id="qa-1",
        reason="verify",
        actor="web",
        source="web",
        depth=4,
    )
    accepted = request_channel_handoff(
        state_dir=state_dir,
        writer=writer,
        channel_id="ch-zaofu",
        thread_id="main",
        message_id="msg-2",
        member_id="dev-1",
        target_member_id="qa-1",
        reason="verify",
        actor="web",
        source="web",
        depth=1,
    )
    detail = project_channel(state_dir, "ch-zaofu")

    assert rejected.skipped[0]["reason"] == "handoff depth exceeded"
    assert accepted.targets == ["qa-1"]
    assert detail is not None
    assert [item["status"] for item in detail["handoffs"]] == ["requested", "rejected", "requested", "accepted"]
    assert detail["reply_requests"][0]["status"] == "completed"


def test_channel_adapter_drains_latest_queued_reply(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.emit(
        "channel.member.invited",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "member_id": "fake-1",
            "persona": "fake-1",
            "member_type": "persona",
            "backend": "fake",
            "permissions": ["read", "message"],
            "source": "web",
        },
    )
    writer.emit(
        "channel.agent.reply.requested",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "request_id": "reply-old",
            "message_id": "msg-old",
            "target_member_id": "fake-1",
            "member_id": "operator",
            "status": "queued",
            "queue_state": "latest_only",
            "source": "web",
        },
    )
    writer.emit(
        "channel.agent.reply.requested",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "request_id": "reply-new",
            "message_id": "msg-new",
            "target_member_id": "fake-1",
            "member_id": "operator",
            "status": "queued",
            "queue_state": "latest_only",
            "source": "web",
        },
    )

    result = dispatch_pending_replies(
        state_dir=state_dir,
        writer=writer,
        channel_id="ch-zaofu",
        actor="web",
        source="web",
        allow_queued=True,
    )

    detail = project_channel(state_dir, "ch-zaofu")

    assert result.failed == ["reply-old"]
    assert result.completed == ["reply-new"]
    assert detail is not None
    statuses = {item["request_id"]: item["status"] for item in detail["reply_requests"]}
    reasons = {item["request_id"]: item["reason"] for item in detail["reply_requests"]}
    assert statuses == {"reply-old": "failed", "reply-new": "completed"}
    assert reasons["reply-old"] == "superseded by latest queued mention"
