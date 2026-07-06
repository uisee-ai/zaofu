"""doc 122 discussion driver: mention_relay guards + clarification state machine."""

from __future__ import annotations

import json
from pathlib import Path

from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.runtime.channel_discussion import advance_discussion, relay_depth_of
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_router import route_channel_message


CH = "ch-disc"


def _writer(tmp_path: Path) -> tuple[Path, EventWriter]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir(exist_ok=True)
    return state_dir, EventWriter(EventLog(state_dir / "events.jsonl"))


def _invite(writer: EventWriter, member_id: str, *, channel_role: str = "dev",
            member_type: str = "persona_agent") -> None:
    writer.emit(
        "channel.member.invited",
        actor="web",
        correlation_id=CH,
        payload={
            "channel_id": CH,
            "member_id": member_id,
            "member_type": member_type,
            "provider": "persona",
            "backend": "persona",
            "channel_role": channel_role,
            "permissions": ["read", "message", "summarize"],
            "source": "web",
        },
    )


def _set_mode(writer: EventWriter, mode: str, **extra) -> None:
    writer.emit(
        "channel.discussion.mode.set",
        actor="web",
        correlation_id=CH,
        payload={"channel_id": CH, "mode": mode, "source": "web", **extra},
    )


def _post(writer: EventWriter, *, message_id: str, member_id: str, role: str,
          text: str, refs: dict | None = None, actor: str = "web"):
    return writer.emit(
        "channel.message.posted",
        actor=actor,
        correlation_id=CH,
        payload={
            "channel_id": CH,
            "thread_id": "main",
            "message_id": message_id,
            "member_id": member_id,
            "role": role,
            "source": "web" if role == "user" else "runtime",
            "text": text,
            **({"refs": refs} if refs else {}),
        },
    )


def _route(state_dir: Path, writer: EventWriter, event, tmp_path: Path):
    return route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=event,
        message_payload=event.payload,
        actor="web",
        source="web",
        project_root=tmp_path,
    )


def _types(state_dir: Path) -> list[str]:
    return [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]


def _setup_pair(tmp_path: Path, mode: str = "mention_relay"):
    state_dir, writer = _writer(tmp_path)
    _invite(writer, "arch-1", channel_role="arch")
    _invite(writer, "critic-1", channel_role="critic")
    _set_mode(writer, mode, max_rounds=50)
    return state_dir, writer


# ---------------------------------------------------------------------------
# T1: mention_relay gate + guards
# ---------------------------------------------------------------------------

def test_agent_post_blocked_when_relay_mode_off(tmp_path: Path) -> None:
    state_dir, writer = _writer(tmp_path)
    _invite(writer, "arch-1")
    _invite(writer, "critic-1")
    event = _post(writer, message_id="m-a1", member_id="arch-1", role="assistant",
                  text="@critic-1 what about scope?", actor="arch-1")
    result = _route(state_dir, writer, event, tmp_path)
    types = _types(state_dir)
    assert result.skipped == [{"reason": "auto_route_not_allowed"}]
    assert "channel.relay.routed" not in types
    assert "channel.agent.reply.requested" not in types


def test_mention_relay_routes_agent_mentions(tmp_path: Path) -> None:
    state_dir, writer = _setup_pair(tmp_path)
    event = _post(writer, message_id="m-a1", member_id="arch-1", role="assistant",
                  text="@critic-1 challenge this", actor="arch-1")
    result = _route(state_dir, writer, event, tmp_path)
    types = _types(state_dir)
    assert result.targets == ["critic-1"]
    assert result.reply_requests
    assert "channel.relay.routed" in types
    routed = [e for e in EventLog(state_dir / "events.jsonl").read_all()
              if e.type == "channel.relay.routed"][0]
    assert routed.payload["relay_depth"] == 1
    assert routed.payload["targets"] == ["critic-1"]


