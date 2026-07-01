"""ZF-PWF-STOP-GUARD-001 — stop_guard.evaluate_stop_gates tests.

Pure-function gate evaluator: given a Task + has_success_event callback,
returns a StopGuardResult.blocked flag + missing list + continue advice.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.known_types import KNOWN_EVENT_TYPES
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.cli import hook_recv
from zf.runtime.stop_guard import (
    StopGuardResult,
    evaluate_stop_gates,
)


def _task(
    *,
    tiers: list[str] | None = None,
    active_dispatch_id: str = "disp-1",
) -> Task:
    return Task(
        id="TASK-1",
        title="demo",
        status="in_progress",
        active_dispatch_id=active_dispatch_id,
        contract=TaskContract(
            behavior="do the thing",
            verification_tiers=tiers or [],
        ),
    )


def _yes(*_: object) -> bool:
    return True


def _no(*_: object) -> bool:
    return False


# ---------------------------------------------------------------------------
# Event-registry wire-up
# ---------------------------------------------------------------------------


def test_provider_stop_check_is_known_event() -> None:
    assert "provider.stop.check" in KNOWN_EVENT_TYPES


# ---------------------------------------------------------------------------
# Empty / no-op cases
# ---------------------------------------------------------------------------


def test_no_task_returns_empty_missing() -> None:
    result = evaluate_stop_gates(None, has_success_event=_no)
    assert result.missing == []
    assert result.required == []
    assert result.blocked is False
    assert result.advice == ""


def test_task_without_verification_tiers_no_gates() -> None:
    """Contract didn't declare gates → defer to kernel discriminator."""
    task = _task(tiers=[])
    result = evaluate_stop_gates(task, has_success_event=_no)
    assert result.blocked is False
    assert result.missing == []


def test_task_without_dispatch_id_is_blocked() -> None:
    """Missing active_dispatch_id is a hard block (dispatch didn't
    record; stopping now would orphan the task)."""
    task = _task(active_dispatch_id="", tiers=[])
    result = evaluate_stop_gates(task, has_success_event=_yes)
    assert result.blocked is True
    assert "active_dispatch_id" in result.missing


# ---------------------------------------------------------------------------
# Tier → event mapping
# ---------------------------------------------------------------------------


def test_review_tier_maps_to_review_approved_event() -> None:
    task = _task(tiers=["review"])
    # No review.approved event yet
    result = evaluate_stop_gates(task, has_success_event=_no)
    assert result.blocked is True
    assert "review.approved" in result.missing
    assert "review.approved" in result.required


def test_test_tier_maps_to_test_passed_event() -> None:
    task = _task(tiers=["test"])
    result = evaluate_stop_gates(task, has_success_event=_no)
    assert "test.passed" in result.missing


def test_judge_tier_maps_to_judge_passed_event() -> None:
    task = _task(tiers=["judge"])
    result = evaluate_stop_gates(task, has_success_event=_no)
    assert "judge.passed" in result.missing


def test_discriminator_tier_maps_to_discriminator_passed() -> None:
    task = _task(tiers=["discriminator"])
    result = evaluate_stop_gates(task, has_success_event=_no)
    assert "discriminator.passed" in result.missing


def test_static_gate_tier_maps_to_static_gate_passed() -> None:
    task = _task(tiers=["static_gate"])
    result = evaluate_stop_gates(task, has_success_event=_no)
    assert "static_gate.passed" in result.missing


def test_unknown_tier_is_silently_ignored() -> None:
    """Tier names not in the map don't crash and don't get added to
    missing — kernel discriminator handles unknowns."""
    task = _task(tiers=["mysterious_tier_X"])
    result = evaluate_stop_gates(task, has_success_event=_no)
    assert result.blocked is False
    assert "mysterious_tier_X" not in result.missing


# ---------------------------------------------------------------------------
# Multi-tier scenarios
# ---------------------------------------------------------------------------


def test_all_gates_satisfied_allows_stop() -> None:
    task = _task(tiers=["review", "test", "judge"])
    result = evaluate_stop_gates(task, has_success_event=_yes)
    assert result.blocked is False
    assert result.missing == []
    assert set(result.required) == {
        "review.approved", "test.passed", "judge.passed",
    }


def test_partial_satisfaction_lists_only_missing() -> None:
    """Mixed: test.passed exists, review.approved doesn't.
    Missing should include only review.approved."""
    task = _task(tiers=["review", "test"])

    def _selective(task_id: str, event_type: str) -> bool:
        return event_type == "test.passed"

    result = evaluate_stop_gates(task, has_success_event=_selective)
    assert result.blocked is True
    assert result.missing == ["review.approved"]
    assert set(result.required) == {"review.approved", "test.passed"}


def test_case_insensitive_tier_normalization() -> None:
    task = _task(tiers=["REVIEW", "  Test  "])
    result = evaluate_stop_gates(task, has_success_event=_no)
    assert "review.approved" in result.missing
    assert "test.passed" in result.missing


# ---------------------------------------------------------------------------
# Advice formatting
# ---------------------------------------------------------------------------


def test_advice_empty_when_no_missing() -> None:
    task = _task(tiers=["review"])
    result = evaluate_stop_gates(task, has_success_event=_yes)
    assert result.advice == ""


def test_advice_lists_task_and_missing_gates() -> None:
    task = _task(tiers=["review", "test"])
    result = evaluate_stop_gates(task, has_success_event=_no)
    assert "TASK-1" in result.advice
    assert "review.approved" in result.advice
    assert "test.passed" in result.advice
    assert "STOP-GUARD" in result.advice


def test_advice_suggests_emit_or_suspend() -> None:
    task = _task(tiers=["review"])
    result = evaluate_stop_gates(task, has_success_event=_no)
    assert "completion event" in result.advice
    assert "suspended" in result.advice


# ---------------------------------------------------------------------------
# Result is frozen
# ---------------------------------------------------------------------------


def test_stop_guard_result_is_frozen() -> None:
    task = _task(tiers=["review"])
    result = evaluate_stop_gates(task, has_success_event=_no)
    with pytest.raises((AttributeError, TypeError)):
        result.missing = []  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Wire-up grep proof — _evaluate_stop_guard exists in hook_recv and
# is invoked for `provider.stop.check` events.
# ---------------------------------------------------------------------------


def test_hook_recv_has_evaluate_stop_guard() -> None:
    from zf.cli import hook_recv

    assert hasattr(hook_recv, "_evaluate_stop_guard")


def test_hook_recv_run_dispatches_to_stop_guard_for_provider_stop_check() -> None:
    """Source-level grep proof that hook_recv.run branches on
    provider.stop.check and invokes _evaluate_stop_guard."""
    import inspect

    from zf.cli import hook_recv

    source = inspect.getsource(hook_recv.run)
    assert 'args.event == "provider.stop.check"' in source
    assert "_evaluate_stop_guard" in source


def test_hook_recv_stop_guard_uses_real_event_log_query_filters(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="demo",
        status="in_progress",
        assigned_to="dev",
        active_dispatch_id="disp-1",
        contract=TaskContract(
            behavior="do the thing",
            verification_tiers=["review", "test"],
        ),
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="review.approved",
        actor="review",
        task_id="TASK-1",
    ))
    log.append(ZfEvent(
        type="test.passed",
        actor="test",
        task_id="TASK-1",
    ))

    assert hook_recv._evaluate_stop_guard(state_dir, log, "dev") == 0
