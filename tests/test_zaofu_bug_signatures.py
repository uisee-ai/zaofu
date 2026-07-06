"""β-1: zaofu_bug_signatures detection module.

Per docs/design/36-zero-touch-long-horizon-roadmap.md §4.5 + backlog
backlogs/2026-05-17-1447-zero-touch-beta-self-healing.md (β-1 section).

Each signature is a pure function: (recent_events: list[ZfEvent]) ->
SignatureMatch | None. The orchestrator wraps the scan and emits
zaofu.bug.detected for each high-confidence match.

These tests replay real failure patterns from cangjie r-next-8 and
r-next-9 to lock the detectors. When a future zaofu bug exposes the
same pattern, the orchestrator auto-emits zaofu.bug.detected → the
operator playbook (β-2) and `zf bug-fix-cycle` CLI (β-3) take over.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.model import ZfEvent


# ─── event registration ──────────────────────────────────────────────────


def test_zaofu_bug_detected_in_known_types():
    from zf.core.events.known_types import KNOWN_EVENT_TYPES

    assert "zaofu.bug.detected" in KNOWN_EVENT_TYPES


def test_zaofu_bug_detected_in_wake_patterns():
    from zf.runtime.wake_patterns import WAKE_PATTERNS

    assert "zaofu.bug.detected" in WAKE_PATTERNS


# ─── ship_block_loop signature ───────────────────────────────────────────


def _ship_blocked(blockers: list[str], pdd_id: str = "F-deadbeef") -> ZfEvent:
    return ZfEvent(
        type="ship.blocked",
        actor="zf-cli",
        payload={
            "pdd_id": pdd_id,
            "target_ref": f"candidate/{pdd_id}",
            "blockers": blockers,
        },
    )


def test_ship_block_loop_matches_on_two_identical_blocks():
    """cangjie r-next-8 + r-next-9 both ended with ship.blocked. If the
    same blockers reappear (≥2 attempts with same reasons), signature
    fires — pointing at zaofu/ship.py."""
    from zf.runtime.zaofu_bug_signatures import ship_block_loop_signature

    events = [
        _ship_blocked(["working tree is dirty", "candidate is not ready"]),
        _ship_blocked(["working tree is dirty", "candidate is not ready"]),
    ]
    match = ship_block_loop_signature(events)

    assert match is not None
    assert match.signature == "ship_block_loop"
    assert match.confidence in ("high", "medium")
    assert len(match.evidence_event_ids) == 2
    # The suggested fix area points at ship.py
    assert "ship" in match.suggested_fix_area


def test_ship_block_loop_no_match_on_single_block():
    """A single ship.blocked could be a one-off (operator stash needed).
    Need ≥2 to declare a loop."""
    from zf.runtime.zaofu_bug_signatures import ship_block_loop_signature

    events = [_ship_blocked(["working tree is dirty"])]
    match = ship_block_loop_signature(events)

    assert match is None


def test_ship_block_loop_different_blockers_dont_match():
    """Two ship.blocked with DIFFERENT blockers are not the same bug —
    don't flag as loop."""
    from zf.runtime.zaofu_bug_signatures import ship_block_loop_signature

    events = [
        _ship_blocked(["working tree is dirty"]),
        _ship_blocked(["target ref 'candidate/F-x' not found"]),
    ]
    match = ship_block_loop_signature(events)

    assert match is None


def test_ship_block_loop_extracts_run_state_snapshot():
    """The match must include the pdd_id so the operator playbook knows
    which feature to stash + retry."""
    from zf.runtime.zaofu_bug_signatures import ship_block_loop_signature

    events = [
        _ship_blocked(["working tree is dirty"], pdd_id="F-abcdef00"),
        _ship_blocked(["working tree is dirty"], pdd_id="F-abcdef00"),
    ]
    match = ship_block_loop_signature(events)

    assert match is not None
    snap = match.run_state_snapshot
    assert snap.get("pdd_id") == "F-abcdef00"
    assert match.cangjie_state_snapshot == snap


# ─── respawn_failure_cascade signature ───────────────────────────────────


def _respawn_failed(instance_id: str, reason: str = "spawn never ready") -> ZfEvent:
    return ZfEvent(
        type="worker.respawn.failed",
        actor=instance_id,
        payload={
            "role": instance_id.split("-")[0],
            "instance_id": instance_id,
            "error": reason,
        },
    )