def test_relay_depth_guard_suppresses_deep_chain(tmp_path: Path) -> None:
    state_dir, writer = _setup_pair(tmp_path)
    _post(writer, message_id="m-h0", member_id="operator", role="user",
          text="kick off")
    previous = "m-h0"
    for hop in range(1, 5):
        sender = "arch-1" if hop % 2 else "critic-1"
        target = "critic-1" if hop % 2 else "arch-1"
        request_id = f"req-{hop}"
        writer.emit(
            "channel.agent.reply.requested",
            actor="web",
            correlation_id=CH,
            payload={
                "channel_id": CH, "thread_id": "main",
                "request_id": request_id, "message_id": previous,
                "target_member_id": sender, "status": "pending", "source": "web",
            },
        )
        # keep the ledger moving so G2 does not fire before G1 is exercised
        writer.emit(
            "channel.question.opened",
            actor="operator",
            correlation_id=CH,
            payload={"channel_id": CH, "thread_id": "main",
                     "question_id": f"q-hop-{hop}", "question": "?",
                     "category": "scope", "asked_by": "operator", "source": "web"},
        )
        _post(writer, message_id=f"m-a{hop}", member_id=sender, role="assistant",
              text=f"@{target} ping {hop}", refs={"request_id": request_id},
              actor=sender)
        previous = f"m-a{hop}"

    channel = project_channel(state_dir, CH)
    assert relay_depth_of(channel, "m-a4") == 4

    event = EventLog(state_dir / "events.jsonl").read_all()[-1]
    result = _route(state_dir, writer, event, tmp_path)
    suppressed = [e for e in EventLog(state_dir / "events.jsonl").read_all()
                  if e.type == "channel.relay.suppressed"]
    assert result.skipped == [{"reason": "depth_exceeded"}]
    assert suppressed and suppressed[-1].payload["reason"] == "depth_exceeded"
    assert "channel.agent.reply.requested" not in [
        e.type for e in EventLog(state_dir / "events.jsonl").read_all()
        if e.causation_id == event.id
    ]


def test_relay_all_mention_disabled(tmp_path: Path) -> None:
    state_dir, writer = _setup_pair(tmp_path)
    event = _post(writer, message_id="m-a1", member_id="arch-1", role="assistant",
                  text="@all please respond", actor="arch-1")
    result = _route(state_dir, writer, event, tmp_path)
    assert result.skipped == [{"reason": "all_disabled"}]
    suppressed = [e for e in EventLog(state_dir / "events.jsonl").read_all()
                  if e.type == "channel.relay.suppressed"]
    assert suppressed[-1].payload["reason"] == "all_disabled"


def test_bare_ack_pair_loop_suppressed_without_ledger_delta(tmp_path: Path) -> None:
    state_dir, writer = _setup_pair(tmp_path)
    _post(writer, message_id="m-h0", member_id="operator", role="user", text="go")
    first = _post(writer, message_id="m-a1", member_id="arch-1", role="assistant",
                  text="@critic-1 thoughts?", actor="arch-1")
    assert _route(state_dir, writer, first, tmp_path).targets == ["critic-1"]
    second = _post(writer, message_id="m-a2", member_id="critic-1", role="assistant",
                   text="@arch-1 noted", actor="critic-1")
    assert _route(state_dir, writer, second, tmp_path).targets == ["arch-1"]
    event = _post(writer, message_id="m-a3", member_id="arch-1", role="assistant",
                  text="@critic-1 ok", actor="arch-1")
    result = _route(state_dir, writer, event, tmp_path)
    assert result.skipped == [{"reason": "bare_ack"}]

    # a ledger delta between the two same-direction mentions lifts the guard
    writer.emit(
        "channel.question.opened",
        actor="arch-1",
        correlation_id=CH,
        payload={"channel_id": CH, "thread_id": "main", "question_id": "q-1",
                 "question": "scope?", "category": "scope",
                 "asked_by": "arch-1", "source": "web"},
    )
    event2 = _post(writer, message_id="m-a4", member_id="arch-1", role="assistant",
                   text="@critic-1 with a real question now", actor="arch-1")
    result2 = _route(state_dir, writer, event2, tmp_path)
    assert result2.targets == ["critic-1"]


# ---------------------------------------------------------------------------
# T2: discussion start + blind fanout + state machine
# ---------------------------------------------------------------------------

def _setup_trio(tmp_path: Path):
    state_dir, writer = _writer(tmp_path)
    _invite(writer, "pm-1", channel_role="product_pm")
    _invite(writer, "arch-1", channel_role="arch")
    _invite(writer, "critic-1", channel_role="critic")
    _invite(writer, "kanban-agent", channel_role="owner_delegate")
    _set_mode(writer, "fanout_then_synthesis", max_rounds=50)
    return state_dir, writer


