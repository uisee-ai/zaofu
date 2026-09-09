from __future__ import annotations

import json
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
from zf.runtime.channel_sidecar import channel_message_event_payload
from zf.runtime.channel_roles import (
    load_role_definition_excerpt,
    normalize_role_context_ref,
)
from zf.runtime.channel_reply_prompt import fake_channel_reply_text
from zf.web.headless_agent import HeadlessMessage, HeadlessThreadStore, HeadlessTurnResult


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
    def __init__(self, *, error: str, status: str = "failed") -> None:
        super().__init__(available=True)
        self.error = error
        self.status = status

    def run_turn(self, **kwargs) -> HeadlessTurnResult:
        self.calls.append(kwargs)
        return HeadlessTurnResult(
            ok=False,
            status=self.status,
            backend=self.backend_id,
            thread_id=str(kwargs.get("thread_id") or ""),
            provider_session_id="",
            reply="",
            messages=[],
            usage={},
            error=self.error,
        )


class _ScriptedHeadlessBackend(_FakeHeadlessBackend):
    def __init__(self, turns: list[dict]) -> None:
        super().__init__(available=True)
        self.turns = list(turns)

    def run_turn(self, **kwargs) -> HeadlessTurnResult:
        self.calls.append(kwargs)
        turn = self.turns.pop(0)
        session_id = "codex-session-1"
        kwargs["on_session_id"](session_id)
        reply = str(turn.get("reply") or "")
        on_message = kwargs.get("on_message")
        if on_message is not None and reply:
            on_message(
                HeadlessMessage(
                    type="text",
                    content=reply,
                    session_id=session_id,
                )
            )
        incomplete = turn.get("incomplete") is True
        return HeadlessTurnResult(
            ok=not incomplete,
            status="incomplete" if incomplete else "completed",
            backend=self.backend_id,
            thread_id=str(kwargs.get("thread_id") or ""),
            provider_session_id=session_id,
            reply=reply,
            messages=[HeadlessMessage(type="text", content=reply)],
            usage={"total_tokens": int(turn.get("tokens") or 1)},
            resumed=bool(kwargs.get("provider_session_id")),
            incomplete=incomplete,
            completion_reason=(
                str(turn.get("completion_reason") or "max_output_tokens")
                if incomplete
                else "completed"
            ),
            error="provider response incomplete" if incomplete else "",
        )


def test_channel_provider_headless_timeout_defaults_to_long_channel_turn(monkeypatch) -> None:
    monkeypatch.delenv("ZF_CHANNEL_PROVIDER_HEADLESS_TIMEOUT_S", raising=False)
    monkeypatch.delenv("ZF_KANBAN_AGENT_HEADLESS_TIMEOUT_S", raising=False)
    assert channel_adapter._channel_provider_headless_timeout_s() == 1800.0

    monkeypatch.setenv("ZF_KANBAN_AGENT_HEADLESS_TIMEOUT_S", "180")
    assert channel_adapter._channel_provider_headless_timeout_s() == 180.0

    monkeypatch.setenv("ZF_CHANNEL_PROVIDER_HEADLESS_TIMEOUT_S", "420")
    assert channel_adapter._channel_provider_headless_timeout_s() == 420.0

    monkeypatch.setenv("ZF_CHANNEL_PROVIDER_HEADLESS_TIMEOUT_S", "bad")
    assert channel_adapter._channel_provider_headless_timeout_s() == 1800.0


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
    assert normalize_channel_skill_refs("skills/../SKILL.md") == []
    assert normalize_channel_skill_refs("skills/.hidden/SKILL.md") == []
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
    assert normalize_permission_profile("workspace-writer") == "workspace_writer"
    assert permission_profile_write_policy("operator")["allowed_write_paths"] == []
    assert permission_profile_write_policy("workspace_writer")["allowed_write_paths"] == ["*"]
    isolated_policy = permission_profile_write_policy("isolated_writer")
    assert isolated_policy["requires_isolated_worktree"] is True
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

    # r3 live regression (2026-07-03): the budget is agent-loop circuit
    # breaking — a HUMAN direct mention must never be blocked by it. The
    # old behavior bricked the channel for the operator permanently.
    channel = project_channel(state_dir, "ch-zaofu")
    assert channel is not None
    assert result.reply_requests, "human direct mention bypasses the debate budget"
    assert not any(
        route.get("reason") == "debate_round_limit_reached"
        for route in channel["routes"]
    )


