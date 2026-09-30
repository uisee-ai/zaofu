"""Tests for the subprocess TestKit runner and fail-closed verdict."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from tests.testkit.evidence import sha256_file
from tests.testkit.cleanup import terminate_process_group
from tests.testkit.memray_guard import CaptureBudget
from tests.testkit.runner import run_pytest


def test_mainline_profile_selects_registered_deterministic_boundaries() -> None:
    from tests.testkit.suites import MAINLINE, SUITES

    registered = {path.split("::", 1)[0] for paths in SUITES.values() for path in paths}
    assert len(MAINLINE) == len(set(MAINLINE))
    assert all(node.split("::", 1)[0] in registered for node in MAINLINE)
    for boundary in ("plan_approval", "blocking_dispatch", "blocking_recovery",
                     "cli_lifecycle", "cli_full_preflight", "writer_batch_resume",
                     "claude_readiness", "test_ship.py", "hook_task_pipeline_workdir", "respawn_termination",
                     "respawn_finds_transcript_in_actual_spawn_cwd",
                     "s9_fixrun", "s9.py"):
        assert any(boundary in node for node in MAINLINE), boundary
    assert not any("native_claude" in node or "real_tmux" in node for node in MAINLINE)


@pytest.mark.parametrize("verdict,expected", [("pass", 0), ("fail", 1), ("harness_failure", 1)])
def test_mainline_cli_uses_existing_runner_and_preserves_verdict(
    tmp_path, monkeypatch, verdict, expected,
) -> None:
    from types import SimpleNamespace
    from tests.testkit import __main__ as cli
    from tests.testkit.suites import MAINLINE

    calls = []

    def run(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(verdict=verdict, to_dict=lambda: {"verdict": verdict})

    monkeypatch.setattr(cli, "run_pytest", run)
    monkeypatch.setattr(sys, "argv", ["testkit", "--profile", "mainline",
                                     "--output", str(tmp_path), "--timeout", "120"])
    assert cli.main() == expected
    assert len(calls) == 1
    assert calls[0]["nodeids"] == MAINLINE
    assert calls[0]["stage"] == "MAINLINE-DETERMINISTIC"
    assert calls[0]["timeout_seconds"] == 120
    assert calls[0]["output_dir"] == tmp_path
    assert calls[0]["bind_source"] is True
    assert calls[0]["source_profile"] == "acceptance"


@pytest.mark.parametrize("family", ["general", "issue", "prd", "refactor", "refactor-strong", "refactor-two-lane"])
def test_browser_live_cli_routes_one_explicit_case(tmp_path, monkeypatch, family):
    from types import SimpleNamespace
    from tests.testkit import __main__ as cli

    calls = []
    def run(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(verdict="fail", to_dict=lambda: {"verdict": "fail"})
    monkeypatch.setattr(cli, "run_pytest", run)
    monkeypatch.setattr(sys, "argv", ["testkit", "--profile", "browser-live",
                                     "--browser-case", family, "--output", str(tmp_path)])
    assert cli.main() == 1
    assert calls[0]["nodeids"] == (cli.BROWSER_NODES[family],)
    assert calls[0]["stage"] == "BROWSER-LIVE"
    assert calls[0]["bind_source"] is True
    assert calls[0]["source_profile"] == "browser"
    assert calls[0]["retain_case_artifacts"] is True


@pytest.mark.parametrize("args", [
    ["--profile", "browser-live"],
    ["--profile", "mainline", "--browser-case", "general"],
    ["--profile", "browser-live", "--browser-case", "general", "--manifest", "unexpected.json"],
])
def test_browser_cli_rejects_ambiguous_input_before_spawn(tmp_path, monkeypatch, args):
    from tests.testkit import __main__ as cli
    monkeypatch.setattr(cli, "run_pytest", lambda **kw: pytest.fail("spawned"))
    monkeypatch.setattr(sys, "argv", ["testkit", *args, "--output", str(tmp_path / "out")])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_retained_case_artifacts_live_under_report_directory(tmp_path):
    case = tmp_path / "test_artifact.py"
    case.write_text("def test_artifact(tmp_path):\n    (tmp_path / 'browser-gate.json').write_text('evidence')\n")
    out = tmp_path / "out"
    result = run_pytest(project_root=Path(__file__).resolve().parents[1],
                        output_dir=out, nodeids=(str(case),), retain_case_artifacts=True)
    assert result.verdict == "pass"
    files = list((out / "case-work").glob("test_artifact*/browser-gate.json"))
    assert files and all(path.read_text() == "evidence" for path in files)


def test_retention_never_erases_preexisting_case_work(tmp_path):
    out = tmp_path / "out"
    (out / "case-work").mkdir(parents=True)
    witness = out / "case-work/owned-by-user"
    witness.write_text("keep")
    with pytest.raises(FileExistsError, match="already exists"):
        run_pytest(project_root=Path(__file__).resolve().parents[1], output_dir=out,
                   nodeids=("tests/test_testkit_runner.py",), retain_case_artifacts=True)
    assert witness.read_text() == "keep"


def test_mainline_cli_rejects_mixed_stage_and_profile(tmp_path, monkeypatch) -> None:
    from tests.testkit import __main__ as cli

    monkeypatch.setattr(sys, "argv", ["testkit", "--stage", "S0", "--profile", "mainline",
                                     "--output", str(tmp_path)])
    with pytest.raises(SystemExit) as rejected:
        cli.main()
    assert rejected.value.code == 2


def _write_case(path: Path, source: str) -> Path:
    path.write_text(source, encoding="utf-8")
    return path


def _statuses(result) -> dict[str, str]:  # noqa: ANN001
    return {str(case["nodeid"]).split("::")[-1]: case["status"] for case in result.cases}


def test_runner_records_passed_cases_and_junit_evidence(tmp_path: Path) -> None:
    case = _write_case(
        tmp_path / "test_pass.py",
        "def test_pass():\n    assert 1 == 1\n",
    )

    result = run_pytest(
        project_root=Path(__file__).resolve().parents[1],
        stage="S1",
        output_dir=tmp_path / "out",
        nodeids=(str(case),),
        timeout_seconds=30,
    )

    assert result.verdict == "pass"
    assert result.exit_code == 0
    assert result.session_finished is True
    assert result.to_dict()["session_finished"] is True
    assert _statuses(result) == {"test_pass": "passed"}
    assert Path(result.report_path).exists()
    assert Path(result.junit_path).exists()
    assert Path(result.stdout_path).exists()
    assert Path(result.stderr_path).exists()


def test_runner_binds_config_and_dependency_lock_digests(tmp_path: Path) -> None:
    """Run evidence must identify the control-plane and dependency inputs."""
    case = _write_case(
        tmp_path / "test_pass.py",
        "def test_pass():\n    assert True\n",
    )
    root = Path(__file__).resolve().parents[1]

    result = run_pytest(
        project_root=root,
        stage="S1",
        output_dir=tmp_path / "out",
        nodeids=(str(case),),
        timeout_seconds=30,
    )

    assert result.config_path == str((root / "zf.yaml").resolve())
    assert result.config_digest == sha256_file(root / "zf.yaml")
    assert result.lock_path == str((root / "uv.lock").resolve())
    assert result.lock_digest == sha256_file(root / "uv.lock")
    receipt = json.loads(Path(tmp_path / "out" / "testkit-run.json").read_text())
    assert receipt["config_digest"] == result.config_digest
    assert receipt["lock_digest"] == result.lock_digest


def test_runner_classifies_pytest_startup_oserror_as_harness_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _write_case(
        tmp_path / "test_startup.py",
        "def test_pass():\n    assert True\n",
    )

    def fail_popen(*_args, **_kwargs):  # noqa: ANN001
        raise OSError("pytest executable unavailable")

    monkeypatch.setattr("tests.testkit.runner.subprocess.Popen", fail_popen)
    result = run_pytest(
        project_root=Path(__file__).resolve().parents[1],
        stage="S1",
        output_dir=tmp_path / "out",
        nodeids=(str(case),),
        timeout_seconds=30,
    )

    assert result.verdict == "harness_failure"
    assert result.exit_code is None
    assert result.session_finished is False
    assert result.to_dict()["session_finished"] is False
    assert result.startup_error == "OSError: pytest executable unavailable"
    assert result.to_dict()["startup_error"] == result.startup_error
    assert "pytest executable unavailable" in Path(result.stderr_path).read_text(
        encoding="utf-8"
    )
    bundle = Path(result.failure_bundle_path)
    assert bundle.is_dir()
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["metadata"]["startup_error"] == result.startup_error
    assert any(item["source"].endswith("pytest.stderr.log") for item in manifest["evidence"])


def test_runner_does_not_treat_xfail_or_xpass_as_green(tmp_path: Path) -> None:
    case = _write_case(
        tmp_path / "test_verdicts.py",
        """
