"""Regression test for B-WORKTREE-SHADOW-IMPORT-01.

Locks the contract that ``tests.e2e.run_mixed`` always launches subprocesses
with ``PYTHONPATH`` pinned to the runner's *absolute* repo ``src/`` and
that the runner's main process refuses to start when it itself was
loaded from a foreign ``src/zf/``.

The original bug surfaced as silent ``MockAdapter (['cat'])`` fallback
when the runner spawned ``zf start`` from a git worktree whose stale
``src/zf/`` shadow-imported the new ``backends:`` schema field. The fix
lives in ``_subprocess_env`` + ``_check_runner_environment``; if either
is bypassed by a future refactor, this test fails before the bug can
escape into a 5-minute live e2e run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.e2e.run_mixed import (
    REPO_ROOT,
    RunSummary,
    _check_runner_environment,
    _run,
    _subprocess_env,
    parse_args,
    print_summary,
    reset_state,
    start_harness,
    start_watcher,
)


# ---------------- _subprocess_env ----------------


def test_subprocess_env_pythonpath_is_absolute_repo_src() -> None:
    env = _subprocess_env()
    pp = env.get("PYTHONPATH", "")
    assert pp, "PYTHONPATH must be set"
    assert Path(pp).is_absolute(), f"PYTHONPATH must be absolute, got {pp!r}"
    assert pp == str(REPO_ROOT / "src"), (
        f"PYTHONPATH must point at REPO_ROOT/src ({REPO_ROOT/'src'}), got {pp!r}"
    )


def test_subprocess_env_does_not_inherit_relative_pythonpath(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the launching shell had a relative PYTHONPATH=src (the exact
    trap from B-WORKTREE-SHADOW-IMPORT-01), the helper must overwrite
    it with the absolute path."""
    monkeypatch.setenv("PYTHONPATH", "src")
    env = _subprocess_env()
    assert env["PYTHONPATH"] == str(REPO_ROOT / "src")


# ---------------- _run ----------------


def test_run_helper_injects_subprocess_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_run(cmd, **kw):  # noqa: ANN001, ANN003
        captured["cmd"] = cmd
        captured["kw"] = kw
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    _run(["echo", "hi"])
    assert "env" in captured["kw"], "_run must inject env"
    assert captured["kw"]["env"]["PYTHONPATH"] == str(REPO_ROOT / "src")


def test_run_helper_does_not_clobber_explicit_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A future caller passing ``env=...`` explicitly keeps its value
    (setdefault semantics). Documents the contract so a refactor that
    switches to ``kw['env'] = _subprocess_env()`` would fail this test
    and force an intentional decision."""
    captured: dict = {}

    def fake_run(cmd, **kw):  # noqa: ANN001, ANN003
        captured["kw"] = kw
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    custom_env = {"PYTHONPATH": "/explicitly/set", "MARKER": "1"}
    _run(["echo", "hi"], env=custom_env)
    assert captured["kw"]["env"] is custom_env


def test_expected_done_can_exceed_seed_count() -> None:
    args = parse_args([
        "--worktree", "/tmp/zaofu-test",
        "--seed-file", "/tmp/large-task.txt",
        "--expected-done", "3",
    ])

    assert args.expected_done == 3


def test_summary_uses_expected_done_not_seed_count(capsys: pytest.CaptureFixture[str]) -> None:
    summary = RunSummary(
        tasks_seeded=1,
        tasks_expected_done=3,
        tasks_done=3,
        elapsed_s=1.0,
        dispatch_by_instance={"dev-1": 2, "dev-2": 1},
        arch_proposals={"arch": 3},
        design_critiques={"critic": 3},
        builds_done={"dev-1": 2, "dev-2": 1},
        test_passed={},
        judge_passed={},
        gate_failed={},
        total_cost_usd=0.0,
        invariants={"guard": "pass"},
        timed_out=False,
    )

    assert print_summary(summary) == 0
    out = capsys.readouterr().out
    assert "tasks seeded:     1" in out
    assert "tasks expected:   3" in out
    assert "tasks done:       3 / 3" in out


def test_reset_state_clears_terminal_archives(tmp_path: Path) -> None:
    state = tmp_path / ".zf"
    for dirname in ("kanban", "feature_list", "events", "cost"):
        folder = state / dirname
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "2026-05-05.json").write_text("[{\"id\":\"old\"}]\n")
    for name in (
        "events.jsonl",
        "kanban.json",
        "feature_list.json",
        "role_sessions.yaml",
        "cost.jsonl",
    ):
        (state / name).parent.mkdir(parents=True, exist_ok=True)
        (state / name).write_text("old\n")

    reset_state(tmp_path)

    for dirname in ("kanban", "feature_list", "events", "cost"):
        folder = state / dirname
        assert folder.exists()
        assert list(folder.iterdir()) == []


# ---------------- start_harness (foreground watcher compatibility) ----------------


def test_start_harness_defers_to_start_watcher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Current ``zf start`` runs the watcher in foreground by default.

    The mixed runner must not call bare ``zf start`` before seeding tasks,
    otherwise it blocks forever in the watcher and ``seed_tasks`` never runs.
    """
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):  # noqa: ANN001, ANN003
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert start_harness(tmp_path) == 0
    assert calls == []


