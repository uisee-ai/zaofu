"""Contract tests for pytest-asyncio ordinary evidence integration."""

import os
from pathlib import Path

import pytest

from tests.testkit.runner import run_pytest


ROOT = Path(__file__).resolve().parents[2]
NODE = "tests/testkit/fixtures/async_evidence_fixture.py::test_async_case"


@pytest.mark.parametrize("mode", ["pass", "call-fail", "teardown-fail", "call-cancel"])
def test_async_phase_facts_and_evidence_are_fail_closed(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("ZF_ASYNC_CASE", mode)
    result = run_pytest(project_root=ROOT, stage="async-evidence", output_dir=tmp_path / "out",
                        nodeids=[NODE], timeout_seconds=30)
    [case] = result.cases
    assert result.session_finished and not result.timed_out
    assert not result.collection_errors and not result.evidence_errors
    if mode == "pass":
        assert result.verdict == "pass"
        assert case["ordinary_evidence"]["retention"] == "discarded_after_all_phases_passed"
    else:
        assert result.verdict == "fail"
        assert case["status"] in {"failed", "error"}
        assert case["ordinary_evidence"]["snapshots"]
        assert "async" in str(case["phases"])


def test_async_setup_failure_is_not_invented_as_a_call_case(tmp_path, monkeypatch):
    monkeypatch.setenv("ZF_ASYNC_CASE", "setup-fail")
    result = run_pytest(project_root=ROOT, stage="async-evidence", output_dir=tmp_path / "out",
                        nodeids=[NODE], timeout_seconds=30)
    [case] = result.cases
    assert result.verdict == "fail" and case["status"] == "error"
    assert "async setup failure" in case["phases"]["setup"]["longrepr"]