import pytest

@pytest.mark.xfail(reason="known production issue")
def test_expected_failure():
    assert False

@pytest.mark.xfail(strict=True, reason="must remain failing")
def test_unexpected_pass():
    assert True
""",
    )

    result = run_pytest(
        project_root=Path(__file__).resolve().parents[1],
        output_dir=tmp_path / "out",
        nodeids=(str(case),),
        timeout_seconds=30,
    )

    assert result.verdict == "fail"
    assert result.exit_code != 0
    assert _statuses(result) == {
        "test_expected_failure": "xfailed",
        "test_unexpected_pass": "xpassed",
    }


def test_runner_preserves_xfail_failure_evidence(tmp_path: Path) -> None:
    """Known production failures remain actionable evidence, not just an X."""
    case = _write_case(
        tmp_path / "test_xfail_evidence.py",
        """
import pytest

@pytest.mark.xfail(strict=True, raises=RuntimeError, reason="ZF-TEST-999: detector")
def test_detector():
    raise RuntimeError("deterministic detector payload")
""",
    )

    result = run_pytest(
        project_root=Path(__file__).resolve().parents[1],
        output_dir=tmp_path / "out",
        nodeids=(str(case),),
        timeout_seconds=30,
    )

    assert result.verdict == "fail"
    record = next(item for item in result.cases if item["nodeid"].endswith("::test_detector"))
    assert record["status"] == "xfailed"
    assert "deterministic detector payload" in record["phases"]["call"]["longrepr"]
    bundle = Path(result.failure_bundle_path)
    assert bundle.is_dir()
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "zf-testkit-failure-bundle.v1"
    assert manifest["metadata"]["verdict_before_bundle"] == "fail"
    assert manifest["evidence"]
    for entry in manifest["evidence"]:
        copied = bundle / entry["ref"]
        assert copied.is_file()
        assert entry["digest"] == sha256_file(copied)


def test_runner_classifies_setup_error_as_non_product_pass(tmp_path: Path) -> None:
    case = _write_case(
        tmp_path / "test_setup_error.py",
        """
