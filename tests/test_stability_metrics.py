"""P1-12:S1-S5 稳判据机械化(SYNTHESIS §6),r4 归档作 fail 侧实弹。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from zf.core.events.model import ZfEvent
from zf.runtime.stability_metrics import evaluate_stability


def _ev(etype: str, seconds_ago: float, **payload) -> ZfEvent:
    ts = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()
    return ZfEvent(type=etype, ts=ts, payload=payload or {})


def test_clean_run_is_stable() -> None:
    events = [
        _ev("task.dispatched", 3600), _ev("dev.build.done", 3000),
        _ev("review.approved", 2400), _ev("judge.passed", 600),
    ]
    report = evaluate_stability(events)
    assert report.passes is True
    assert report.s3_total_escalates == 0


def test_unacked_escalate_fails_s3() -> None:
    events = [_ev("human.escalate", 1200, reason="cap")]
    report = evaluate_stability(events)
    assert report.s3_pass is False and report.s3_unacked_escalates == 1


def test_acked_escalate_passes_s3() -> None:
    events = [
        _ev("human.escalate", 1200, reason="cap"),
        _ev("remediation.escalated_acked", 600),
    ]
    assert evaluate_stability(events).s3_pass is True


def test_blackout_fails_s5_and_new_class_fails_s1() -> None:
    baseline = [_ev("review.rejected", 9000)]
    events = [
        _ev("review.rejected", 3000),
        _ev("cost.usage.blackout", 1000),
    ]
    report = evaluate_stability(events, baseline_events=baseline)
    assert report.s5_pass is False
    assert report.s1_pass is False  # blackout 是基线没有的新 failure 类
    assert "cost_usage_blackout" in report.s1_new_failure_classes


def test_stall_recovery_p95() -> None:
    events = [
        ZfEvent(type="worker.stuck", actor="dev-1",
                ts=_ev("x", 1000).ts, payload={}),
        ZfEvent(type="worker.stuck.recovered", actor="dev-1",
                ts=_ev("x", 940).ts, payload={}),
    ]
    report = evaluate_stability(events)
    assert report.s4_samples == 1
    assert 55 <= report.s4_recovery_p95_s <= 65


def test_r4_archive_is_not_stable() -> None:
    # 实弹 fixture:r4 归档(21 escalate/风暴期)必须判 NOT STABLE
    from zf.core.events.log import EventLog
    from pathlib import Path

    archive = Path("/home/user/workspace/avbs-refactor/state-archive-avbs-r4-final/events.jsonl")
    if not archive.exists():
        import pytest
        pytest.skip("r4 archive not present on this host")
    report = evaluate_stability(EventLog(archive).read_all())
    assert report.passes is False
    assert report.s3_total_escalates >= 20
