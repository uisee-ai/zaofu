"""Sprint §7 — termination conditions tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.autoresearch.loop import (
    EvalDelta,
    EvalSnapshot,
    IterationRecord,
    LoopConfig,
    LoopTerminationDecision,
    LoopState,
    should_stop_loop,
)


def _snap(critical: int = 7, completed: int = 0) -> EvalSnapshot:
    return EvalSnapshot(8, 3, critical, 0.179, 8, 1, completed)


def _rec(
    iter: int,
    *,
    status: str = "failed",
    delta_verdict: str | None = None,
    completed: int = 0,
) -> IterationRecord:
    delta = None
    if delta_verdict is not None:
        delta = EvalDelta(
            healthy_delta=0, critical_delta=0,
            coordinator_delta=0.0, backlog_delta=0,
            completed_delta=0, verdict=delta_verdict,
        )
    return IterationRecord(
        iter=iter, started_at="t",
        scenario="s", run_id=f"r{iter}",
        run_status=status, tasks_done=completed, expected_done=3,
        eval=_snap(completed=completed),
        delta=delta, reflect=None,
        git_head=f"sha{iter}",
        head_changed_since_prev=(iter > 1),
        summary=f"iter {iter}",
    )


def _cfg(**kw) -> LoopConfig:
    defaults = dict(
        scenarios=["s"],
        worktree=Path("/tmp/x"),
        parent_state_dir=Path("/tmp/x/.zf"),
        max_iterations=10,
        budget_usd=200.0,
    )
    defaults.update(kw)
    return LoopConfig(**defaults)


# ---------------------------------------------------------------------------
# Convergence: 2 consecutive passed-with-improved
# ---------------------------------------------------------------------------


def test_single_passed_iter_does_not_converge() -> None:
    """Need at least 2 passed iter in a row to converge."""
    cfg = _cfg(max_iterations=10)
    state = LoopState(
        cost_usd_so_far=0.0,
        consecutive_passed=1,
        consecutive_regressed=0,
    )
    rec = _rec(1, status="passed", delta_verdict="improved")
    d = should_stop_loop(record=rec, cfg=cfg, state=state)
    assert d.should_stop is False


def test_two_consecutive_passed_converges() -> None:
    cfg = _cfg(max_iterations=10)
    state = LoopState(
        cost_usd_so_far=0.0,
        consecutive_passed=2,   # this iter is the 2nd consecutive passed
        consecutive_regressed=0,
    )
    rec = _rec(3, status="passed", delta_verdict="improved")
    d = should_stop_loop(record=rec, cfg=cfg, state=state)
    assert d.should_stop is True
    assert d.final_status == "converged"


def test_passed_then_fail_resets_streak() -> None:
    """A 'passed' streak must reset when a later iter fails."""
    cfg = _cfg()
    state = LoopState(
        cost_usd_so_far=0.0,
        consecutive_passed=0,
        consecutive_regressed=0,
    )
    rec = _rec(3, status="failed", delta_verdict="regressed")
    d = should_stop_loop(record=rec, cfg=cfg, state=state)
    assert d.should_stop is False


# ---------------------------------------------------------------------------
# Hard max iterations
# ---------------------------------------------------------------------------


def test_max_iterations_hits_stop() -> None:
    cfg = _cfg(max_iterations=3)
    state = LoopState(cost_usd_so_far=0.0)
    rec = _rec(3, status="failed", delta_verdict="unchanged")
    d = should_stop_loop(record=rec, cfg=cfg, state=state)
    assert d.should_stop is True
    assert d.final_status == "max_iter_unmet"
    assert "missing_done=3" in d.reason


def test_max_iterations_with_expected_done_reports_done() -> None:
    cfg = _cfg(max_iterations=1)
    state = LoopState(cost_usd_so_far=0.0)
    rec = _rec(1, status="passed", completed=3)
    d = should_stop_loop(record=rec, cfg=cfg, state=state)
    assert d.should_stop is True
    assert d.final_status == "done"
    assert "outcome=passed" in d.reason


def test_below_max_does_not_stop_on_iter_count_alone() -> None:
    cfg = _cfg(max_iterations=10)
    state = LoopState(cost_usd_so_far=0.0)
    rec = _rec(2, status="failed", delta_verdict="unchanged")
    d = should_stop_loop(record=rec, cfg=cfg, state=state)
    assert d.should_stop is False


# ---------------------------------------------------------------------------
# Budget exhaustion
# ---------------------------------------------------------------------------


def test_budget_exhausted_stops() -> None:
    cfg = _cfg(budget_usd=50.0, max_iterations=100)
    state = LoopState(cost_usd_so_far=51.0)
    rec = _rec(2, status="failed")
    d = should_stop_loop(record=rec, cfg=cfg, state=state)
    assert d.should_stop is True
    assert d.final_status == "budget_exhausted"


def test_below_budget_does_not_stop() -> None:
    cfg = _cfg(budget_usd=50.0, max_iterations=100)
    state = LoopState(cost_usd_so_far=49.99)
    rec = _rec(2, status="failed")
    d = should_stop_loop(record=rec, cfg=cfg, state=state)
    assert d.should_stop is False


# ---------------------------------------------------------------------------
# Regress streak (3 consecutive)
# ---------------------------------------------------------------------------


def test_three_consecutive_regress_stops() -> None:
    cfg = _cfg(max_iterations=10)
    state = LoopState(
        cost_usd_so_far=0.0,
        consecutive_passed=0,
        consecutive_regressed=3,
    )
    rec = _rec(4, status="failed", delta_verdict="regressed")
    d = should_stop_loop(record=rec, cfg=cfg, state=state)
    assert d.should_stop is True
    assert d.final_status == "no_progress"


def test_two_regress_does_not_stop() -> None:
    cfg = _cfg(max_iterations=10)
    state = LoopState(
        cost_usd_so_far=0.0,
        consecutive_regressed=2,
    )
    rec = _rec(3, status="failed", delta_verdict="regressed")
    d = should_stop_loop(record=rec, cfg=cfg, state=state)
    assert d.should_stop is False


def test_improved_iter_breaks_regress_streak() -> None:
    """3 regress already counted, but if THIS iter improves, that's
    a fresh signal — don't terminate."""
    cfg = _cfg(max_iterations=10)
    # state.consecutive_regressed=2 means *prior* 2 were regressed;
    # the driver will reset to 0 because this rec.delta.verdict ==
    # "improved". should_stop_loop reads the state AS PROVIDED, so
    # the driver must pre-reset before calling.
    state = LoopState(
        cost_usd_so_far=0.0,
        consecutive_regressed=0,    # reset by driver
    )
    rec = _rec(3, status="failed", delta_verdict="improved")
    d = should_stop_loop(record=rec, cfg=cfg, state=state)
    assert d.should_stop is False


# ---------------------------------------------------------------------------
# Priority: budget > max-iteration outcome > regress > convergence
# ---------------------------------------------------------------------------


def test_budget_wins_over_convergence() -> None:
    """Even with 2 consecutive passed, if budget is blown we stop on
    budget — operator sees the actual reason."""
    cfg = _cfg(max_iterations=10, budget_usd=10.0)
    state = LoopState(
        cost_usd_so_far=11.0,
        consecutive_passed=2,
    )
    rec = _rec(3, status="passed", delta_verdict="improved")
    d = should_stop_loop(record=rec, cfg=cfg, state=state)
    assert d.should_stop is True
    assert d.final_status == "budget_exhausted"