import pytest

@pytest.fixture
def broken_fixture():
    raise RuntimeError("fixture setup failed")

def test_never_runs(broken_fixture):
    assert False
""",
    )

    result = run_pytest(
        project_root=Path(__file__).resolve().parents[1],
        output_dir=tmp_path / "out",
        nodeids=(str(case),),
        timeout_seconds=30,
    )

    assert result.verdict == "fail"
    assert result.exit_code != 0
    assert _statuses(result) == {"test_never_runs": "error"}


def test_runner_classifies_ordinary_skip_as_non_pass(tmp_path: Path) -> None:
    case = _write_case(
        tmp_path / "test_skipped.py",
        "import pytest\n\ndef test_skipped():\n    pytest.skip('missing dependency')\n",
    )

    result = run_pytest(
        project_root=Path(__file__).resolve().parents[1],
        output_dir=tmp_path / "out",
        nodeids=(str(case),),
        timeout_seconds=30,
    )

    assert result.verdict == "fail"
    assert result.exit_code == 0
    assert _statuses(result) == {"test_skipped": "skipped"}


def test_runner_classifies_collection_error_as_non_pass(tmp_path: Path) -> None:
    case = _write_case(
        tmp_path / "test_collection_error.py",
        "def test_broken(:\n    pass\n",
    )

    result = run_pytest(
        project_root=Path(__file__).resolve().parents[1],
        output_dir=tmp_path / "out",
        nodeids=(str(case),),
        timeout_seconds=30,
    )

    assert result.verdict == "fail"
    assert result.exit_code != 0
    assert result.collection_errors
    assert result.cases == ()


def test_runner_timeout_is_harness_failure_and_terminates_process_group(
    tmp_path: Path,
) -> None:
    case = _write_case(
        tmp_path / "test_timeout.py",
        "import time\n\ndef test_sleeps():\n    time.sleep(5)\n",
    )

    result = run_pytest(
        project_root=Path(__file__).resolve().parents[1],
        output_dir=tmp_path / "out",
        nodeids=(str(case),),
        timeout_seconds=0.1,
    )

    assert result.verdict == "harness_failure"
    assert result.timed_out is True
    assert result.exit_code is not None


def test_runner_capture_guard_binds_policy_and_result(tmp_path: Path) -> None:
    capture = tmp_path / "capture" / "memray.bin"
    case = _write_case(
        tmp_path / "test_capture_guard.py",
        "from pathlib import Path\n"
        "def test_writes_capture():\n"
        f"    p=Path({str(capture)!r}); p.parent.mkdir(); p.write_bytes(b'capture')\n",
    )
    result = run_pytest(
        project_root=Path(__file__).resolve().parents[1],
        output_dir=tmp_path / "out",
        nodeids=(str(case),),
        timeout_seconds=10,
        capture_budget=CaptureBudget(capture=capture, min_free_bytes=1, poll_seconds=.01),
    )
    assert result.verdict == "pass"
    assert result.capture_guard["policy"]["ref"] == "capture-budget.json"
    assert result.capture_guard["result"]["ref"] == "capture-guard.json"
    guard = json.loads((tmp_path / "out/capture-guard.json").read_text())
    assert guard["violation"] is None
    assert guard["child_returncode"] == 0


@pytest.mark.parametrize("swallow_interrupt", [False, True])
def test_runner_capture_guard_is_fail_closed_on_overflow(tmp_path: Path, swallow_interrupt) -> None:
    capture = tmp_path / "capture" / "memray.bin"
    cleanup_marker = tmp_path / "finally.txt"
    case = _write_case(
        tmp_path / "test_capture_guard_overflow.py",
        "from pathlib import Path\nimport time\n"
        "def test_writes_capture():\n"
        f"    p=Path({str(capture)!r}); p.parent.mkdir()\n"
        "    try:\n"
        "        p.write_bytes(b'x'*4096); time.sleep(30)\n"
        + ("    except KeyboardInterrupt:\n        pass\n" if swallow_interrupt else "")
        + "    finally:\n"
        f"        Path({str(cleanup_marker)!r}).write_text('cleaned')\n",
    )
    result = run_pytest(
        project_root=Path(__file__).resolve().parents[1],
        output_dir=tmp_path / "out",
        nodeids=(str(case),),
        timeout_seconds=10,
        capture_budget=CaptureBudget(capture=capture, max_capture_bytes=1,
                                     min_free_bytes=1, poll_seconds=.01,
                                     cleanup_seconds=1),
    )
    assert result.verdict == "harness_failure"
    assert result.capture_guard["result"]["ref"] == "capture-guard.json"
    guard = json.loads((tmp_path / "out/capture-guard.json").read_text())
    assert guard["violation"]["kind"] == "budget"
    assert guard["interrupt_sent"]
    assert cleanup_marker.read_text() == "cleaned"
    if swallow_interrupt:
        assert result.exit_code == 0  # Guard violation still cannot turn green.


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group semantics")
@pytest.mark.parametrize("bootstrap_delay", [0.0, 2.5])
@pytest.mark.parametrize("exit_mode", ["timeout", "sigkill"])
def test_runner_timeout_cleans_descendant_after_pytest_leader_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bootstrap_delay: float,
    exit_mode: str,
) -> None:
    """Runner cleanup must cover a stubborn child after pytest exits on SIGTERM."""
    marker = tmp_path / "child.pid"
    child_code = (
        f"import os,pathlib,signal,time; time.sleep({bootstrap_delay}); "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); "
        "time.sleep(30)"
    )
    case = _write_case(
        tmp_path / "test_timeout_tree.py",
        "import os, signal, subprocess, sys, time\nfrom pathlib import Path\n\n"
        "def test_spawns_stubborn_descendant():\n"
        f"    subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        f"    while not Path({str(marker)!r}).exists():\n        time.sleep(0.01)\n"
        + ("    os.kill(os.getpid(), signal.SIGKILL)\n" if exit_mode == "sigkill" else "")
        +
        "    time.sleep(30)\n",
    )

    real_popen = subprocess.Popen
    ready: list[int] = []

    def wait_for_child_ready(command, *args, **kwargs):
        process = real_popen(command, *args, **kwargs)
        if list(command[:3]) != [sys.executable, "-m", "pytest"]:
            return process
        # Only synchronize the fixture's preparation. The runner still uses
        # the real wait/timeout/kill path after a stubborn child actually exists.
        try:
            assert kwargs["start_new_session"]
            deadline = time.monotonic() + 30
            while not marker.exists() and time.monotonic() < deadline:
                assert process.poll() is None, "pytest exited before child readiness"
                time.sleep(0.01)
            assert marker.exists(), "fixture child did not become ready in 30 seconds"
            ready.append(process.pid)
        except BaseException:
            terminate_process_group(process, timeout=1)
            raise
        return process

    monkeypatch.setattr(subprocess, "Popen", wait_for_child_ready)
    result = run_pytest(
        project_root=Path(__file__).resolve().parents[1],
        output_dir=tmp_path / "out",
        nodeids=(str(case),),
        timeout_seconds=2.0,
    )
    assert len(ready) == 1
    assert result.verdict == "harness_failure"
    assert result.timed_out is (exit_mode == "timeout")
    assert result.exit_code == (-signal.SIGTERM if exit_mode == "timeout" else -signal.SIGKILL)
    [case] = result.cases
    assert case["status"] == "not_run"
    assert case["phases"]["setup"]["outcome"] == "passed"
    assert "call" not in case["phases"] and "teardown" not in case["phases"]
    assert not result.session_finished
    assert marker.exists()
    child_pid = int(marker.read_text(encoding="utf-8"))
    child_stat = Path("/proc") / str(child_pid) / "stat"
    try:
        deadline = time.monotonic() + 2
        while child_stat.exists() and time.monotonic() < deadline:
            try:
                fields = child_stat.read_text(encoding="utf-8").split()
            except ProcessLookupError:
                # The runner may reap the child between exists() and read();
                # disappearance is the desired cleanup outcome.
                break
            if len(fields) > 2 and fields[2] == "Z":
                break
            time.sleep(0.01)
        assert not child_stat.exists() or child_stat.read_text(encoding="utf-8").split()[2] == "Z"
    finally:
        if child_stat.exists():
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_runner_rejects_empty_suite_before_starting_process(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one"):
        run_pytest(
            project_root=Path(__file__).resolve().parents[1],
            output_dir=tmp_path / "out",
            nodeids=(),
        )


def test_runner_requires_git_identity_for_a_green_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _write_case(
        tmp_path / "test_no_git.py",
        "def test_pass():\n    assert True\n",
    )

    monkeypatch.setattr(
        "tests.testkit.runner._git_identity", lambda _root: ("", False)
    )
    result = run_pytest(
        project_root=Path(__file__).resolve().parents[1],
        output_dir=tmp_path / "out",
        nodeids=(str(case),),
        timeout_seconds=30,
    )

    assert result.exit_code == 0
    assert result.git_head == ""
    assert result.verdict == "harness_failure"


def test_runner_rejects_environment_deselection_of_required_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _write_case(
        tmp_path / "test_selection.py",
        "def test_selected():\n    assert True\n\n"
        "def test_required():\n    assert False, 'required case must run'\n",
    )
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k test_selected")

    result = run_pytest(
        project_root=Path(__file__).resolve().parents[1],
        output_dir=tmp_path / "out",
        nodeids=(str(case),),
        timeout_seconds=30,
    )

    report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
    assert result.exit_code == 0  # pytest succeeds although required work is absent.
    assert len(report["deselected"]) == 1
    assert report["deselected"][0].endswith("::test_required")
    assert result.verdict == "fail"
    assert result.to_dict()["deselected"] == report["deselected"]


@pytest.mark.parametrize(
    "existing_name",
    [
        "pytest-testkit.json",
        "pytest-junit.xml",
        "pytest.stdout.log",
        "pytest.stderr.log",
        "testkit-run.json",
        "model-evidence",
    ],
)
def test_runner_refuses_reused_evidence_without_overwriting_or_running(
    tmp_path: Path, existing_name: str
) -> None:
    marker = tmp_path / "executed"
    case = _write_case(
        tmp_path / "test_never.py",
        f"from pathlib import Path\n\ndef test_never():\n"
        f"    Path({str(marker)!r}).touch()\n",
    )
    output = tmp_path / "out"
    output.mkdir()
    old_evidence = output / existing_name
    if existing_name == "model-evidence":
        old_evidence.mkdir()
        old_evidence = old_evidence / "first.json"
    old_evidence.write_bytes(b"previous run evidence\n")

    with pytest.raises(FileExistsError, match="evidence"):
        run_pytest(
            project_root=Path(__file__).resolve().parents[1],
            output_dir=output,
            nodeids=(str(case),),
            timeout_seconds=30,
        )

    assert old_evidence.read_bytes() == b"previous run evidence\n"
    assert not marker.exists()


@pytest.mark.parametrize("mode", ["--collect-only", "--setup-only"])
def test_runner_rejects_zero_exit_without_test_body_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    case = _write_case(
        tmp_path / "test_missing_call.py",
        "def test_required():\n    assert False, 'test body must execute'\n",
    )
    monkeypatch.setenv("PYTEST_ADDOPTS", mode)

    result = run_pytest(
        project_root=Path(__file__).resolve().parents[1],
        output_dir=tmp_path / "out",
        nodeids=(str(case),),
        timeout_seconds=30,
    )

    assert result.exit_code == 0
    assert _statuses(result) == {"test_required": "not_run"}
    assert result.verdict == "fail"


def test_runner_rejects_zero_exit_before_teardown_completes(tmp_path: Path) -> None:
    case = _write_case(
        tmp_path / "test_missing_teardown.py",
        """
