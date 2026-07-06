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
