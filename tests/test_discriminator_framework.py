"""Tests for G-DISC-1: BaseDiscriminator + DiscriminatorRunner abstraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.task.schema import Task
from zf.core.verification.discriminator import (
    BaseDiscriminator,
    DiscriminatorReport,
    DiscriminatorResult,
    DiscriminatorRunner,
)


@pytest.fixture
def event_log(tmp_path: Path) -> EventLog:
    return EventLog(tmp_path / "events.jsonl")


def _task() -> Task:
    return Task(id="T1", title="x", status="testing")


class _PassingD(BaseDiscriminator):
    name = "PassD"

    def evaluate(self, task, workspace, event_log):
        return DiscriminatorResult(
            d_name=self.name, passed=True,
            evidence={"checked": True}, reason="",
        )


class _FailingD(BaseDiscriminator):
    name = "FailD"

    def evaluate(self, task, workspace, event_log):
        return DiscriminatorResult(
            d_name=self.name, passed=False,
            evidence={"why": "deliberately failing"},
            reason="this D always fails",
        )


class _ExplodingD(BaseDiscriminator):
    name = "BoomD"

    def evaluate(self, task, workspace, event_log):
        raise RuntimeError("boom")


class TestBaseDiscriminator:
    def test_base_is_abstract(self):
        with pytest.raises(TypeError):
            BaseDiscriminator()  # type: ignore[abstract]


class TestResult:
    def test_result_dataclass_fields(self):
        r = DiscriminatorResult(
            d_name="X", passed=True, evidence={"k": 1}, reason="",
        )
        assert r.d_name == "X"
        assert r.passed is True
        assert r.evidence == {"k": 1}


class TestRunner:
    def test_runner_runs_all_discriminators(self, tmp_path, event_log):
        runner = DiscriminatorRunner([_PassingD(), _PassingD()])
        report = runner.run(_task(), tmp_path, event_log)
        assert len(report.d_results) == 2

    def test_runner_returns_passed_when_all_pass(self, tmp_path, event_log):
        runner = DiscriminatorRunner([_PassingD(), _PassingD()])
        report = runner.run(_task(), tmp_path, event_log)
        assert report.passed is True

    def test_runner_returns_failed_when_any_fails(self, tmp_path, event_log):
        runner = DiscriminatorRunner([_PassingD(), _FailingD()])
        report = runner.run(_task(), tmp_path, event_log)
        assert report.passed is False

    def test_runner_collects_per_d_results(self, tmp_path, event_log):
        runner = DiscriminatorRunner([_PassingD(), _FailingD()])
        report = runner.run(_task(), tmp_path, event_log)
        names = [r.d_name for r in report.d_results]
        assert names == ["PassD", "FailD"]
        assert report.d_results[0].passed is True
        assert report.d_results[1].passed is False

    def test_runner_handles_d_exception_as_failed(self, tmp_path, event_log):
        runner = DiscriminatorRunner([_PassingD(), _ExplodingD()])
        report = runner.run(_task(), tmp_path, event_log)
        assert report.passed is False
        boom_result = next(r for r in report.d_results if r.d_name == "BoomD")
        assert boom_result.passed is False
        assert "boom" in boom_result.reason.lower()

    def test_runner_empty_discriminator_list(self, tmp_path, event_log):
        """Vacuous truth: zero D = passed (nothing to check)."""
        runner = DiscriminatorRunner([])
        report = runner.run(_task(), tmp_path, event_log)
        assert report.passed is True
        assert report.d_results == []


class TestReport:
    def test_report_dataclass(self):
        r = DiscriminatorReport(
            passed=True,
            d_results=[
                DiscriminatorResult(d_name="A", passed=True, evidence={}, reason=""),
            ],
        )
        assert r.passed is True
        assert len(r.d_results) == 1
