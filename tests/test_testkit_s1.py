"""S1 smoke tests for the deterministic ZaoFu TestKit."""

from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
import sys
import threading
import time

import pytest

from tests.testkit.cleanup import (
    assert_no_resource_leaks,
    assert_no_child_process_leaks,
    child_processes,
    remove_empty_temp_files,
    resource_snapshot,
)
from tests.testkit.command_runner import run_command
from tests.testkit.evidence import sha256_file, write_json_atomic
from tests.testkit.environment import capture_environment, write_environment
from tests.testkit.external_sinks import ExternalEffectSpy
from tests.testkit.oracles import (
    Verdict,
    assert_event_causation,
    assert_forbidden_events,
    assert_no_duplicate_event_ids,
    assert_required_events,
    classify_failure,
)
from tests.testkit.scenarios import isolated_project


def test_isolated_project_uses_explicit_state_dir(tmp_path: Path) -> None:
    project = isolated_project(
        tmp_path,
        run_id="RUN-1",
        task_id="TASK-1",
        operation_id="OP-1",
        attempt=2,
        generation=3,
    )
    assert project.root == tmp_path.resolve()
    assert project.state_dir == tmp_path / "state"
    assert (project.run_id, project.task_id, project.operation_id) == (
        "RUN-1",
        "TASK-1",
        "OP-1",
    )
    assert (project.attempt, project.generation) == (2, 3)
    assert not (tmp_path / ".zf").exists()
    event = project.emit("task.created", payload={"run_id": "RUN-1"})
    assert event.correlation_id == "RUN-1"
    assert event.origin == "kernel"
    assert project.events_path.exists()


def test_oracle_checks_required_forbidden_causation_and_ids(tmp_path: Path) -> None:
    project = isolated_project(tmp_path)
    created = project.emit("task.created")
    project.emit("task.dispatched", causation_id=created.id)
    log = project.event_log()
    assert_required_events(log, ["task.created", "task.dispatched"])
    assert_forbidden_events(log, ["run.goal.completed"])
    assert_event_causation(
        log,
        event_type="task.dispatched",
        causation_id=created.id,
    )
    assert_no_duplicate_event_ids(log)


def test_evidence_sidecar_is_atomic_and_digestable(tmp_path: Path) -> None:
    path = tmp_path / "evidence" / "result.json"
    digest = write_json_atomic(path, {"case_id": "s1", "status": "pass"})
    assert path.exists()
    assert digest == sha256_file(path)
    assert not list(path.parent.glob("*.tmp"))