def test_debate_round_guard_is_session_scoped_and_agent_only() -> None:
    from zf.runtime.channel_router import _debate_round_guard_reason

    def _channel(session_state: str, reply_created: str, *, explicit: bool = True) -> dict:
        return {
            "members": [{"member_id": f"m{i}", "member_type": "provider_agent"} for i in range(3)],
            "discussion": {
                "mode": "fanout_then_synthesis",
                "max_rounds": 1,
                "max_rounds_explicit": explicit,
            },
            "discussions": {"main": {
                "thread_id": "main",
                "state": session_state,
                "started_at": "2026-07-03T10:00:00+00:00",
            }},
            "reply_requests": [{
                "thread_id": "main",
                "status": "completed",
                "created_at": reply_created,
            }],
        }

    in_session = _channel("phase2_relay", "2026-07-03T10:05:00+00:00")
    # agent-authored + active session + budget exhausted → blocked
    assert _debate_round_guard_reason(in_session, thread_id="main", human_author=False) \
        == "debate_round_limit_reached"
    # the same state never blocks a human
    assert _debate_round_guard_reason(in_session, thread_id="main", human_author=True) == ""
    # replies from BEFORE the session don't count against its budget
    pre_session = _channel("phase2_relay", "2026-07-03T09:00:00+00:00")
    assert _debate_round_guard_reason(pre_session, thread_id="main", human_author=False) == ""
    # no active session → nothing to meter
    idle = _channel("idle", "2026-07-03T10:05:00+00:00")
    assert _debate_round_guard_reason(idle, thread_id="main", human_author=False) == ""
    # operator ruling 2026-07-03: only an EXPLICIT max_rounds arms the guard;
    # the projected roster-scaled default never blocks routing (relay hop
    # depth still bounds agent chains).
    default_budget = _channel("phase2_relay", "2026-07-03T10:05:00+00:00", explicit=False)
    assert _debate_round_guard_reason(default_budget, thread_id="main", human_author=False) == ""


def _invite_member(writer: EventWriter, channel_id: str, member_id: str) -> None:
    writer.emit(
        "channel.member.invited",
        actor="web",
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "member_id": member_id,
            "member_type": "provider_agent",
            "provider": "codex",
            "backend": "codex",
            "permissions": ["read", "message", "summarize"],
            "source": "web",
        },
    )


def test_default_max_rounds_scales_with_roster_size(tmp_path: Path) -> None:
    # 2026-07-03 racing-codex e2e rounds 1-3 (100% reproduction): a fixed
    # default of 6 exhausts before a 3-member discussion's blind fanout (3)
    # + phase2-relay-kickoff (3) even completes, permanently blocking the
    # synthesis-kickoff dispatch. The default must scale with roster size.
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.emit(
        "channel.created", actor="web", correlation_id="ch-zaofu",
        payload={"channel_id": "ch-zaofu", "name": "zaofu", "source": "web"},
    )
    for member_id in ("pm-1", "arch-1", "critic-1"):
        _invite_member(writer, "ch-zaofu", member_id)
    writer.emit(
        "channel.discussion.mode.set",
        actor="web",
        correlation_id="ch-zaofu",
        payload={"channel_id": "ch-zaofu", "mode": "fanout_then_synthesis", "source": "web"},
    )

    channel = project_channel(state_dir, "ch-zaofu")
    assert channel is not None
    assert channel["discussion"]["max_rounds"] == 12  # max(6, 3*4)


def test_explicit_max_rounds_not_overridden_by_roster_default(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.emit(
        "channel.created", actor="web", correlation_id="ch-zaofu",
        payload={"channel_id": "ch-zaofu", "name": "zaofu", "source": "web"},
    )
    for member_id in ("pm-1", "arch-1", "critic-1"):
        _invite_member(writer, "ch-zaofu", member_id)
    writer.emit(
        "channel.discussion.mode.set",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu", "mode": "fanout_then_synthesis",
            "max_rounds": 3, "source": "web",
        },
    )

    channel = project_channel(state_dir, "ch-zaofu")
    assert channel is not None
    assert channel["discussion"]["max_rounds"] == 3


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