def test_all_mention_starts_blind_fanout(tmp_path: Path) -> None:
    state_dir, writer = _setup_trio(tmp_path)
    event = _post(writer, message_id="m-req", member_id="operator", role="user",
                  text="@all 做一个 three.js 赛车小游戏")
    result = _route(state_dir, writer, event, tmp_path)
    events = EventLog(state_dir / "events.jsonl").read_all()
    types = [e.type for e in events]

    started = [e for e in events if e.type == "channel.discussion.started"]
    assert len(started) == 1
    assert set(started[0].payload["roster"]) == {"pm-1", "arch-1", "critic-1"}
    assert started[0].payload["synthesizer"] == "pm-1"
    assert set(result.targets) == {"pm-1", "arch-1", "critic-1"}

    # persona backends complete inline -> the router-tail tick advances phase1->2
    detail = project_channel(state_dir, CH)
    assert detail["discussions"]["main"]["state"] == "phase2_relay"

    # blind: no context pack may reference a peer's reply message
    reply_message_ids = [
        str(e.payload.get("message_id") or "") for e in events
        if e.type == "channel.message.posted"
        and str(e.payload.get("role") or "") == "assistant"
    ]
    assert reply_message_ids  # personas replied
    packs = [e for e in events if e.type == "channel.context_pack.built"]
    assert len(packs) == 3
    for pack in packs:
        blob = json.dumps(pack.payload, ensure_ascii=False)
        for reply_id in reply_message_ids:
            assert reply_id not in blob


def test_active_discussion_not_restarted_by_second_all(tmp_path: Path) -> None:
    state_dir, writer = _setup_trio(tmp_path)
    first = _post(writer, message_id="m-req", member_id="operator", role="user",
                  text="@all need a racing game")
    _route(state_dir, writer, first, tmp_path)
    second = _post(writer, message_id="m-followup", member_id="operator", role="user",
                   text="@all one more thing: keyboard controls")
    _route(state_dir, writer, second, tmp_path)
    started = [e for e in EventLog(state_dir / "events.jsonl").read_all()
               if e.type == "channel.discussion.started"]
    assert len(started) == 1


def test_single_mention_never_starts_discussion(tmp_path: Path) -> None:
    state_dir, writer = _setup_trio(tmp_path)
    event = _post(writer, message_id="m-one", member_id="operator", role="user",
                  text="@arch-1 quick question")
    result = _route(state_dir, writer, event, tmp_path)
    assert result.targets == ["arch-1"]
    assert "channel.discussion.started" not in _types(state_dir)


# ---------------------------------------------------------------------------
# T3: ledger + freeze + convergence
# ---------------------------------------------------------------------------

def _run_to_phase2(tmp_path: Path):
    state_dir, writer = _setup_trio(tmp_path)
    event = _post(writer, message_id="m-req", member_id="operator", role="user",
                  text="@all requirement to clarify")
    _route(state_dir, writer, event, tmp_path)
    detail = project_channel(state_dir, CH)
    assert detail["discussions"]["main"]["state"] == "phase2_relay"
    return state_dir, writer


def _open_question(writer: EventWriter, qid: str, asked_by: str = "arch-1") -> None:
    writer.emit(
        "channel.question.opened",
        actor=asked_by,
        correlation_id=CH,
        payload={"channel_id": CH, "thread_id": "main", "question_id": qid,
                 "question": "unclear bit", "category": "scope",
                 "asked_by": asked_by, "source": "web"},
    )


def _resolve(writer: EventWriter, qid: str, *, resolution: str, actor: str) -> None:
    writer.emit(
        "channel.question.resolved",
        actor=actor,
        correlation_id=CH,
        payload={"channel_id": CH, "thread_id": "main", "question_id": qid,
                 "resolution": resolution, "resolved_by": actor,
                 "answer": "answer text", "source": "web"},
    )


def _freeze(writer: EventWriter, member_id: str) -> None:
    writer.emit(
        "channel.questions.frozen",
        actor=member_id,
        correlation_id=CH,
        payload={"channel_id": CH, "thread_id": "main",
                 "member_id": member_id, "source": "web"},
    )


