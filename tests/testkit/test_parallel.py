"""Contracts for worker report identity and fail-closed merging."""

import subprocess
import sys

import pytest

from tests.testkit.parallel import merge_worker_reports
from tests.testkit.runner import run_pytest
from tests.testkit.pytest_plugin import _final_case_status
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _report(worker: str, nodeid: str = "test_case"):
    return {"session_finished": True, "testrun_uid": "run-1", "worker_id": worker,
            "results": {nodeid: {"nodeid": nodeid, "status": "passed", "phases": {}}}}


def test_merge_keeps_worker_identity_and_all_cases():
    merged = merge_worker_reports([_report("gw0", "test_case_0"), _report("gw1", "test_case_1")])
    assert set(merged["results"]) == {"test_case_0", "test_case_1"}
    assert {row["worker_id"] for row in merged["results"].values()} == {"gw0", "gw1"}
    assert merged["session_finished"]


@pytest.mark.parametrize("reports", [
    [_report("gw0"), _report("gw0", "other")],
    [{"session_finished": True, "results": {}}],
    [_report("gw0") | {"session_finished": False}],
])
def test_merge_rejects_missing_or_duplicate_worker_identity(reports):
    with pytest.raises(ValueError):
        merge_worker_reports(reports)


def test_runner_merges_real_xdist_worker_reports(tmp_path):
    source = "tests/conformance/test_zaofu_testkit_s0.py"
    collection = subprocess.check_output(
        [sys.executable, "-m", "pytest", "-o", "addopts=", "--collect-only", "-q", source],
        cwd=ROOT, text=True, timeout=30,
    )
    expected = {line for line in collection.splitlines() if line.startswith(source + "::")}
    assert expected, collection
    result = run_pytest(
        project_root=ROOT, stage="xdist-contract", output_dir=tmp_path / "out",
        nodeids=[source],
        timeout_seconds=120, xdist_workers=2,
    )
    assert result.verdict == "pass"
    assert result.session_finished and len(result.cases) == len(expected)
    assert {case["nodeid"] for case in result.cases} == expected
    assert {case["worker_id"] for case in result.cases} <= {"gw0", "gw1"}


def test_runner_relocates_ordinary_snapshots_from_xdist_workers(tmp_path):
    result = run_pytest(
        project_root=ROOT, stage="xdist-ordinary", output_dir=tmp_path / "out",
        nodeids=["tests/conformance/test_zaofu_testkit_s3.py"],
        timeout_seconds=120, xdist_workers=2,
    )
    assert result.verdict == "pass"
    assert len(result.cases) == 7
    assert all(case["ordinary_evidence"]["registered"] for case in result.cases)
    assert not result.evidence_errors


def test_rerun_recovery_is_flaky_not_green():
    result = {
        "attempts": {
            "0": {"setup": {"outcome": "passed"}, "call": {"outcome": "failed"},
                  "teardown": {"outcome": "passed"}},
            "1": {"setup": {"outcome": "passed"}, "call": {"outcome": "passed"},
                  "teardown": {"outcome": "passed"}},
        }
    }
    assert _final_case_status(result) == "flaky_pass"