def test_channel_router_hydrates_sidecar_message_before_routing(tmp_path: Path) -> None:
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
            "permissions": ["read", "message"],
            "source": "web",
        },
    )
    full_text = "x" * 2600 + " @fake please respond"
    payload = channel_message_event_payload(
        state_dir,
        {
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-sidecar",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": full_text,
            "mentions": [],
        },
        created_by="test",
    )
    assert "@fake" not in payload["text"]
    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-zaofu",
        payload=payload,
    )

    result = route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=message,
        message_payload=message.payload,
        actor="web",
        source="web",
    )
    events = EventLog(state_dir / "events.jsonl").read_all()
    context_events = [event for event in events if event.type == "channel.context_pack.built"]
    detail = project_channel(state_dir, "ch-zaofu")

    assert result.targets == ["fake-1"]
    assert context_events[-1].payload["context_pack_ref"].startswith("channels/ch-zaofu/context-packs/")
    assert context_events[-1].payload["refs"]["context_pack"]["kind"] == "channel_context_pack"
    assert "message_refs" not in context_events[-1].payload
    assert detail is not None
    assert detail["context_packs"][0]["message_refs"][0]["message_id"] == "msg-sidecar"


def test_channel_router_blocks_missing_sidecar_message_body(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "schema_version": "channel.message.posted.v2",
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-missing-body",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "@fake preview only",
            "body_ref": "channels/ch-zaofu/messages/missing.json",
            "refs": {
                "message_body": {
                    "kind": "channel_message_body",
                    "ref": "channels/ch-zaofu/messages/missing.json",
                    "schema_version": "channel.message.body.v1",
                    "content_type": "application/json",
                    "required": True,
                }
            },
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
    blocked = [event for event in EventLog(state_dir / "events.jsonl").read_all()
               if event.type == "channel.route.blocked"]

    assert result.skipped == [{"reason": "message_body_missing"}]
    assert blocked[-1].payload["reason"] == "message_body_missing"


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


def test_channel_synthesis_request_action_reuses_an_active_turn(
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
            "member_id": "synth-1",
            "persona": "synth-1",
            "member_type": "persona_agent",
            "backend": "fake",
            "channel_role": "synthesizer",
            "visibility_profile": "planner",
            "role_context_ref": "channel_roles/synthesizer.md",
            "permissions": ["read", "message", "summarize"],
            "source": "web",
        },
    )
    service = ControlledActionService(
        state_dir,
        writer,
        project_root=tmp_path,
        actor="web",
        source="web",
        surface="web",
    )
    first_requested = writer.emit(
        "runtime.action.requested",
        actor="web",
        correlation_id="ch-zaofu",
        payload={"action": "channel-synthesis-request"},
    )
    first = service.execute(
        action="channel-synthesis-request",
        requested_action="channel.synthesis.request",
        requested=first_requested,
        payload={"channel_id": "ch-zaofu", "thread_id": "main"},
    )
    second_requested = writer.emit(
        "runtime.action.requested",
        actor="web",
        correlation_id="ch-zaofu",
        payload={"action": "channel-synthesis-request"},
    )
    second = service.execute(
        action="channel-synthesis-request",
        requested_action="channel.synthesis.request",
        requested=second_requested,
        payload={"channel_id": "ch-zaofu", "thread_id": "main"},
    )

    syntheses = [
        event
        for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == "channel.synthesis.requested"
    ]
    assert first["status"] == "requested"
    assert second["_status_code"] == 200
    assert second["ok"] is True
    assert second["status"] == "already_requested"
    assert second["channel_id"] == "ch-zaofu"
    assert second["thread_id"] == "main"
    assert second["request_id"] == first["request_id"]
    assert second["target_member_id"] == "synth-1"
    assert len(syntheses) == 1


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


