"""Additional S1 lifecycle, failpoint, snapshot and report tests."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from tests.testkit.evidence import sha256_file
from tests.testkit.failpoints import COMMIT_POINTS, FailpointController, InjectedFailure
from tests.testkit.cleanup import (
    assert_no_child_process_leaks,
    child_processes,
    terminate_process,
)
from tests.testkit.processes import start_process, wait_for_exit
from tests.testkit.oracles import Verdict
from tests.testkit.report import (
    TestResult,
    summarize_results,
    write_evidence_index,
    write_test_result,
)
from tests.testkit.failure_bundle import write_failure_bundle
from tests.testkit.scenarios import isolated_project
from tests.testkit.snapshots import (
    assert_snapshot_equal,
    persist_snapshot,
    truth_snapshot,
)


def test_failpoint_injects_once_and_records_hits() -> None:
    controller = FailpointController(fail_at="after_append")
    with pytest.raises(InjectedFailure, match="after_append"):
        controller.hit("after_append")
    controller.hit("after_append")
    assert controller.hits == ["after_append", "after_append"]
    assert controller.was_hit("after_append")


def test_failpoint_is_disabled_by_default() -> None:
    """A failpoint must never inject a crash unless a test opts in explicitly."""
    controller = FailpointController()
    controller.hit("after_append")
    assert controller.hits == ["after_append"]
    assert controller.was_hit("after_append")


@pytest.mark.parametrize("point", COMMIT_POINTS)
def test_named_commit_point_is_deterministically_injectable(point: str) -> None:
    controller = FailpointController(fail_at=point)
    with pytest.raises(InjectedFailure, match=point):
        controller.hit(point)
    assert controller.hits == [point]


def test_process_runner_can_start_and_wait(tmp_path: Path) -> None:
    managed = start_process(
        [sys.executable, "-c", "print('worker-ready')"],
        cwd=tmp_path,
    )
    try:
        assert wait_for_exit(managed, timeout=2) == 0
    finally:
        managed.stop()


def test_terminate_process_escalates_when_sigterm_is_ignored(tmp_path: Path) -> None:
    """Cleanup must not leave a stubborn child after the graceful timeout."""
    baseline = child_processes()
    ready = tmp_path / "sigterm-handler-ready"
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import pathlib, signal, time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                f"pathlib.Path({str(ready)!r}).touch(); time.sleep(30)"
            ),
        ],
    )
    try:
        deadline = time.monotonic() + 2
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "child did not install SIGTERM handler"
        terminate_process(process, timeout=0.05)
        assert process.poll() is not None
        assert process.returncode == -9
        assert_no_child_process_leaks(baseline)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups only")
def test_process_runner_stop_terminates_descendants(tmp_path: Path) -> None:
    marker = tmp_path / "child.pid"
    script = (
        "import pathlib, subprocess, sys, time; "
        f"p=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        f"pathlib.Path({str(marker)!r}).write_text(str(p.pid)); time.sleep(30)"
    )
    managed = start_process(
        [sys.executable, "-c", script],
        cwd=tmp_path,
    )
    try:
        deadline = time.monotonic() + 2
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        child_pid = int(marker.read_text(encoding="utf-8"))
        managed.stop(timeout=2)
        assert managed.returncode is not None
        child_stat = Path("/proc") / str(child_pid) / "stat"
        deadline = time.monotonic() + 2
        while child_stat.exists() and time.monotonic() < deadline:
            fields = child_stat.read_text(encoding="utf-8").split()
            if len(fields) > 2 and fields[2] == "Z":
                break
            time.sleep(0.01)
        assert not child_stat.exists() or child_stat.read_text(encoding="utf-8").split()[2] == "Z"
    finally:
        managed.stop()


def test_snapshot_replay_is_equal_and_persisted(tmp_path: Path) -> None:
    project = isolated_project(tmp_path)
    project.emit("task.created")
    before = truth_snapshot(project.event_log(), state={"status": "ready"})
    after = truth_snapshot(project.event_log(), state={"status": "ready"})
    assert_snapshot_equal(before, after)
    digest = persist_snapshot(tmp_path / "snapshot.json", before)
    assert len(digest) == 64


def test_report_keeps_commit_and_evidence(tmp_path: Path) -> None:
    result = TestResult(
        case_id="s1-report",
        verdict=Verdict.PASS,
        commit="a" * 40,
        command_id="unit",
        acceptance_ids=("AC-1",),
        evidence=[{"ref": "stdout.log", "digest": "b" * 64}],
    )
    result_digest = write_test_result(tmp_path / "result.json", result)
    index_digest = write_evidence_index(
        tmp_path / "index.json",
        run_id="RUN-1",
        commit="a" * 40,
        results=[result],
    )
    assert len(result_digest) == 64
    assert len(index_digest) == 64
    assert summarize_results([result]) is Verdict.PASS


def test_failure_bundle_copies_evidence_with_digest_and_metadata(tmp_path: Path) -> None:
    """Failure diagnostics are portable without mutating canonical sources."""
    source = tmp_path / "pytest.stdout.log"
    source.write_text("case failed\n", encoding="utf-8")
    original_digest = sha256_file(source)

    bundle = tmp_path / "failure-bundle"
    digest = write_failure_bundle(
        bundle,
        metadata={"case_id": "S1-BUNDLE", "commit": "a" * 40},
        evidence_paths=(source,),
    )

    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "zf-testkit-failure-bundle.v1"
    assert manifest["metadata"]["case_id"] == "S1-BUNDLE"
    entry = manifest["evidence"][0]
    copied = bundle / entry["ref"]
    assert copied.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert entry["digest"] == original_digest == sha256_file(copied)
    assert entry["bytes"] == source.stat().st_size
    assert digest == sha256_file(bundle / "manifest.json")
    assert sha256_file(source) == original_digest


def test_failure_bundle_rejects_reuse_and_symlink_sources(tmp_path: Path) -> None:
    source = tmp_path / "stderr.log"
    source.write_text("failure\n", encoding="utf-8")
    bundle = tmp_path / "bundle"
    write_failure_bundle(bundle, metadata={"case_id": "S1"}, evidence_paths=(source,))

    with pytest.raises(FileExistsError, match="already exists"):
        write_failure_bundle(bundle, metadata={}, evidence_paths=(source,))

    outside = tmp_path / "outside.log"
    outside.write_text("outside\n", encoding="utf-8")
    link = tmp_path / "linked.log"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="regular file"):
        write_failure_bundle(tmp_path / "symlink-bundle", metadata={}, evidence_paths=(link,))
    assert not (tmp_path / "symlink-bundle").exists()


def test_failure_bundle_cleans_temporary_directory_after_copy_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first.log"
    second = tmp_path / "second.log"
    first.write_text("first\n", encoding="utf-8")
    second.write_text("second\n", encoding="utf-8")
    real_copyfile = shutil.copyfile
    calls = 0

    def fail_on_second(source: object, target: object):  # noqa: ANN001
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated evidence copy failure")
        return real_copyfile(source, target)

    monkeypatch.setattr("tests.testkit.failure_bundle.shutil.copyfile", fail_on_second)
    with pytest.raises(OSError, match="simulated evidence copy failure"):
        write_failure_bundle(
            tmp_path / "copy-error-bundle",
            metadata={"case_id": "S1-COPY-ERROR"},
            evidence_paths=(first, second),
        )

    assert not (tmp_path / "copy-error-bundle").exists()
    assert not list(tmp_path.glob(".copy-error-bundle.*"))


@pytest.mark.parametrize(
    ("verdicts", "expected"),
    [
        ([], Verdict.NOT_APPLICABLE),
        ([Verdict.NOT_APPLICABLE], Verdict.PASS),
        ([Verdict.PASS, Verdict.NOT_APPLICABLE], Verdict.PASS),
        ([Verdict.FAIL, Verdict.PASS], Verdict.FAIL),
        ([Verdict.FLAKY, Verdict.FAIL], Verdict.FLAKY),
        ([Verdict.HARNESS_FAILURE, Verdict.FLAKY, Verdict.FAIL], Verdict.HARNESS_FAILURE),
    ],
)
def test_report_summary_preserves_verdict_precedence(
    verdicts: list[Verdict], expected: Verdict
) -> None:
    results = [
        TestResult(case_id=f"case-{idx}", verdict=verdict, commit="a" * 40)
        for idx, verdict in enumerate(verdicts)
    ]
    assert summarize_results(results) is expected


def test_evidence_index_records_not_applicable_and_harness_failure(
    tmp_path: Path,
) -> None:
    results = [
        TestResult(
            case_id="optional-web",
            verdict=Verdict.NOT_APPLICABLE,
            commit="a" * 40,
            details={"reason": "project has no web surface"},
        ),
        TestResult(
            case_id="runner-health",
            verdict=Verdict.HARNESS_FAILURE,
            commit="a" * 40,
            details={"reason": "docker unavailable"},
        ),
    ]
    write_evidence_index(
        tmp_path / "index.json",
        run_id="RUN-STATUS",
        commit="a" * 40,
        results=results,
    )
    payload = json.loads((tmp_path / "index.json").read_text())
    assert payload["verdict"] == Verdict.HARNESS_FAILURE.value
    assert [item["verdict"] for item in payload["results"]] == [
        Verdict.NOT_APPLICABLE.value,
        Verdict.HARNESS_FAILURE.value,
    ]
    assert payload["results"][0]["details"]["reason"] == "project has no web surface"


def test_evidence_index_rejects_result_from_different_commit(tmp_path: Path) -> None:
    result = TestResult(
        case_id="stale-case",
        verdict=Verdict.PASS,
        commit="b" * 40,
    )
    with pytest.raises(ValueError, match="stale-case"):
        write_evidence_index(
            tmp_path / "index.json",
            run_id="RUN-STALE",
            commit="a" * 40,
            results=[result],
        )
    assert not (tmp_path / "index.json").exists()