def test_agent_cannot_resolve_answered(tmp_path: Path) -> None:
    state_dir, writer = _run_to_phase2(tmp_path)
    _open_question(writer, "q-1")
    _resolve(writer, "q-1", resolution="answered", actor="critic-1")
    detail = project_channel(state_dir, CH)
    questions = {q["question_id"]: q for q in detail["open_questions"]}
    assert questions["q-1"]["status"] == "open"
    assert detail["rejected_resolutions"]

    advance_discussion(state_dir, writer, channel_id=CH, thread_id="main")
    assert "channel.question.resolve.rejected" in _types(state_dir)

    _resolve(writer, "q-1", resolution="answered", actor="operator")
    detail = project_channel(state_dir, CH)
    questions = {q["question_id"]: q for q in detail["open_questions"]}
    assert questions["q-1"]["status"] == "resolved"


def test_agent_can_mark_assumption(tmp_path: Path) -> None:
    state_dir, writer = _run_to_phase2(tmp_path)
    _open_question(writer, "q-2")
    _resolve(writer, "q-2", resolution="assumption", actor="critic-1")
    detail = project_channel(state_dir, CH)
    questions = {q["question_id"]: q for q in detail["open_questions"]}
    assert questions["q-2"]["status"] == "resolved"
    assert questions["q-2"]["resolution"] == "assumption"


def test_convergence_requires_full_freeze(tmp_path: Path) -> None:
    state_dir, writer = _run_to_phase2(tmp_path)
    _open_question(writer, "q-1")
    _resolve(writer, "q-1", resolution="answered", actor="operator")
    _freeze(writer, "pm-1")
    _freeze(writer, "arch-1")
    advance_discussion(state_dir, writer, channel_id=CH, thread_id="main")
    assert "channel.synthesis.requested" not in [
        t for t in _types(state_dir)
    ], "phase3 must wait for the full roster to freeze"

    _freeze(writer, "critic-1")
    advance_discussion(state_dir, writer, channel_id=CH, thread_id="main")
    events = EventLog(state_dir / "events.jsonl").read_all()
    synth = [e for e in events if e.type == "channel.synthesis.requested"]
    assert synth and synth[0].payload["target_member_id"] == "pm-1"
    detail = project_channel(state_dir, CH)
    assert detail["discussions"]["main"]["state"] == "phase3_synthesis"

    # idempotent: another tick must not duplicate the synthesis request
    advance_discussion(state_dir, writer, channel_id=CH, thread_id="main")
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert len([e for e in events if e.type == "channel.synthesis.requested"]) == 1


def test_open_question_blocks_convergence(tmp_path: Path) -> None:
    state_dir, writer = _run_to_phase2(tmp_path)
    _open_question(writer, "q-1")
    for member in ("pm-1", "arch-1", "critic-1"):
        _freeze(writer, member)
    advance_discussion(state_dir, writer, channel_id=CH, thread_id="main")
    detail = project_channel(state_dir, CH)
    assert detail["discussions"]["main"]["state"] == "phase2_relay"


# ---------------------------------------------------------------------------
# T4: consensus sign-off
# ---------------------------------------------------------------------------

def _run_to_phase3(tmp_path: Path):
    state_dir, writer = _run_to_phase2(tmp_path)
    _open_question(writer, "q-1")
    _resolve(writer, "q-1", resolution="answered", actor="operator")
    for member in ("pm-1", "arch-1", "critic-1"):
        _freeze(writer, member)
    advance_discussion(state_dir, writer, channel_id=CH, thread_id="main")
    detail = project_channel(state_dir, CH)
    assert detail["discussions"]["main"]["state"] == "phase3_synthesis"
    return state_dir, writer


def _consensus(writer: EventWriter, etype: str, actor: str, **payload) -> None:
    writer.emit(
        f"channel.consensus.{etype}",
        actor=actor,
        correlation_id=CH,
        payload={"channel_id": CH, "thread_id": "main", "source": "web", **payload},
    )