def test_channel_synthesis_phase_does_not_resume_member_discussion_session(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    backend = _FakeHeadlessBackend()
    writer.emit(
        "channel.member.invited",
        actor="web",
        correlation_id="ch-phase-session",
        payload={
            "channel_id": "ch-phase-session",
            "member_id": "synthesizer",
            "member_type": "provider_agent",
            "backend": "codex",
            "channel_role": "synthesizer",
            "permissions": ["read", "message", "summarize"],
            "source": "web",
        },
    )
    store = HeadlessThreadStore(state_dir=state_dir, project_root=tmp_path)
    discussion_thread = store.load(
        scope="project",
        task_id="",
        thread_key="channel:ch-phase-session:main:synthesizer",
    )
    store.pin_provider_session(
        discussion_thread,
        backend="codex-headless",
        provider_session_id="stale-discussion-session",
        workdir=str(tmp_path),
        status="completed",
    )
    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-phase-session",
        payload={
            "channel_id": "ch-phase-session",
            "thread_id": "main",
            "message_id": "msg-synthesis-phase",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "@synthesizer synthesize the canonical PRD",
            "mentions": ["synthesizer"],
            "refs": {"synthesis_request_id": "synth-phase-1"},
        },
    )
    backend.reply = fake_channel_reply_text(
        {"member_id": "synthesizer"},
        message.payload,
    )

    route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=message,
        message_payload=message.payload,
        actor="web",
        source="web",
        project_root=tmp_path,
        headless_backends={"codex-headless": backend},
    )

    assert len(backend.calls) == 1
    assert backend.calls[0]["provider_session_id"] == ""
    assert backend.calls[0]["run_thread_id"].endswith(
        ":phase:synthesis_request_id:synth-phase-1"
    )


def test_channel_incomplete_provider_turn_auto_continues_same_session(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    backend = _ScriptedHeadlessBackend([
        {
            "reply": "First half ",
            "incomplete": True,
            "completion_reason": "max_output_tokens",
            "tokens": 5,
        },
        {"reply": "and second half.", "tokens": 7},
    ])
    writer.emit(
        "channel.member.invited",
        actor="web",
        correlation_id="ch-continue",
        payload={
            "channel_id": "ch-continue",
            "member_id": "codex-1",
            "member_type": "provider_agent",
            "backend": "codex",
            "channel_role": "reviewer",
            "permissions": ["read", "message"],
            "source": "web",
        },
    )
    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-continue",
        payload={
            "channel_id": "ch-continue",
            "thread_id": "main",
            "message_id": "msg-continue",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "@codex-1 explain the result",
            "mentions": ["codex-1"],
        },
    )

    route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=message,
        message_payload=message.payload,
        actor="web",
        source="web",
        project_root=tmp_path,
        headless_backends={"codex-headless": backend},
    )

    detail = project_channel(state_dir, "ch-continue")
    assert detail is not None
    assert len(backend.calls) == 2
    assert backend.calls[0]["provider_session_id"] == ""
    assert backend.calls[1]["provider_session_id"] == "codex-session-1"
    assert "Continue exactly from the prior response" in backend.calls[1]["prompt"]
    assert any(
        item["text"] == "First half and second half."
        for item in detail["messages"]
    )
    reply = detail["reply_requests"][0]
    assert reply["status"] == "completed"
    assert reply["continuation_count"] == 1
    assert detail["usage_summary"]["total_tokens"] == 12


def test_channel_headless_stream_never_publishes_machine_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from zf.runtime import agent_session_stream
    from zf.runtime.channel_reply_stream import CHANNEL_CONTRACT_MARKER

    class RecordingBus:
        def __init__(self) -> None:
            self.rows: list[dict] = []

        def publish(self, event_type, payload, **_kwargs) -> None:
            self.rows.append({"type": event_type, "payload": payload})

        def discard(self, _key) -> None:
            return None

    bus = RecordingBus()
    monkeypatch.setattr(
        agent_session_stream,
        "live_delta_bus_for_writer",
        lambda _writer: bus,
    )
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    reply = (
        "Keep one canonical state owner.\n\n"
        f"{CHANNEL_CONTRACT_MARKER}\n"
        '{"channel_contribution":{"summary":"hidden","risks":[]}}'
    )
    backend = _FakeHeadlessBackend(reply=reply)
    writer.emit(
        "channel.member.invited",
        actor="web",
        correlation_id="ch-stream-contract",
        payload={
            "channel_id": "ch-stream-contract",
            "thread_id": "main",
            "member_id": "codex-1",
            "member_type": "provider_agent",
            "backend": "codex",
            "channel_role": "tech_leader",
            "permissions": ["read", "message"],
            "source": "web",
        },
    )
    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-stream-contract",
        payload={
            "channel_id": "ch-stream-contract",
            "thread_id": "main",
            "message_id": "msg-stream-contract",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "@codex review",
            "mentions": ["codex-1"],
        },
    )

    route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=message,
        message_payload=message.payload,
        actor="web",
        source="web",
        project_root=tmp_path,
        headless_backends={"codex-headless": backend},
    )

    visible = "".join(
        str(row["payload"].get("delta") or "")
        for row in bus.rows
        if row["payload"].get("kind") == "text"
    )
    assert visible == "Keep one canonical state owner.\n\n"
    assert CHANNEL_CONTRACT_MARKER not in visible
    assert "channel_contribution" not in visible


