"""U22 驳回有效性原语:被审 candidate 落后于最新交付时,驳回无效。

场景复刻 r6.1 续跑第 12 轮:dev 02:52:03 交付 c24f1c6a,集成 02:52:11
用旧 ref(a8871f57),review 02:55 拒收——判的是旧内容,驳回应判无效。
"""
from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.rejection_validity import rejection_effective

_T = "AVBS-METRICS-ASSEMBLY-001"


def _ev(etype: str, eid: str, *, actor: str = "zf-cli", task_id: str | None = _T, **payload) -> ZfEvent:
    return ZfEvent(type=etype, id=eid, actor=actor, task_id=task_id, payload=payload)


def test_round12_replica_candidate_behind_is_ineffective() -> None:
    events = [
        _ev("dev.build.done", "e1", actor="dev-metrics", source_commit="a8871f57"),
        _ev("candidate.task_ref.applied", "e2", source_commit="a8871f57"),
        _ev("dev.build.done", "e3", actor="dev-metrics", source_commit="c24f1c6a"),
        _ev("candidate.task_ref.applied", "e4", source_commit="a8871f57"),  # 慢一拍集成
        _ev("review.rejected", "e5"),
    ]
    v = rejection_effective(events, task_id=_T, rejection_event_id="e5")
    assert v.effective is False
    assert v.reason == "candidate_behind_latest_completion"
    assert v.latest_completion_commit == "c24f1c6a"
    assert v.integrated_commit == "a8871f57"


def test_current_candidate_rejection_is_effective() -> None:
    events = [
        _ev("dev.build.done", "e1", actor="dev-metrics", source_commit="c24f1c6a"),
        _ev("candidate.task_ref.applied", "e2", source_commit="c24f1c6a"),
        _ev("review.rejected", "e3"),
    ]
    v = rejection_effective(events, task_id=_T, rejection_event_id="e3")
    assert v.effective is True
    assert v.reason == "candidate_current"


def test_kernel_echo_completion_does_not_count_as_latest() -> None:
    events = [
        _ev("dev.build.done", "e1", actor="dev-metrics", source_commit="aaa111"),
        _ev("candidate.task_ref.applied", "e2", source_commit="aaa111"),
        _ev("dev.build.done", "e3", actor="zf-cli", source_commit="stale00"),  # 回声
        _ev("review.rejected", "e4"),
    ]
    v = rejection_effective(events, task_id=_T, rejection_event_id="e4")
    assert v.effective is True


def test_missing_records_conservatively_effective() -> None:
    v = rejection_effective(
        [_ev("review.rejected", "e1")], task_id=_T, rejection_event_id="e1",
    )
    assert v.effective is True
    assert v.reason == "insufficient_records_conservative_effective"


def test_events_after_rejection_do_not_retroactively_invalidate() -> None:
    events = [
        _ev("dev.build.done", "e1", actor="dev-metrics", source_commit="aaa111"),
        _ev("candidate.task_ref.applied", "e2", source_commit="aaa111"),
        _ev("review.rejected", "e3"),
        _ev("dev.build.done", "e4", actor="dev-metrics", source_commit="bbb222"),
    ]
    v = rejection_effective(events, task_id=_T, rejection_event_id="e3")
    assert v.effective is True


def test_short_and_full_commit_prefix_match() -> None:
    events = [
        _ev("dev.build.done", "e1", actor="dev-metrics",
            source_commit="c24f1c6a835f09d5f5ce9f4e2873a9df26a74f12"),
        _ev("candidate.task_ref.applied", "e2", source_commit="c24f1c6a"),
        _ev("review.rejected", "e3"),
    ]
    v = rejection_effective(events, task_id=_T, rejection_event_id="e3")
    assert v.effective is True
