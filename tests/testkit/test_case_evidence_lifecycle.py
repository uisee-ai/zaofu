"""Exercise pytest hooks, actual process exits and durable bundle copies."""

import base64
import json
from pathlib import Path
import os
import subprocess
import sys
import time

import pytest

from tests.testkit.evidence import sha256_file
from tests.testkit.runner import run_pytest
from tests.testkit.cleanup import terminate_process_group


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = "tests/testkit/fixtures/ordinary_evidence_fixture.py::test_lifecycle"


@pytest.mark.parametrize("mode,point", [
    ("call", "call-report"), ("setup", "setup-report"),
    ("teardown", "pre-teardown"), ("xfail", "call-report"),
    ("owner", "owner-guard"), ("exit", "checkpoint"),
    ("SIGTERM", "checkpoint"), ("SIGKILL", "checkpoint"),
])
def test_failure_bundle_retains_registered_bytes(tmp_path, monkeypatch, mode, point):
    monkeypatch.setenv("ZF_CASE_MODE", mode)
    result = run_pytest(project_root=ROOT, stage="ordinary-evidence", output_dir=tmp_path / "out",
                        nodeids=[FIXTURE], timeout_seconds=30)
    assert result.verdict == ("harness_failure" if mode in {"exit", "SIGTERM", "SIGKILL"} else "fail")
    [case] = result.cases
    snapshot = case["ordinary_evidence"]["snapshots"][point]
    bundle = Path(result.failure_bundle_path)
    manifest = json.loads((bundle / "manifest.json").read_text())
    [entry] = [row for row in manifest["evidence"] if row["source"] == snapshot["path"]]
    assert sha256_file(bundle / entry["ref"]) == entry["digest"] == snapshot["digest"]
    payload = json.loads((bundle / entry["ref"]).read_text())
    assert payload["complete_for_declared_scope"]
    assert payload["capture_point"] == point
    assert payload["nodeid"] == case["nodeid"]
    [raw] = payload["files"]
    assert base64.b64decode(raw["base64"], validate=True) == (b"initial\xff" if mode == "setup" else b"call-state\x00\xff")
    if mode not in {"setup", "exit", "SIGTERM", "SIGKILL"}:
        assert not Path(payload["original_root"]).exists()
    assert not result.failure_bundle_error
    assert not result.evidence_errors