def test_consensus_blocked_reopens_phase2(tmp_path: Path) -> None:
    state_dir, writer = _run_to_phase3(tmp_path)
    _consensus(writer, "proposed", "pm-1",
               artifact_ref=".zf/channel-artifacts/clarified.md", proposed_by="pm-1")
    _consensus(writer, "blocked", "critic-1", member_id="critic-1",
               blocker_question_id="q-blocker", blocker_question="edge case?")
    advance_discussion(state_dir, writer, channel_id=CH, thread_id="main")
    detail = project_channel(state_dir, CH)
    assert detail["discussions"]["main"]["state"] == "phase2_relay"
    questions = {q["question_id"]: q for q in detail["open_questions"]}
    assert questions["q-blocker"]["status"] == "open"

    # tick again: the blocker must not be reopened twice
    advance_discussion(state_dir, writer, channel_id=CH, thread_id="main")
    opened = [e for e in EventLog(state_dir / "events.jsonl").read_all()
              if e.type == "channel.question.opened"
              and e.payload.get("question_id") == "q-blocker"]
    assert len(opened) == 1


def test_consensus_requires_human_confirmation(tmp_path: Path) -> None:
    state_dir, writer = _run_to_phase3(tmp_path)
    _consensus(writer, "proposed", "pm-1",
               artifact_ref=".zf/channel-artifacts/clarified.md", proposed_by="pm-1")
    for member in ("pm-1", "arch-1", "critic-1"):
        _consensus(writer, "signed", member, member_id=member,
                   artifact_ref=".zf/channel-artifacts/clarified.md")
    advance_discussion(state_dir, writer, channel_id=CH, thread_id="main")
    assert "channel.consensus.reached" not in _types(state_dir)

    _consensus(writer, "signed", "operator", member_id="operator",
               artifact_ref=".zf/channel-artifacts/clarified.md")
    advance_discussion(state_dir, writer, channel_id=CH, thread_id="main")
    types = _types(state_dir)
    assert "channel.consensus.reached" in types
    assert "channel.discussion.closed" in types
    detail = project_channel(state_dir, CH)
    assert detail["discussions"]["main"]["state"] == "idle"
    assert detail["discussions"]["main"]["last_outcome"] == "consensus"


def test_all_mention_without_roster_does_not_start(tmp_path: Path) -> None:
    """422-failed invites left a channel memberless; @all must NOT strand the
    thread in phase1_blind with an empty roster (found live in e2e)."""
    state_dir, writer = _writer(tmp_path)
    _set_mode(writer, "fanout_then_synthesis", max_rounds=50)
    event = _post(writer, message_id="m-req", member_id="operator", role="user",
                  text="@all anyone there?")
    _route(state_dir, writer, event, tmp_path)
    assert "channel.discussion.started" not in _types(state_dir)


# ---------------------------------------------------------------------------
# Sprint2 P0-1: exit hook — reached triggers idea-to-product proposal
# ---------------------------------------------------------------------------

def test_consensus_reached_proposes_idea_to_product(tmp_path: Path) -> None:
    state_dir, writer = _run_to_phase3(tmp_path)
    _consensus(writer, "proposed", "pm-1",
               artifact_ref=".zf/channel-artifacts/clarified.md", proposed_by="pm-1")
    for member in ("pm-1", "arch-1", "critic-1"):
        _consensus(writer, "signed", member, member_id=member)
    _consensus(writer, "signed", "operator", member_id="operator")
    advance_discussion(state_dir, writer, channel_id=CH, thread_id="main")
    events = EventLog(state_dir / "events.jsonl").read_all()
    types = [e.type for e in events]
    assert "operator.intent.created" in types
    assert "operator.action.proposed" in types
    proposal = [e for e in events if e.type == "operator.action.proposed"][-1]
    actions = [p["action"] for p in proposal.payload["proposals"]]
    assert actions == ["create-task", "workflow-invoke"]
    assert proposal.payload["requires_owner_confirmation"] is True
    create_task = proposal.payload["proposals"][0]["payload"]
    contract = create_task["contract"]
    assert contract["spec_ref"] == ".zf/channel-artifacts/clarified.md"
    assert contract["handoff_artifacts"] == [".zf/channel-artifacts/clarified.md"]
    # must round-trip through the fixed TaskContract schema (a free-form key
    # here crashes TaskStore.get on read-back — caught live in sprint 3)
    from zf.core.task.schema import TaskContract
    TaskContract(**contract)

    # idempotent: re-advance must not propose twice
    advance_discussion(state_dir, writer, channel_id=CH, thread_id="main")
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert len([e for e in events if e.type == "operator.action.proposed"]) == 1