def test_respawn_failure_cascade_matches_on_repeated_failures():
    """cangjie r-next-9: worker.respawn.failed (B-NEW-21 candidate).
    Two in <5min ≡ cascade."""
    from zf.runtime.zaofu_bug_signatures import respawn_failure_cascade_signature

    events = [
        _respawn_failed("dev-1"),
        _respawn_failed("dev-1"),
    ]
    match = respawn_failure_cascade_signature(events)

    assert match is not None
    assert match.signature == "respawn_failure_cascade"
    assert "spawn" in match.suggested_fix_area.lower() or \
        "respawn" in match.suggested_fix_area.lower()


def test_respawn_failure_cascade_single_failure_is_noise():
    from zf.runtime.zaofu_bug_signatures import respawn_failure_cascade_signature

    events = [_respawn_failed("dev-1")]
    match = respawn_failure_cascade_signature(events)

    assert match is None


# ─── judge_failure_loop signature ────────────────────────────────────────


def _judge_failed(task_id: str, reason: str = "evidence schema") -> ZfEvent:
    return ZfEvent(
        type="judge.failed",
        actor="judge",
        task_id=task_id,
        payload={
            "verdict": "reject",
            "summary": reason,
        },
    )


def test_judge_failure_loop_matches_on_three_failures_same_task():
    """B-NEW-12 family: judge whack-a-mole. Same task fails judge ≥3
    times with overlapping reasons → suggest evidence schema fix or
    judge prompt issue."""
    from zf.runtime.zaofu_bug_signatures import judge_failure_loop_signature

    events = [
        _judge_failed("TASK-A", reason="missing evidence runtime"),
        _judge_failed("TASK-A", reason="missing evidence runtime"),
        _judge_failed("TASK-A", reason="missing evidence runtime"),
    ]
    match = judge_failure_loop_signature(events)

    assert match is not None
    assert match.signature == "judge_failure_loop"
    snap = match.run_state_snapshot
    assert snap.get("task_id") == "TASK-A"


def test_judge_failure_loop_no_match_on_two_failures():
    """Two judge.failed is normal rework, not a loop."""
    from zf.runtime.zaofu_bug_signatures import judge_failure_loop_signature

    events = [
        _judge_failed("TASK-A"),
        _judge_failed("TASK-A"),
    ]
    match = judge_failure_loop_signature(events)

    assert match is None


def test_judge_failure_loop_different_tasks_dont_aggregate():
    """3 judge.failed across 3 different tasks is healthy review, not a
    zaofu bug."""
    from zf.runtime.zaofu_bug_signatures import judge_failure_loop_signature

    events = [
        _judge_failed("TASK-A"),
        _judge_failed("TASK-B"),
        _judge_failed("TASK-C"),
    ]
    match = judge_failure_loop_signature(events)

    assert match is None


# ─── ALL_SIGNATURES + scan helper ────────────────────────────────────────


def test_all_signatures_registry_lists_three():
    """The registry must expose all defined signatures for orchestrator
    scan iteration."""
    from zf.runtime.zaofu_bug_signatures import ALL_SIGNATURES

    names = {sig.__name__ for sig in ALL_SIGNATURES}
    assert "ship_block_loop_signature" in names
    assert "respawn_failure_cascade_signature" in names
    assert "judge_failure_loop_signature" in names


def test_scan_returns_all_matches():
    """``scan_zaofu_bugs(events)`` runs every signature, returns the
    list of matches. Replays a mixed event tail where ≥2 signatures
    should fire."""
    from zf.runtime.zaofu_bug_signatures import scan_zaofu_bugs

    events = [
        # Ship loop
        _ship_blocked(["working tree is dirty"]),
        _ship_blocked(["working tree is dirty"]),
        # Judge loop
        _judge_failed("TASK-A"),
        _judge_failed("TASK-A"),
        _judge_failed("TASK-A"),
    ]
    matches = scan_zaofu_bugs(events)

    sig_names = {m.signature for m in matches}
    assert "ship_block_loop" in sig_names
    assert "judge_failure_loop" in sig_names


# ─── wire-up grep ────────────────────────────────────────────────────────


def test_wire_up_orchestrator_runs_zaofu_bug_scan():
    """The orchestrator must invoke scan_zaofu_bugs (probably periodic
    via tick) and emit zaofu.bug.detected per match."""
    src = Path(__file__).resolve().parents[1] / "src/zf/runtime/orchestrator.py"
    text = src.read_text(encoding="utf-8")
    assert "zaofu.bug.detected" in text, (
        "β-1 wire-up missing: orchestrator does not emit zaofu.bug.detected"
    )
    assert "scan_zaofu_bugs" in text or "zaofu_bug_signatures" in text, (
        "β-1 wire-up missing: orchestrator does not import scan_zaofu_bugs"
    )