# ---------------- start_watcher (Popen path) ----------------


def test_start_watcher_passes_pinned_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """``start_watcher`` uses ``subprocess.Popen`` directly (not via
    ``_run``). It must explicitly pass ``env=_subprocess_env()`` —
    otherwise the foreground watcher would shadow-import like the
    original bug."""
    captured: dict = {}

    class FakePopen:
        def __init__(self, cmd, **kw):  # noqa: ANN001, ANN003
            captured["cmd"] = cmd
            captured["kw"] = kw
            self.pid = 4242

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    # Skip the 90s readiness wait deterministically: first time.time()
    # call sets the deadline, subsequent calls are past it so the while
    # loop falls through to the warning-and-return branch.
    times = iter([0.0, 1000.0, 1000.0])
    monkeypatch.setattr(
        "tests.e2e.run_mixed.time.time", lambda: next(times),
    )
    monkeypatch.setattr("tests.e2e.run_mixed.time.sleep", lambda *_: None)
    monkeypatch.setattr(
        "tests.e2e.run_mixed._count_event", lambda *a, **k: 0,
    )

    (tmp_path / ".zf" / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".zf" / "events.jsonl").write_text("")

    start_watcher(tmp_path)
    assert "env" in captured["kw"], (
        "start_watcher's Popen must pass env explicitly"
    )
    assert captured["kw"]["env"]["PYTHONPATH"] == str(REPO_ROOT / "src")


# ---------------- _check_runner_environment ----------------


def test_check_runner_environment_passes_when_zf_resolves_to_repo_root(
) -> None:
    """When the runner is invoked correctly (this very pytest run is the
    canonical correct invocation: it loaded ``zf`` from REPO_ROOT/src/zf),
    the self-check is a no-op."""
    _check_runner_environment()  # must not sys.exit


def test_check_runner_environment_aborts_on_shadow_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Simulate the bug: pretend ``zf`` was loaded from a foreign path.
    The self-check must call ``sys.exit`` with a message naming both
    paths so the user can fix their launch command."""
    fake_zf_dir = tmp_path / "stale" / "src" / "zf"
    fake_zf_dir.mkdir(parents=True)
    fake_init = fake_zf_dir / "__init__.py"
    fake_init.write_text("")

    import zf  # noqa: I001  (already imported by the test module)
    monkeypatch.setattr(zf, "__file__", str(fake_init))

    with pytest.raises(SystemExit) as excinfo:
        _check_runner_environment()
    msg = str(excinfo.value)
    assert "B-WORKTREE-SHADOW-IMPORT-01" in msg
    assert str(fake_zf_dir) in msg
    assert str((REPO_ROOT / "src" / "zf").resolve()) in msg