def test_atomic_evidence_writes_are_collision_free_under_same_process_concurrency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent writers must not lose or fail an evidence receipt."""
    path = tmp_path / "evidence" / "result.json"
    barrier = threading.Barrier(2)
    original_write_text = Path.write_text

    def synchronized_write(self: Path, data: str, *args: object, **kwargs: object) -> int:
        if self.name.startswith(".result.json.") and self.name.endswith(".tmp"):
            barrier.wait(timeout=5)
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", synchronized_write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda ordinal: write_json_atomic(path, {"writer": ordinal}),
                (1, 2),
            )
        )

    assert len(results) == 2
    assert path.exists()
    assert not list(path.parent.glob("*.tmp"))


def test_command_runner_records_pass_and_fail(tmp_path: Path) -> None:
    output = tmp_path / "receipts"
    passed = run_command(
        [sys.executable, "-c", "print('ok')"],
        command_id="pass",
        cwd=tmp_path,
        output_dir=output,
    )
    failed = run_command(
        [sys.executable, "-c", "raise SystemExit(3)"],
        command_id="fail",
        cwd=tmp_path,
        output_dir=output,
    )
    assert passed.passed
    assert passed.exit_code == 0
    assert not failed.passed
    assert failed.exit_code == 3
    assert (output / "pass.receipt.json").exists()
    assert (output / "fail.stderr.log").exists()


def test_external_effect_spy_deduplicates_concurrent_operation_id() -> None:
    """Recovery retries must record every observation but execute once."""
    spy: ExternalEffectSpy[str] = ExternalEffectSpy()
    executions = 0
    execution_lock = threading.Lock()

    def callback() -> str:
        nonlocal executions
        with execution_lock:
            executions += 1
        time.sleep(0.02)
        return "remote-result"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _ordinal: spy.execute("OP-S1-CONCURRENT", callback), (1, 2))
        )

    assert results == ["remote-result", "remote-result"]
    assert executions == 1
    assert spy.execution_count("OP-S1-CONCURRENT") == 1
    assert [call.operation_key for call in spy.calls] == [
        "OP-S1-CONCURRENT",
        "OP-S1-CONCURRENT",
    ]
    assert sorted(call.executed for call in spy.calls) == [False, True]
    spy.assert_at_most_once()


def test_external_effect_spy_records_arguments_and_observation_order() -> None:
    """Effect evidence must explain what was attempted and in which order."""
    spy: ExternalEffectSpy[str] = ExternalEffectSpy()

    assert spy.execute(
        "OP-S1-ARGS",
        lambda: "remote-result",
        arguments={"target": "issue/1", "method": "POST"},
    ) == "remote-result"
    assert spy.execute(
        "OP-S1-ARGS",
        lambda: "must-not-run",
        arguments={"method": "POST", "target": "issue/1"},
    ) == "remote-result"

    calls = spy.calls
    assert [call.sequence for call in calls] == [0, 1]
    assert calls[0].arguments == calls[1].arguments == (
        ("method", "'POST'"),
        ("target", "'issue/1'"),
    )
    assert [call.executed for call in calls] == [True, False]
    assert spy.execution_count("OP-S1-ARGS") == 1


def test_command_runner_records_missing_executable_as_environment_error(
    tmp_path: Path,
) -> None:
    receipt = run_command(
        [str(tmp_path / "does-not-exist")],
        command_id="missing-tool",
        cwd=tmp_path,
        output_dir=tmp_path / "receipts",
    )

    assert receipt.passed is False
    assert receipt.exit_code is None
    assert receipt.timed_out is False
    assert receipt.environment_error is True
    assert "FileNotFoundError" in receipt.error
    assert "FileNotFoundError" in Path(receipt.stderr_ref).read_text(encoding="utf-8")
    payload = json.loads(
        (tmp_path / "receipts" / "missing-tool.receipt.json").read_text(encoding="utf-8")
    )
    assert payload["environment_error"] is True


def test_command_runner_does_not_overwrite_existing_command_evidence(
    tmp_path: Path,
) -> None:
    """A command ID is an immutable evidence claim within one output dir."""
    output = tmp_path / "receipts"
    first = run_command(
        [sys.executable, "-c", "print('first')"],
        command_id="immutable",
        cwd=tmp_path,
        output_dir=output,
    )
    stdout_before = Path(first.stdout_ref).read_text(encoding="utf-8")

    with pytest.raises(FileExistsError, match="already claimed"):
        run_command(
            [sys.executable, "-c", "print('second')"],
            command_id="immutable",
            cwd=tmp_path,
            output_dir=output,
        )

    assert Path(first.stdout_ref).read_text(encoding="utf-8") == stdout_before
    assert "first" in stdout_before
    assert "second" not in stdout_before


def test_command_runner_claim_is_atomic_under_concurrent_reuse(
    tmp_path: Path,
) -> None:
    """Concurrent callers must produce one receipt, never interleaved logs."""
    output = tmp_path / "receipts"

    def invoke(label: str):
        try:
            return run_command(
                [sys.executable, "-c", f"print({label!r})"],
                command_id="racing",
                cwd=tmp_path,
                output_dir=output,
            )
        except FileExistsError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(invoke, ("left", "right")))

    assert sum(result is not None for result in results) == 1
    stdout = (output / "racing.stdout.log").read_text(encoding="utf-8")
    assert stdout.strip() in {"left", "right"}
    assert (output / "racing.receipt.json").is_file()


@pytest.mark.parametrize("command_id", ("", "../escape", "has/slash", "-leading"))
def test_command_runner_rejects_unsafe_command_id(
    tmp_path: Path,
    command_id: str,
) -> None:
    with pytest.raises(ValueError, match="filename-safe"):
        run_command(
            [sys.executable, "-c", "pass"],
            command_id=command_id,
            cwd=tmp_path,
            output_dir=tmp_path / "receipts",
        )


def test_command_runner_timeout_is_not_pass(tmp_path: Path) -> None:
    receipt = run_command(
        [sys.executable, "-c", "import time; time.sleep(0.2)"],
        command_id="timeout",
        cwd=tmp_path,
        output_dir=tmp_path / "receipts",
        timeout_seconds=0.01,
    )
    assert receipt.timed_out
    assert not receipt.passed


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group semantics")
def test_command_runner_timeout_terminates_descendant_process_group(
    tmp_path: Path,
) -> None:
    """Timeout cleanup must not orphan a child spawned by the product command."""
    marker = tmp_path / "child.pid"
    script = (
        "import pathlib, subprocess, sys, time; "
        f"p=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        f"pathlib.Path({str(marker)!r}).write_text(str(p.pid)); time.sleep(30)"
    )
    receipt = run_command(
        [sys.executable, "-c", script],
        command_id="timeout-tree",
        cwd=tmp_path,
        output_dir=tmp_path / "receipts",
        timeout_seconds=0.2,
    )

    assert receipt.timed_out
    deadline = time.monotonic() + 2
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.exists()
    child_pid = int(marker.read_text(encoding="utf-8"))
    child_stat = Path("/proc") / str(child_pid) / "stat"
    deadline = time.monotonic() + 2
    state = ""
    while time.monotonic() < deadline:
        try:
            fields = child_stat.read_text(encoding="utf-8").split()
        except FileNotFoundError:
            state = "gone"
            break
        if len(fields) > 2 and fields[2] == "Z":
            state = "Z"
            break
        time.sleep(0.01)
    if state == "":
        try:
            state = child_stat.read_text(encoding="utf-8").split()[2]
        except FileNotFoundError:
            state = "gone"
    assert state in {"gone", "Z"}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group semantics")
def test_command_runner_timeout_cleans_group_after_leader_exits(
    tmp_path: Path,
) -> None:
    """A SIGTERM-exiting leader must not strand a stubborn group member."""
    marker = tmp_path / "child.pid"
    child_code = (
        "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(30)"
    )
    script = (
        "import pathlib, subprocess, sys, time; "
        f"p=subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        f"pathlib.Path({str(marker)!r}).write_text(str(p.pid)); time.sleep(30)"
    )
    receipt = run_command(
        [sys.executable, "-c", script],
        command_id="timeout-leader-exit",
        cwd=tmp_path,
        output_dir=tmp_path / "receipts",
        timeout_seconds=0.2,
    )
    assert receipt.timed_out
    assert marker.exists()
    child_pid = int(marker.read_text(encoding="utf-8"))
    child_stat = Path("/proc") / str(child_pid) / "stat"
    deadline = time.monotonic() + 2
    state = "gone"
    while time.monotonic() < deadline:
        try:
            fields = child_stat.read_text(encoding="utf-8").split()
        except FileNotFoundError:
            state = "gone"
            break
        if len(fields) > 2 and fields[2] == "Z":
            state = "Z"
            break
        state = fields[2] if len(fields) > 2 else "unknown"
        time.sleep(0.01)
    assert state in {"gone", "Z"}


def test_command_runner_passes_shell_metacharacters_as_literal_arguments(
    tmp_path: Path,
) -> None:
    """Product commands must execute without interpreting argument text as shell."""
    sentinel = tmp_path / "must-not-be-created"
    payload = f"$(touch {sentinel}) && git push --force"
    receipt = run_command(
        [sys.executable, "-c", "import sys; print(sys.argv[1])", payload],
        command_id="literal-argument",
        cwd=tmp_path,
        output_dir=tmp_path / "receipts",
    )

    assert receipt.passed
    assert (tmp_path / "receipts" / "literal-argument.stdout.log").read_text(
        encoding="utf-8",
    ).strip() == payload
    assert not sentinel.exists()


def test_failure_classification_is_not_a_product_score() -> None:
    assert classify_failure() is Verdict.PASS
    assert classify_failure(product_assertion_failed=True) is Verdict.FAIL
    assert classify_failure(environment_error=True) is Verdict.HARNESS_FAILURE
    assert classify_failure(flaky=True) is Verdict.FLAKY


def test_process_cleanup_helper_has_no_leak(tmp_path: Path) -> None:
    before = child_processes()
    write_json_atomic(tmp_path / "marker.json", {"before": time.time()})
    assert_no_child_process_leaks(before)


def test_resource_snapshot_detects_and_clears_scoped_transient_files(
    tmp_path: Path,
) -> None:
    scope = tmp_path / "state"
    scope.mkdir()
    before = resource_snapshot(scope)
    transient = scope / ".evidence.json.123.tmp"
    lock = scope / "events.jsonl.lock"
    transient.write_text("partial", encoding="utf-8")
    lock.write_text("owner", encoding="utf-8")

    with pytest.raises(AssertionError, match="lock files leaked"):
        assert_no_resource_leaks(before, root=scope)

    lock.unlink()
    remove_empty_temp_files(scope)
    # Non-empty temporary evidence is not silently deleted by the helper;
    # remove it explicitly after the leak assertion has proven visibility.
    transient.unlink()
    assert_no_resource_leaks(before, root=scope)


def test_resource_cleanup_preserves_preexisting_scope_files(
    tmp_path: Path,
) -> None:
    """Leak checks must only report resources created after their baseline."""
    scope = tmp_path / "state"
    scope.mkdir()
    existing_lock = scope / "preexisting.lock"
    existing_temp = scope / "preexisting.tmp"
    existing_lock.write_text("owned-before-test", encoding="utf-8")
    existing_temp.write_text("non-empty", encoding="utf-8")

    before = resource_snapshot(scope)
    new_lock = scope / "new.lockfile"
    new_temp = scope / ".new-evidence.tmp"
    new_lock.write_text("leaked-lock", encoding="utf-8")
    new_temp.write_text("partial", encoding="utf-8")

    with pytest.raises(AssertionError, match="new.lockfile"):
        assert_no_resource_leaks(before, root=scope)

    new_lock.unlink()
    new_temp.unlink()
    assert_no_resource_leaks(before, root=scope)
    assert existing_lock.read_text(encoding="utf-8") == "owned-before-test"
    assert existing_temp.read_text(encoding="utf-8") == "non-empty"


@pytest.mark.skipif(sys.platform == "win32", reason="symlink semantics vary on Windows")
def test_empty_temp_cleanup_does_not_follow_outside_symlink(
    tmp_path: Path,
) -> None:
    """Transient cleanup must never delete a target outside its scoped root."""
    scope = tmp_path / "state"
    scope.mkdir()
    outside = tmp_path / "outside.tmp"
    outside.write_text("", encoding="utf-8")
    link = scope / "linked.tmp"
    link.symlink_to(outside)

    remove_empty_temp_files(scope)

    assert outside.exists()
    assert not link.exists()


def test_environment_evidence_pins_commit_config_lock_and_state_dir(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config = tmp_path / "zf.yaml"
    lock = tmp_path / "uv.lock"
    config.write_text("project: {}\n", encoding="utf-8")
    lock.write_text("lock\n", encoding="utf-8")
    state_dir = tmp_path / "state"

    metadata = capture_environment(
        repo_root,
        state_dir,
        config_path=config,
        lock_path=lock,
    )
    evidence = tmp_path / "evidence" / "environment.json"
    write_environment(evidence, metadata)

    assert metadata["git_head"]
    assert metadata["config_digest"] == sha256_file(config)
    assert metadata["lock_digest"] == sha256_file(lock)
    assert metadata["state_dir"] == str(state_dir.resolve())
    assert evidence.exists()
