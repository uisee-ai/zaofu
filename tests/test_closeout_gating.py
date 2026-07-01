"""X16:closeout 门化(decision evidence 分级)+ check lane。"""

from __future__ import annotations

from pathlib import Path

from zf.runtime.check_preflight import run_check_preflight
from zf.runtime.closeout_learning import (
    closeout_events_for_done,
    extract_closeout_decision,
)


class TestCloseoutDecision:
    def test_valid_learning_recorded(self):
        events = closeout_events_for_done(
            task_id="T1", terminal_event_id="e1",
            payload={"learning": {"decision": "backlog_candidate",
                                  "reason": "发现 provider 陷阱"}},
            harness_profile="baseline",
        )
        assert events[0]["type"] == "closeout.learning.recorded"
        assert events[0]["payload"]["decision"] == "backlog_candidate"

    def test_strict_gap_observe_first(self):
        events = closeout_events_for_done(
            task_id="T1", terminal_event_id="e1",
            payload={}, harness_profile="strict",
        )
        assert events[0]["type"] == "closeout.learning.gap"
        assert events[0]["payload"]["severity"] == "STOP"
        assert "不回滚" in events[0]["payload"]["note"]

    def test_baseline_missing_is_quiet(self):
        assert closeout_events_for_done(
            task_id="T1", terminal_event_id="e1",
            payload={}, harness_profile="baseline",
        ) == []

    def test_bogus_decision_rejected(self):
        assert extract_closeout_decision(
            {"learning": {"decision": "yolo"}}) is None

    def test_no_runtime_truth_mutation(self):
        # 纯函数:只产事件 spec,不触 TaskStore/docs(rg 级证明)
        src = Path("src/zf/runtime/closeout_learning.py").read_text()
        assert "task_store" not in src and "TaskStore" not in src


class TestCheckLane:
    def test_evidence_shape_and_pass_fail(self, tmp_path):
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=tmp_path)
        out = run_check_preflight(
            workdir=tmp_path, checks=["true", "false"],
        )
        assert out["schema_version"] == "check-preflight.v1"
        assert out["passed"] is False
        assert [c["ok"] for c in out["checks"]] == [True, False]
        assert "not this lane" in out["terminal_authority"]

    def test_lane_never_emits_terminal(self):
        src = Path("src/zf/runtime/check_preflight.py").read_text()
        # 'emit' 一词出现在边界声明文档串里属合法;禁的是真实发射面。
        for forbidden in ("judge.passed", "review.approved", "test.passed",
                          "event_writer", "ZfEvent", "EventWriter"):
            assert forbidden not in src, forbidden