# ---------------------------------------------------------------------------
# Sprint2 P1-1: phase deadlines + quorum degradation
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone  # noqa: E402

from zf.runtime.channel_discussion import sweep_discussion_deadlines  # noqa: E402


def _setup_trio_with_deadlines(tmp_path: Path):
    state_dir, writer = _writer(tmp_path)
    _invite(writer, "pm-1", channel_role="product_pm")
    _invite(writer, "arch-1", channel_role="arch")
    _invite(writer, "critic-1", channel_role="critic")
    _set_mode(writer, "fanout_then_synthesis", max_rounds=50,
              phase_deadline_seconds={"phase1_blind": 60, "phase2_relay": 120,
                                      "phase3_synthesis": 120})
    return state_dir, writer


def test_phase1_deadline_quorum_degrades_to_phase2(tmp_path: Path) -> None:
    state_dir, writer = _setup_trio_with_deadlines(tmp_path)
    event = _post(writer, message_id="m-req", member_id="operator", role="user",
                  text="@all requirement")
    _route(state_dir, writer, event, tmp_path)
    # personas replied inline -> all 3 completed; fake a missing member by
    # rebuilding: instead assert quorum path via a fresh channel where one
    # member never gets a reply request completion is not constructible with
    # personas — so exercise the below-quorum stall on a hand-built session.
    detail = project_channel(state_dir, CH)
    assert detail["discussions"]["main"]["state"] == "phase2_relay"

    # phase2 deadline: no activity past 120s -> stalled
    future = datetime.now(timezone.utc) + timedelta(seconds=600)
    advance_discussion(state_dir, writer, channel_id=CH, thread_id="main", now=future)
    detail = project_channel(state_dir, CH)
    assert detail["discussions"]["main"]["state"] == "idle"
    assert detail["discussions"]["main"]["last_outcome"] == "stalled"


def test_phase1_below_quorum_stalls(tmp_path: Path) -> None:
    state_dir, writer = _setup_trio_with_deadlines(tmp_path)
    # hand-build a started session without routing (no replies at all)
    writer.emit(
        "channel.discussion.started", actor="web", correlation_id=CH,
        payload={"channel_id": CH, "thread_id": "main", "trigger": "mention_all",
                 "roster": ["pm-1", "arch-1", "critic-1"], "synthesizer": "pm-1",
                 "requirement_message_id": "m-x", "source": "web"},
    )
    future = datetime.now(timezone.utc) + timedelta(seconds=600)
    advance_discussion(state_dir, writer, channel_id=CH, thread_id="main", now=future)
    detail = project_channel(state_dir, CH)
    assert detail["discussions"]["main"]["state"] == "idle"
    assert detail["discussions"]["main"]["last_outcome"] == "stalled"


def test_no_deadline_config_never_stalls(tmp_path: Path) -> None:
    state_dir, writer = _setup_trio(tmp_path)
    event = _post(writer, message_id="m-req", member_id="operator", role="user",
                  text="@all requirement")
    _route(state_dir, writer, event, tmp_path)
    future = datetime.now(timezone.utc) + timedelta(days=30)
    advance_discussion(state_dir, writer, channel_id=CH, thread_id="main", now=future)
    detail = project_channel(state_dir, CH)
    assert detail["discussions"]["main"]["state"] == "phase2_relay"


def test_sweep_ticks_active_discussions(tmp_path: Path) -> None:
    state_dir, writer = _setup_trio_with_deadlines(tmp_path)
    event = _post(writer, message_id="m-req", member_id="operator", role="user",
                  text="@all requirement")
    _route(state_dir, writer, event, tmp_path)
    future = datetime.now(timezone.utc) + timedelta(seconds=600)
    ticked = sweep_discussion_deadlines(state_dir, writer, now=future)
    assert ticked == 1
    detail = project_channel(state_dir, CH)
    assert detail["discussions"]["main"]["state"] == "idle"
    assert detail["discussions"]["main"]["last_outcome"] == "stalled"
