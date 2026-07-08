"""RM 结果级熔断(avbs-r5:251 次 rework 每次 verify.passed 的教训)。"""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.run_manager import _outcome_no_progress_break

_T = "AVBS-SCENE-001"
_ACTION = {
    "task_id": _T,
    "safe_resume_action": "needs_rework_dispatch",
    "action": "workflow-task-resume",
    "checkpoint_id": "ck-1",
}


def _applied() -> ZfEvent:
    return ZfEvent(type="workflow.resume.applied", actor="run-manager", task_id=_T,
                   payload={"task_id": _T, "safe_resume_action": "needs_rework_dispatch"})


def test_under_three_applied_no_break() -> None:
    events = [_applied(), _applied()]
    assert _outcome_no_progress_break(events, _ACTION) == ""


def test_three_applied_zero_progress_breaks() -> None:
    events = [_applied(), _applied(), _applied()]
    reason = _outcome_no_progress_break(events, _ACTION)
    assert "3 times" in reason and _T in reason


def test_progress_inside_window_resets() -> None:
    events = [
        _applied(), _applied(),
        ZfEvent(type="verify.passed", task_id=_T, payload={"task_id": _T}),
        _applied(),
    ]
    assert _outcome_no_progress_break(events, _ACTION) == ""


def test_other_task_progress_does_not_reset() -> None:
    events = [
        _applied(), _applied(),
        ZfEvent(type="verify.passed", task_id="OTHER", payload={"task_id": "OTHER"}),
        _applied(),
    ]
    assert _outcome_no_progress_break(events, _ACTION) != ""


def _verify_failed(checkpoint_id: str) -> ZfEvent:
    return ZfEvent(
        type="run.manager.action.verify.failed", actor="run-manager", task_id=_T,
        payload={"task_id": _T, "action": "workflow-task-resume",
                 "checkpoint_id": checkpoint_id,
                 "reason": "expected downstream event not observed"},
    )


def test_verify_failed_under_cap_allows_retry() -> None:
    """FIX-5①(bizsim r4):verify.failed 2 次仍可重试。"""
    from zf.runtime.run_manager import _action_seen

    events = [_verify_failed("ck-1"), _verify_failed("ck-1")]
    assert _action_seen(events, {**_ACTION, "checkpoint_id": "ck-1"}) is False


def test_verify_failed_cap_stops_replanning() -> None:
    """FIX-5①:同 checkpoint 3 次 verify.failed → seen,终结无限重规划
    (r4 实锚:'checkpoint not found'/'downstream not observed' 逐 tick 循环)。"""
    from zf.runtime.run_manager import _action_seen

    events = [_verify_failed("ck-1") for _ in range(3)]
    assert _action_seen(events, {**_ACTION, "checkpoint_id": "ck-1"}) is True
    # 其他 checkpoint 不受影响
    assert _action_seen(events, {**_ACTION, "checkpoint_id": "ck-2"}) is False
