"""Unit tests for tests/longhorizon/run_zaofu_channel_real.py.

Covers pure helpers only — no zf emit, no subprocess to cj-mono.
The integration aspects are exercised by the dry-run invocation
documented in tasks/2026-05-18-1330-eval-workflow-simulation-cangjie-mono.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from tests.longhorizon.run_zaofu_channel_real import (
    CHANNEL_POST_ACTION,
    ChannelIds,
    _channel_post_url,
    build_seed_steps,
    make_channel_ids,
)
from tests.longhorizon.run_zaofu_channel_real_pair import (
    L4_PAIR_DEV_TEXT,
    L4_PAIR_REVIEW_TEXT,
    build_round2_step,
)
from tests.longhorizon.run_zaofu_channel_real_l5 import (
    ROUNDTABLE_MEMBERS,
    build_roundtable_rounds,
)


def test_make_channel_ids_uses_utc_stamp():
    now = datetime(2026, 5, 29, 12, 34, tzinfo=timezone.utc)
    ids = make_channel_ids(now)
    assert ids.channel_id == "ch-l4-pair-20260529t1234"
    assert ids.thread_id == "thr-main"
    assert ids.dev_member == "dev-cc-1"
    assert ids.review_member == "review-cdx-1"


def test_seed_steps_required_fields_present():
    ids = ChannelIds(channel_id="ch-test")
    steps = build_seed_steps(ids)
    types = [s.type for s in steps]
    # The router emits channel.agent.reply.requested itself once it sees
    # the @mention in text / the mentions list — the runner must not emit
    # it manually (there is no reactor for raw reply.requested).
    assert types == [
        "channel.created",
        "channel.member.added",
        "channel.member.added",
        "channel.message.posted",
    ]
    assert "channel.agent.reply.requested" not in types
    # channel.created schema: channel_id + name + source
    created = steps[0].payload
    assert {"channel_id", "name", "source"} <= created.keys()
    # member.added: channel_id + member_id + source
    for member_step in steps[1:3]:
        p = member_step.payload
        assert {"channel_id", "member_id", "source"} <= p.keys()
    # message.posted: channel_id + thread_id + source + message_id + text;
    # must carry an @mention so the router can resolve a target.
    msg = steps[3].payload
    assert {"channel_id", "thread_id", "source", "message_id", "text"} <= msg.keys()
    assert "@" + ids.dev_member in msg["text"]
    assert msg.get("mentions") == [ids.dev_member]


def test_seed_steps_carry_consistent_channel_id():
    ids = ChannelIds(channel_id="ch-consistency")
    steps = build_seed_steps(ids)
    for step in steps:
        assert step.payload["channel_id"] == "ch-consistency"


def test_seed_steps_via_web_swaps_post_for_action():
    ids = ChannelIds(channel_id="ch-viaweb")
    steps = build_seed_steps(ids, via_web=True)
    # 3 raw emits (created + 2 members) + 1 web action post; the
    # reply.requested step is dropped because the action triggers
    # route_channel_message internally.
    types = [s.type for s in steps]
    kinds = [s.kind for s in steps]
    assert types == [
        "channel.created",
        "channel.member.added",
        "channel.member.added",
        CHANNEL_POST_ACTION,
    ]
    assert kinds == ["emit", "emit", "emit", "web_action"]
    post_step = steps[-1]
    # Action envelope expectations: channel_id, thread_id, text, role,
    # member_id.
    p = post_step.payload
    assert {"channel_id", "thread_id", "text", "role", "member_id"} <= p.keys()
    assert p["role"] == "user"
    assert p["member_id"] == ids.op_member
    assert p["channel_id"] == "ch-viaweb"


def test_build_seed_steps_honors_target_member_override():
    ids = ChannelIds(channel_id="ch-override")
    steps = build_seed_steps(ids, target_member="review-cdx-1")
    msg = steps[-1].payload
    assert "@review-cdx-1" in msg["text"]
    assert msg["mentions"] == ["review-cdx-1"]


def test_l4_pair_round1_targets_dev_round2_targets_review():
    """l4-pair: round-1 seed = full setup with dev mention; round-2 = reviewer.

    Both rounds must share the same channel_id + thread_id so the
    orchestrator threads them together.
    """
    ids = ChannelIds(channel_id="ch-pair-test")
    round1 = build_seed_steps(
        ids, target_member=ids.dev_member, user_text=L4_PAIR_DEV_TEXT,
    )
    round2 = build_round2_step(ids)
    # Round 1 should establish the channel + both members + dev message.
    types_r1 = [s.type for s in round1]
    assert types_r1 == [
        "channel.created",
        "channel.member.added",
        "channel.member.added",
        "channel.message.posted",
    ]
    member_ids = [s.payload["member_id"] for s in round1[1:3]]
    assert set(member_ids) == {ids.dev_member, ids.review_member}, (
        "both members must be added to the SAME channel before the pair flow"
    )
    # Round 1 dev message mentions dev only.
    dev_msg = round1[-1].payload
    assert dev_msg["mentions"] == [ids.dev_member]
    assert L4_PAIR_DEV_TEXT in dev_msg["text"]
    assert f"@{ids.dev_member}" in dev_msg["text"]
    # Round 2 mentions reviewer, shares channel_id + thread_id, uses a
    # distinct message_id from round 1.
    assert round2.type == "channel.message.posted"
    assert round2.payload["channel_id"] == ids.channel_id
    assert round2.payload["thread_id"] == ids.thread_id
    assert round2.payload["mentions"] == [ids.review_member]
    assert f"@{ids.review_member}" in round2.payload["text"]
    assert L4_PAIR_REVIEW_TEXT in round2.payload["text"]
    assert round2.payload["message_id"] != dev_msg["message_id"]


def test_l5_roundtable_builds_four_distinct_round_targets():
    """l5: 4 rounds, each targeting a distinct member from ROUNDTABLE_MEMBERS.

    Order matters (arch → critic → dev → review) and the 4 targets must
    be exactly the 4 configured roundtable member_ids — no dupes, no
    drift from the member roster.
    """
    rounds = build_roundtable_rounds()
    assert len(rounds) == 4
    targets = [r.target for r in rounds]
    assert len(set(targets)) == 4, f"targets must be distinct: {targets}"
    member_ids = [m.member_id for m in ROUNDTABLE_MEMBERS]
    assert set(targets) == set(member_ids), (
        f"round targets {targets} must match member roster {member_ids}"
    )
    # Sequence is fixed: arch → critic → dev → review.
    assert targets == ["arch-cc-1", "critic-cdx-1", "dev-cc-1", "review-cdx-1"]
    # Round indices are 1-based and sequential.
    assert [r.index for r in rounds] == [1, 2, 3, 4]


def test_channel_post_url_construction():
    url = _channel_post_url("http://127.0.0.1:8002", "cj-mono")
    assert url == (
        "http://127.0.0.1:8002/api/projects/cj-mono/actions/channel-post-message"
    )
    # Trailing slash on the base must not duplicate the separator.
    url2 = _channel_post_url("http://127.0.0.1:8002/", "default")
    assert "//api/projects" not in url2
    assert url2.endswith("/api/projects/default/actions/channel-post-message")
