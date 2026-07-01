"""EVAL-HANDOFF-SCORE-001 — handoff completeness 10-dimension scoring."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from zf.cli.handoff import (
    compute_handoff_score,
    render_score_md,
)
from zf.core.state.state_packet import (
    StatePacket,
    StatePacketContract,
    StatePacketEvidence,
    StatePacketOwner,
    StatePacketRefs,
)


def _full_packet() -> StatePacket:
    """Build a packet that should score 8/10 (recovery + projection
    files don't exist on disk in test tmp_path)."""
    return StatePacket(
        run_id="run-abc",
        task_id="TASK-FULL",
        objective="ship feature",
        current_stage="implement",
        owner=StatePacketOwner(role="dev", instance_id="dev-1"),
        contract=StatePacketContract(
            behavior="do the thing",
            acceptance=("step1", "step2"),
        ),
        refs=StatePacketRefs(
            base_ref="main",
            task_ref="refs/zaofu/tasks/TASK-FULL",
        ),
        evidence=(StatePacketEvidence(
            kind="test", path="/log", status="passed", event_id="evt-1",
        ),),
        next_event="dev.build.done",
        risks=("known race condition",),
    )


# ---------------------------------------------------------------------------
# compute_handoff_score — dimension scoring
# ---------------------------------------------------------------------------


def test_full_packet_scores_8_when_no_disk_artifacts(tmp_path: Path) -> None:
    """Recovery + projection files are off-disk by default; everything
    else fills → 8/10."""
    score = compute_handoff_score(_full_packet(), state_dir=tmp_path)
    assert score["score"] == 8
    assert score["max_score"] == 10


def test_packet_no_task_scores_zero(tmp_path: Path) -> None:
    pkt = StatePacket(task_id="", current_stage="no_task")
    score = compute_handoff_score(pkt, state_dir=tmp_path)
    assert score["score"] <= 2  # base_ref defaults to 'main' so refs partial


def test_score_dimensions_count(tmp_path: Path) -> None:
    """10 dimensions always returned regardless of content."""
    score = compute_handoff_score(StatePacket(), state_dir=tmp_path)
    assert score["max_score"] == 10
    assert len(score["dimensions"]) == 10


def test_dimensions_have_required_keys(tmp_path: Path) -> None:
    score = compute_handoff_score(_full_packet(), state_dir=tmp_path)
    for d in score["dimensions"]:
        assert "name" in d
        assert "passed" in d
        assert "hint" in d
        assert isinstance(d["passed"], bool)


def test_recovery_ready_when_briefing_exists(tmp_path: Path) -> None:
    """If .zf/briefings/<task>/<dispatch>/state-packet.md exists,
    recovery_briefing_ready passes."""
    pkt = _full_packet()
    briefing_dir = tmp_path / "briefings" / pkt.task_id / "disp-1"
    briefing_dir.mkdir(parents=True)
    (briefing_dir / "state-packet.md").write_text("hi")
    score = compute_handoff_score(pkt, state_dir=tmp_path)
    recovery_dim = next(
        d for d in score["dimensions"]
        if d["name"] == "recovery_briefing_ready"
    )
    assert recovery_dim["passed"] is True


def test_projection_files_ready_when_4_files_exist(tmp_path: Path) -> None:
    pkt = _full_packet()
    proj_dir = tmp_path / "projections" / "tasks" / pkt.task_id
    proj_dir.mkdir(parents=True)
    for name in ("plan.md", "findings.md", "progress.md", "attempt-ledger.md"):
        (proj_dir / name).write_text("hi")
    score = compute_handoff_score(pkt, state_dir=tmp_path)
    proj_dim = next(
        d for d in score["dimensions"]
        if d["name"] == "projection_files_ready"
    )
    assert proj_dim["passed"] is True


def test_full_score_10_when_all_disk_artifacts_present(tmp_path: Path) -> None:
    pkt = _full_packet()
    briefing_dir = tmp_path / "briefings" / pkt.task_id / "disp-1"
    briefing_dir.mkdir(parents=True)
    (briefing_dir / "state-packet.md").write_text("hi")
    proj_dir = tmp_path / "projections" / "tasks" / pkt.task_id
    proj_dir.mkdir(parents=True)
    for name in ("plan.md", "findings.md", "progress.md", "attempt-ledger.md"):
        (proj_dir / name).write_text("hi")
    score = compute_handoff_score(pkt, state_dir=tmp_path)
    assert score["score"] == 10


def test_missing_acceptance_lowers_score(tmp_path: Path) -> None:
    pkt = _full_packet()
    pkt_no_accept = StatePacket(
        run_id=pkt.run_id, task_id=pkt.task_id, objective=pkt.objective,
        current_stage=pkt.current_stage, owner=pkt.owner,
        contract=StatePacketContract(behavior=pkt.contract.behavior),
        refs=pkt.refs, evidence=pkt.evidence, next_event=pkt.next_event,
        risks=pkt.risks,
    )
    score = compute_handoff_score(pkt_no_accept, state_dir=tmp_path)
    accept_dim = next(
        d for d in score["dimensions"]
        if d["name"] == "acceptance_criteria_present"
    )
    assert accept_dim["passed"] is False
    assert accept_dim["hint"] != ""


def test_missing_residual_risks_lowers_score(tmp_path: Path) -> None:
    pkt = _full_packet()
    pkt_no_risks = StatePacket(
        run_id=pkt.run_id, task_id=pkt.task_id, objective=pkt.objective,
        current_stage=pkt.current_stage, owner=pkt.owner,
        contract=pkt.contract, refs=pkt.refs, evidence=pkt.evidence,
        next_event=pkt.next_event, risks=(),
    )
    score = compute_handoff_score(pkt_no_risks, state_dir=tmp_path)
    risks_dim = next(
        d for d in score["dimensions"]
        if d["name"] == "residual_risks_recorded"
    )
    assert risks_dim["passed"] is False


# ---------------------------------------------------------------------------
# render_score_md
# ---------------------------------------------------------------------------


def test_render_md_contains_score_line() -> None:
    score = {
        "score": 8,
        "max_score": 10,
        "dimensions": [
            {"name": "run_id_present", "passed": True, "hint": ""},
            {"name": "task_id_present", "passed": False, "hint": "set task"},
        ],
    }
    out = render_score_md(score)
    assert "## Handoff Quality Score: 8/10" in out
    assert "✓ run_id_present" in out
    assert "✗ task_id_present" in out
    assert "set task" in out


def test_render_md_no_gap_message_when_full() -> None:
    score = {
        "score": 10,
        "max_score": 10,
        "dimensions": [
            {"name": "x", "passed": True, "hint": ""}
        ] * 10,
    }
    out = render_score_md(score)
    assert "## Handoff Quality Score: 10/10" in out
    assert "To score 10/10:" not in out  # no gap message
