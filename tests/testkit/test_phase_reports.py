"""Real abrupt-exit probes for durable ordinary pytest phase facts."""

import json
from pathlib import Path
import signal
import sys

import pytest

from tests.testkit.evidence import sha256_file
from tests.testkit.runner import run_pytest


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = "tests/testkit/fixtures/phase_exit_fixture.py"


@pytest.mark.parametrize(("phase", "mode"), [
    ("setup", "exit"), ("call", "exit"), ("teardown", "exit"),
    pytest.param("call", "SIGTERM", marks=pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal")),
    pytest.param("call", "SIGKILL", marks=pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal")),
])
def test_abrupt_exit_retains_prior_phase_facts_without_claiming_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str, mode: str,
) -> None:
    monkeypatch.setenv("ZF_TESTKIT_FIXTURE_EXIT_PHASE", phase)
    monkeypatch.setenv("ZF_TESTKIT_FIXTURE_EXIT_MODE", mode)
    result = run_pytest(
        project_root=ROOT, stage="phase-report-contract", output_dir=tmp_path / "out",
        nodeids=[FIXTURE], timeout_seconds=30,
    )
    assert result.verdict == "harness_failure"
    assert result.exit_code == (42 if mode == "exit" else -getattr(signal, mode))
    assert not result.session_finished and not result.timed_out
    assert not result.collection_errors and not result.failure_bundle_error
    cases = {case["nodeid"].split("::")[-1]: case for case in result.cases}
    assert cases["test_previous_pass"]["status"] == "passed"
    assert cases["test_previous_failure"]["status"] == "failed"
    assert "original ordinary failure" in cases["test_previous_failure"]["phases"]["call"]["longrepr"]
    interrupted = cases["test_interrupted"]
    assert interrupted["status"] == "not_run"
    assert set(interrupted["phases"]) == {
        "setup": set(), "call": {"setup"}, "teardown": {"setup", "call"},
    }[phase]
    assert cases["test_never_reached"]["status"] == "not_run"
    assert cases["test_never_reached"]["phases"] == {}
    report = json.loads(Path(result.report_path).read_text())
    assert report["session_finished"] is False and report["session_exitstatus"] is None
    bundle = Path(result.failure_bundle_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    [entry] = [row for row in manifest["evidence"] if row["source"] == result.report_path]
    assert sha256_file(bundle / entry["ref"]) == entry["digest"] == sha256_file(Path(result.report_path))
    assert json.loads((bundle / entry["ref"]).read_text()) == report
