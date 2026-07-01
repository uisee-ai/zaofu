"""L2 latent — role='' / role 缺失时 anti-auto-route gate 被绕过(doc 64 §5 真实暴露面)

L2 step 2 测试断言 role='assistant' 的 agent-authored mention 不被 auto-route。但写
test 时发现 src/zf/runtime/channel_router.py:_auto_route_allowed 只 block
role ∈ {assistant, agent, system, state_update};当 role='' 或 role 缺失时直接
``return True`` —— 任何 provider 没把 role 设成 'assistant' 都会绕过 anti-loop gate。

这个测试是该 latent kernel gap 的 RED 锚:current main 下 fail,kernel 修好(把
empty/missing role 当 assistant 处理,或把 sender 是 member_id 当 assistant 处理)
之后变 green。
"""

from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.events.model import ZfEvent
from zf.runtime.channel_router import route_channel_message


CHANNEL_ID = "ch-zaofu"


def _seed_channel(log: EventLog) -> None:
    """两个 provider_agent 成员:claude-arch + codex-critic。"""
    log.append(ZfEvent(
        type="channel.created",
        actor="web",
        payload={"channel_id": CHANNEL_ID, "name": "zaofu", "source": "web"},
        correlation_id=CHANNEL_ID,
    ))
    for member_id, persona, provider in [
        ("claude-arch", "Arch", "claude-code"),
        ("codex-critic", "Critic", "codex"),
    ]:
        log.append(ZfEvent(
            type="channel.member.invited",
            actor="web",
            payload={
                "channel_id": CHANNEL_ID,
                "thread_id": "main",
                "member_id": member_id,
                "persona": persona,
                "member_type": "provider_agent",
                "provider": provider,
                "backend": provider,
                "permissions": ["read", "message"],
                "source": "web",
            },
            correlation_id=CHANNEL_ID,
        ))


def test_l2_latent_role_empty_from_agent_member_does_not_autoroute(tmp_path: Path) -> None:
    """codex-critic 发 mention 时如果 role='' (没设),router 当前直接 auto-route。
    断言:即使 role='',只要 sender 是已知 agent member,就应当不 auto-route。
    """
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    _seed_channel(log)

    msg_event = writer.emit(
        "channel.message.posted",
        actor="codex-critic",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "message_id": "msg-critic-roleless",
            "member_id": "codex-critic",
            "role": "",  # latent gap:空 role 直接被当 user 处理
            "text": "@claude-arch 改 X",
            "source": "runtime",
        },
        correlation_id=CHANNEL_ID,
    )

    result = route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=msg_event,
        message_payload=msg_event.payload,
        actor="router",
        source="runtime",
    )

    assert result.reply_requests == [], (
        f"latent bug: kernel auto-routed an agent member with role=''; "
        f"reply_requests={result.reply_requests}"
    )
    assert result.targets == [], (
        f"latent bug: targets contained {result.targets} for role='' from agent member"
    )

    reply_requested_events = [
        e for e in log.read_all() if e.type == "channel.agent.reply.requested"
    ]
    assert reply_requested_events == [], (
        f"events.jsonl reveals latent bug: reply.requested emitted for role='' agent "
        f"author; events: {[e.payload for e in reply_requested_events]}"
    )


def test_l2_latent_role_missing_from_agent_member_does_not_autoroute(tmp_path: Path) -> None:
    """payload 里完全没有 role key —— 也应当不被 auto-route 当成 user。"""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    _seed_channel(log)

    msg_event = writer.emit(
        "channel.message.posted",
        actor="codex-critic",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "message_id": "msg-critic-roleless-2",
            "member_id": "codex-critic",
            # 故意不放 role key
            "text": "@claude-arch 帮我加 X",
            "source": "runtime",
        },
        correlation_id=CHANNEL_ID,
    )

    result = route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=msg_event,
        message_payload=msg_event.payload,
        actor="router",
        source="runtime",
    )

    assert result.reply_requests == [], (
        f"latent bug: missing role from agent member auto-routed; "
        f"reply_requests={result.reply_requests}"
    )

    reply_requested_events = [
        e for e in log.read_all() if e.type == "channel.agent.reply.requested"
    ]
    assert reply_requested_events == [], (
        f"events.jsonl reveals latent bug (missing role): "
        f"{[e.payload for e in reply_requested_events]}"
    )
