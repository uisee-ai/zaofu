"""L2 — 多 Agent 讨论(arch + critic 反复迭代)

覆盖 docs/test_case/01-zaofu-channel-collab-test-plan.md §3 L2 五步,核心断言来自
doc 64 §5: agent-authored mention 不应触发自动 reply 路由(L2 step 2)。

每个测试都按 step → verify 单一断言原则:assertion 失败 = kernel 实际行为偏离测试
计划,而不是 silently skip。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.events.model import ZfEvent
from zf.runtime.channel_projection import project_channel
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
    log.append(ZfEvent(
        type="channel.member.invited",
        actor="web",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "member_id": "claude-arch",
            "persona": "Arch",
            "member_type": "provider_agent",
            "provider": "claude-code",
            "backend": "claude-code",
            "permissions": ["read", "message"],
            "source": "web",
        },
        correlation_id=CHANNEL_ID,
    ))
    log.append(ZfEvent(
        type="channel.member.invited",
        actor="web",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "member_id": "codex-critic",
            "persona": "Critic",
            "member_type": "provider_agent",
            "provider": "codex",
            "backend": "codex",
            "permissions": ["read", "message"],
            "source": "web",
        },
        correlation_id=CHANNEL_ID,
    ))


def _ts(seconds: int) -> str:
    return datetime(2026, 5, 29, 10, 0, seconds, tzinfo=timezone.utc).isoformat()


# ---------------------------------------------------------------- L2 step 1


def test_l2_step1_arch_critic_iteration_records_iterations_in_synthesis(tmp_path: Path) -> None:
    """arch v1 → critic R1 → arch v2 → critic verifies → synthesis.iterations=2。

    断言落在 events.jsonl truth(payload.iterations==2)而不是 projection 派生
    字段,因为 doc 64 §5 truth 来源是 events.jsonl,projection 可以晚加但
    payload 必须先承载。
    """
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    _seed_channel(log)

    # arch v1
    writer.emit(
        "channel.message.posted",
        actor="claude-arch",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "message_id": "msg-arch-v1",
            "member_id": "claude-arch",
            "role": "assistant",
            "text": "方案 v1",
            "source": "runtime",
        },
        correlation_id=CHANNEL_ID,
    )
    # critic R1
    writer.emit(
        "channel.message.posted",
        actor="codex-critic",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "message_id": "msg-critic-r1",
            "member_id": "codex-critic",
            "role": "assistant",
            "text": "风险 R1",
            "source": "runtime",
        },
        correlation_id=CHANNEL_ID,
    )
    # arch v2
    writer.emit(
        "channel.message.posted",
        actor="claude-arch",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "message_id": "msg-arch-v2",
            "member_id": "claude-arch",
            "role": "assistant",
            "text": "方案 v2 修复 R1",
            "source": "runtime",
        },
        correlation_id=CHANNEL_ID,
    )
    # critic verifies
    writer.emit(
        "channel.message.posted",
        actor="codex-critic",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "message_id": "msg-critic-ok",
            "member_id": "codex-critic",
            "role": "assistant",
            "text": "v2 ok",
            "source": "runtime",
        },
        correlation_id=CHANNEL_ID,
    )
    # synthesis with iterations=2
    writer.emit(
        "channel.synthesis.proposed",
        actor="claude-arch",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "decision": "ready_for_workflow",
            "summary": "arch+critic converged after 2 rounds",
            "iterations": 2,
            "source": "runtime",
        },
        correlation_id=CHANNEL_ID,
    )

    synthesis_events = [e for e in log.read_all() if e.type == "channel.synthesis.proposed"]
    assert len(synthesis_events) == 1
    assert synthesis_events[0].payload["iterations"] == 2

    # projection at minimum keeps the synthesis row (decision/summary preserved)
    detail = project_channel(state_dir, CHANNEL_ID)
    assert detail is not None
    assert detail["syntheses"][0]["decision"] == "ready_for_workflow"
    assert detail["message_count"] == 4


# ---------------------------------------------------------------- L2 step 2


def test_l2_step2_agent_authored_mention_does_not_autoroute(tmp_path: Path) -> None:
    """KEY ASSERTION (doc 64 §5): codex-critic 发 "@claude-arch 改 X",router 必须
    跳过 auto-route,**不**发 channel.agent.reply.requested。

    这是反作弊核对清单 (L2 step 2) 的独立断言:不依赖 "恰好没发生",而是直接
    检查 route_channel_message 的返回 + 事件日志。
    """
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    _seed_channel(log)

    # codex-critic 发的 mention,带 role=assistant(agent authored)
    msg_event = writer.emit(
        "channel.message.posted",
        actor="codex-critic",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "message_id": "msg-critic-mention",
            "member_id": "codex-critic",
            "role": "assistant",
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

    # router 必须明确拒绝 auto-route
    assert result.reply_requests == []
    assert result.targets == []
    assert any(skip.get("reason") == "auto_route_not_allowed" for skip in result.skipped), (
        f"agent-authored mention 不应 auto-route 到 reply.requested; got result={result.as_dict()}"
    )

    # 直接看 events.jsonl:不应有 reply.requested
    reply_requested_events = [
        e for e in log.read_all() if e.type == "channel.agent.reply.requested"
    ]
    assert reply_requested_events == [], (
        f"kernel auto-routed an agent-authored mention; reply.requested events: "
        f"{[e.payload for e in reply_requested_events]}"
    )


# ---------------------------------------------------------------- L2 step 3


def test_l2_step3_operator_handoff_then_reply_request_is_emitted(tmp_path: Path) -> None:
    """operator emit channel.handoff.requested(to=claude-arch) → 之后 operator post
    一条 @claude-arch 的 message → 此时 router 允许触发 reply.requested。

    这一步对比 step 2:确认 router 的 gate 是 message 作者身份(operator vs
    agent),而不是无脑黑名单。
    """
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    _seed_channel(log)

    # operator 显式 handoff
    writer.emit(
        "channel.handoff.requested",
        actor="operator",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "to_member_id": "claude-arch",
            "reason": "operator-driven handoff after critic loop",
            "source": "web",
        },
        correlation_id=CHANNEL_ID,
    )

    # operator 再发一条带 @claude-arch 的 message(role=user)
    msg_event = writer.emit(
        "channel.message.posted",
        actor="operator",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "message_id": "msg-op-handoff",
            "member_id": "operator",
            "role": "user",
            "text": "@claude-arch 接手实现",
            "source": "web",
        },
        correlation_id=CHANNEL_ID,
    )

    result = route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=msg_event,
        message_payload=msg_event.payload,
        actor="router",
        source="web",
    )

    assert result.targets == ["claude-arch"]
    assert len(result.reply_requests) == 1

    reply_events = [
        e for e in log.read_all() if e.type == "channel.agent.reply.requested"
    ]
    assert len(reply_events) == 1
    assert reply_events[0].payload["target_member_id"] == "claude-arch"


# ---------------------------------------------------------------- L2 step 4


def test_l2_step4_latest_only_request_id_supersedes_intermediates(tmp_path: Path) -> None:
    """operator 连发 3 条 @claude-arch → projection latest_request_id 反映最后一条;
    中间两条按 latest-only 语义被 dropped / superseded(从 pending 队列移除)。
    """
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    _seed_channel(log)

    request_ids: list[str] = []
    for idx in range(3):
        msg_event = writer.emit(
            "channel.message.posted",
            actor="operator",
            payload={
                "channel_id": CHANNEL_ID,
                "thread_id": "main",
                "message_id": f"msg-burst-{idx}",
                "member_id": "operator",
                "role": "user",
                "text": f"@claude-arch round {idx}",
                "source": "web",
            },
            correlation_id=CHANNEL_ID,
        )
        result = route_channel_message(
            state_dir=state_dir,
            writer=writer,
            message_event=msg_event,
            message_payload=msg_event.payload,
            actor="router",
            source="web",
        )
        # router 为每条都建一个 request,但只有第一条 dispatch,后续 queued
        assert len(result.reply_requests) == 1
        # 提取 request_id 用于后续断言
        latest_reply = [
            e for e in log.read_all() if e.type == "channel.agent.reply.requested"
        ][-1]
        request_ids.append(latest_reply.payload["request_id"])

    detail = project_channel(state_dir, CHANNEL_ID)
    assert detail is not None
    arch_member = next(m for m in detail["members"] if m["member_id"] == "claude-arch")

    # latest_only 语义:最新 request_id 指向最后一条,中间两条不应是 latest
    assert arch_member["latest_request_id"] == request_ids[-1], (
        f"latest_request_id should be the last burst's request; "
        f"got {arch_member['latest_request_id']!r}, expected {request_ids[-1]!r}"
    )
    assert arch_member["latest_request_id"] not in request_ids[:-1], (
        "intermediate request_ids should be dropped from pending, not surfaced as latest"
    )

    # latest-only 第二维度:projection 暴露的 reply_requests 中,无任何
    # intermediate request 仍处于活跃中间态(pending/started/running)。如果
    # kernel 真的把中间 request 卡在 pending,latest-only 就只是嘴上说说。
    intermediate_ids = set(request_ids[:-1])
    active_intermediates = [
        r for r in detail["reply_requests"]
        if r.get("request_id") in intermediate_ids
        and str(r.get("status") or "") in {"pending", "running", "started"}
    ]
    assert active_intermediates == [], (
        f"intermediate replies must not linger as active under latest-only semantics; "
        f"still active: {[(r['request_id'], r['status']) for r in active_intermediates]}"
    )


# ---------------------------------------------------------------- L2 step 5


def test_l2_step5_transcript_order_matches_event_ts_monotonicity(tmp_path: Path) -> None:
    """乱序写入(append 顺序 ≠ ts 顺序),projection.messages 必须按 ts 单调排序,
    不重排。"""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    _seed_channel(log)

    # 故意按 ts 倒序 append:msg-late(ts=30) 先,msg-mid(ts=20) 中,msg-early(ts=10) 后
    log.append(ZfEvent(
        type="channel.message.posted",
        actor="operator",
        ts=_ts(30),
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "message_id": "msg-late",
            "member_id": "operator",
            "text": "third in time",
            "source": "web",
        },
        correlation_id=CHANNEL_ID,
    ))
    log.append(ZfEvent(
        type="channel.message.posted",
        actor="operator",
        ts=_ts(20),
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "message_id": "msg-mid",
            "member_id": "operator",
            "text": "second in time",
            "source": "web",
        },
        correlation_id=CHANNEL_ID,
    ))
    log.append(ZfEvent(
        type="channel.message.posted",
        actor="operator",
        ts=_ts(10),
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": "main",
            "message_id": "msg-early",
            "member_id": "operator",
            "text": "first in time",
            "source": "web",
        },
        correlation_id=CHANNEL_ID,
    ))

    detail = project_channel(state_dir, CHANNEL_ID)
    assert detail is not None
    ordered_ids = [m["message_id"] for m in detail["messages"]]
    assert ordered_ids == ["msg-early", "msg-mid", "msg-late"], (
        f"projection messages must be sorted by ts monotonically; got {ordered_ids}"
    )