import pytest

@pytest.fixture
def incomplete_cleanup():
    yield
    pytest.exit("cleanup interrupted", returncode=0)

def test_required(incomplete_cleanup):
    assert True
""",
    )

    result = run_pytest(
        project_root=Path(__file__).resolve().parents[1],
        output_dir=tmp_path / "out",
        nodeids=(str(case),),
        timeout_seconds=30,
    )

    assert result.exit_code == 0
    assert result.cases[0]["phases"]["call"]["outcome"] == "passed"
    assert "teardown" not in result.cases[0]["phases"]
    assert result.verdict == "fail"
    assert _statuses(result) == {"test_required": "not_run"}


def test_runner_claims_output_before_two_concurrent_runs_can_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _write_case(
        tmp_path / "test_concurrent_run.py",
        "import time\n\ndef test_slow():\n    time.sleep(0.2)\n",
    )
    output = tmp_path / "out"
    def invoke():  # noqa: ANN001
        try:
            return run_pytest(
                project_root=Path(__file__).resolve().parents[1],
                output_dir=output,
                nodeids=(str(case),),
                timeout_seconds=30,
            )
        except Exception as exc:  # noqa: BLE001
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(invoke) for _ in range(2)]
        results = [future.result() for future in futures]

    assert sum(isinstance(result, FileExistsError) for result in results) == 1
    assert (output / ".testkit-run.claim").exists()


@pytest.mark.parametrize("malformed_results", [[], {"case": []}, {"case": "bad"}])
def test_runner_malformed_result_evidence_is_harness_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    malformed_results: object,
) -> None:
    case = _write_case(tmp_path / "test_report_shape.py", "def test_pass():\n    assert True\n")
    monkeypatch.setattr(
        "tests.testkit.runner._load_report",
        lambda _path: {"session_finished": True, "results": malformed_results},
    )

    result = run_pytest(
        project_root=Path(__file__).resolve().parents[1],
        stage="S1",
        output_dir=tmp_path / "out",
        nodeids=(str(case),),
        timeout_seconds=30,
    )

    assert result.verdict == "harness_failure"
    assert result.cases == ()
    assert result.exit_code == 0