@pytest.mark.parametrize("mode", ["multi-finalizer", "cleanup-change", "unguarded-deletion"])
def test_cleanup_order_preserves_each_error_and_snapshot_time(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("ZF_CASE_MODE", mode)
    result = run_pytest(project_root=ROOT, output_dir=tmp_path / "out", nodeids=[FIXTURE], timeout_seconds=30)
    [case] = result.cases
    snapshots = case["ordinary_evidence"]["snapshots"]
    before = json.loads(Path(snapshots["pre-teardown"]["path"]).read_text())
    assert not Path(before["original_root"]).exists()
    if mode == "unguarded-deletion":
        assert result.verdict == "harness_failure"
        assert "original failure before unguarded deletion" in case["phases"]["call"]["longrepr"]
        assert not before["complete_for_declared_scope"] and before["files"] == []
        assert case["ordinary_evidence"]["errors"]
        assert "owner-guard" not in snapshots
    else:
        assert result.verdict == "fail"
        assert base64.b64decode(before["files"][0]["base64"]) == b"call-state\x00\xff"
        error = case["phases"]["teardown"]["longrepr"]
        if mode == "multi-finalizer":
            assert "first-finalizer" in error and "second-finalizer" in error
        else:
            assert "cleanup changed state before failure" in error
            guarded = json.loads(Path(snapshots["owner-guard"]["path"]).read_text())
            assert base64.b64decode(guarded["files"][0]["base64"]) == b"cleanup-change\xff"
            assert guarded["captured_at_ns"] >= before["captured_at_ns"]


@pytest.mark.parametrize("mode,code", [("pytest-exit-zero", 0), ("pytest-exit-error", 17)])
def test_pytest_exit_cannot_turn_incomplete_case_green(tmp_path, monkeypatch, mode, code):
    monkeypatch.setenv("ZF_CASE_MODE", mode)
    result = run_pytest(project_root=ROOT, output_dir=tmp_path / "out", nodeids=[FIXTURE], timeout_seconds=30)
    assert result.exit_code == code and result.verdict != "pass"
    [case] = result.cases
    assert case["status"] == "not_run" and "call" not in case["phases"]
    assert "explicit ordinary exit" in Path(result.stdout_path).read_text()
    snapshot = case["ordinary_evidence"]["snapshots"]["checkpoint"]
    payload = json.loads(Path(snapshot["path"]).read_text())
    assert base64.b64decode(payload["files"][0]["base64"]) == b"call-state\x00\xff"
    assert not result.ordinary_diagnostics


@pytest.mark.parametrize("mode,phase,reason", [("skip", "call", "ordinary call skipped"), ("setup-skip", "setup", "ordinary setup disposition")])
def test_plain_skip_retains_actionable_reason(tmp_path, monkeypatch, mode, phase, reason):
    monkeypatch.setenv("ZF_CASE_MODE", mode)
    result = run_pytest(project_root=ROOT, output_dir=tmp_path / "out", nodeids=[FIXTURE], timeout_seconds=30)
    assert result.verdict == "fail"
    assert reason in result.cases[0]["phases"][phase]["longrepr"]


def test_success_discards_only_its_own_candidates(tmp_path, monkeypatch):
    monkeypatch.setenv("ZF_CASE_MODE", "pass")
    result = run_pytest(project_root=ROOT, output_dir=tmp_path / "out", nodeids=[FIXTURE], timeout_seconds=30)
    assert result.verdict == "pass"
    [case] = result.cases
    assert case["ordinary_evidence"]["retention"] == "discarded_after_all_phases_passed"
    assert not list((tmp_path / "out" / "case-evidence").rglob("*.json"))


@pytest.mark.parametrize("mode,phase,outcome", [
    ("setup-xfail", "setup", "skipped"), ("teardown-xfail", "teardown", "skipped"),
    ("setup-skip", "setup", "skipped"), ("skip", "call", "skipped"),
    ("strict-xpass", "call", "failed"), ("xpass", "call", "passed"),
    ("no-run-xfail", "setup", "skipped"), ("both", "teardown", "failed"),
])
def test_outcome_conversion_keeps_phase_facts_and_never_goes_green(tmp_path, monkeypatch, mode, phase, outcome):
    monkeypatch.setenv("ZF_CASE_MODE", mode)
    result = run_pytest(project_root=ROOT, output_dir=tmp_path / "out", nodeids=[FIXTURE], timeout_seconds=30)
    assert result.verdict == "fail"
    [case] = result.cases
    assert case["phases"][phase]["outcome"] == outcome
    if mode == "both":
        assert "ordinary call failure" in case["phases"]["call"]["longrepr"]
        assert "ordinary teardown failure" in case["phases"]["teardown"]["longrepr"]
    if mode == "no-run-xfail":
        assert "ordinary_evidence" not in case  # Fixture never ran; no invented state.
    else:
        assert case["ordinary_evidence"]["snapshots"]
    assert not result.evidence_errors and not result.failure_bundle_error


def test_parent_rejects_tampered_snapshot_without_losing_original_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("ZF_CASE_MODE", "tamper")
    result = run_pytest(project_root=ROOT, output_dir=tmp_path / "out", nodeids=[FIXTURE], timeout_seconds=30)
    assert result.verdict == "harness_failure"
    assert "snapshot digest mismatch" in str(result.evidence_errors)
    assert "ordinary call failure" in result.cases[0]["phases"]["call"]["longrepr"]


def test_timeout_recovers_only_last_committed_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("ZF_CASE_MODE", "timeout")
    marker = tmp_path / "ready"
    monkeypatch.setenv("ZF_CASE_READY", str(marker))
    real_popen = subprocess.Popen
    processes = []

    def synchronized_popen(command, *args, **kwargs):
        process = real_popen(command, *args, **kwargs)
        if list(command[:3]) == [sys.executable, "-m", "pytest"]:
            try:
                deadline = time.monotonic() + 30
                while not marker.exists() and time.monotonic() < deadline:
                    assert process.poll() is None
                    time.sleep(0.01)
                assert marker.exists(), "checkpoint readiness not observed"
                processes.append(process)
            except BaseException:
                terminate_process_group(process, timeout=1)
                raise
        return process

    monkeypatch.setattr(subprocess, "Popen", synchronized_popen)
    result = run_pytest(project_root=ROOT, output_dir=tmp_path / "out", nodeids=[FIXTURE], timeout_seconds=0.1)
    assert result.timed_out and not result.session_finished and result.verdict == "harness_failure"
    assert len(processes) == 1 and processes[0].poll() is not None
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)
    [case] = result.cases
    assert "checkpoint" in case["ordinary_evidence"]["snapshots"]
    assert "call" not in case["phases"]
    assert not result.evidence_errors