def test_truncated_synthesis_is_not_marked_completed_or_sent_to_contract_repair(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZF_CHANNEL_PROVIDER_MAX_CONTINUATIONS", "0")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    backend = _FakeHeadlessBackend(
        reply='{"channel_synthesis":{"summary":"truncated"',
    )
    writer.emit(
        "channel.member.invited",
        actor="web",
        correlation_id="ch-repair",
        payload={
            "channel_id": "ch-repair",
            "member_id": "synthesizer",
            "member_type": "provider_agent",
            "backend": "codex",
            "channel_role": "synthesizer",
            "permissions": ["read", "message", "summarize"],
            "source": "web",
        },
    )
    original = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-repair",
        payload={
            "channel_id": "ch-repair",
            "thread_id": "main",
            "message_id": "msg-synthesis-original",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "@synthesizer produce the synthesis",
            "mentions": ["synthesizer"],
            "refs": {"synthesis_request_id": "synth-session"},
        },
    )
    route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=original,
        message_payload=original.payload,
        actor="web",
        source="web",
        project_root=tmp_path,
        headless_backends={"codex-headless": backend},
    )
    events = writer.event_log.read_all()
    assert len(backend.calls) == 1
    assert backend.calls[0]["provider_session_id"] == ""
    assert not any(
        event.type == "channel.agent.reply.completed" for event in events
    )
    assert not any(
        event.type == "channel.synthesis.repair.requested" for event in events
    )
    assert any(
        event.type == "channel.agent.reply.failed"
        and event.payload.get("failure_status") == "incomplete"
        for event in events
    )


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
                error="bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted",
                status="sandbox_unsupported",
            )
        },
    )
    detail = project_channel(state_dir, "ch-zaofu")

    assert result.targets == ["codex-1"]
    assert detail is not None
    assert detail["reply_requests"][0]["status"] == "failed"
    assert "bwrap: loopback" in detail["reply_requests"][0]["reason"]
    failed = next(
        event
        for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == "channel.agent.reply.failed"
    )
    assert failed.payload["failure_status"] == "sandbox_unsupported"
    assert failed.payload["failure_class"] == "provider_sandbox_unsupported"
    assert failed.payload["retryable"] is False


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


def test_channel_router_compacts_context_pack_over_source_budget(
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

    assert result.skipped == []
    assert result.targets == ["fake-1"]
    assert detail is not None
    assert detail["context_packs"][0]["status"] == "built"
    assert detail["context_packs"][0]["limits"]["compaction_required"] == 1
    assert len(detail["context_packs"][0]["message_refs"]) <= 6
    assert len(detail["reply_requests"]) == 1


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


def test_all_targets_are_queued_instead_of_truncated_by_parallel_limit(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.emit(
        "channel.created",
        actor="web",
        correlation_id="ch-all",
        payload={"channel_id": "ch-all", "name": "all targets"},
    )
    for index in range(5):
        writer.emit(
            "channel.member.invited",
            actor="web",
            correlation_id="ch-all",
            payload={
                "channel_id": "ch-all",
                "member_id": f"member-{index}",
                "member_type": "persona_agent",
                "provider": "fake",
                "backend": "fake",
                "permissions": ["read", "message"],
            },
        )
    writer.emit(
        "channel.discussion.mode.set",
        actor="web",
        correlation_id="ch-all",
        payload={
            "channel_id": "ch-all",
            "mode": "conversation",
            "max_parallel_replies": 2,
        },
    )
    message = writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-all",
        payload={
            "channel_id": "ch-all",
            "thread_id": "main",
            "message_id": "msg-count",
            "member_id": "operator",
            "role": "user",
            "text": "@all report your number",
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
        dispatch_inline=False,
    )
    detail = project_channel(state_dir, "ch-all")

    assert result.targets == [f"member-{index}" for index in range(5)]
    assert len(result.reply_requests) == 5
    assert result.queued == ["member-2", "member-3", "member-4"]
    assert detail is not None
    statuses = [item["status"] for item in detail["reply_requests"]]
    assert statuses.count("pending") == 2
    assert statuses.count("queued") == 3
