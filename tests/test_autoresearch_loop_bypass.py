"""Sprint §10 — bypass_autoresearch inner runner tests.

When the parent project's zf.yaml has its own role topology (e.g. cangjie-
mono with 13 panes) and the operator wants to drive their own tasks
(not autoresearch's pre-canned scenarios), the loop calls a simpler
inner runner that:

  1. wipes <worktree>/.zf so each iter starts clean
  2. copies cfg.yaml_template into <worktree>/zf.yaml
  3. runs `zf init --force` then `zf start`
  4. emits cfg.seed_text via `zf emit user.message`
  5. polls events.jsonl until terminal done count >= cfg.expected_done
     or cfg.inner_wait_timeout
  6. runs `zf stop`
  7. returns the same dict shape as autoresearch_fn
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from zf.autoresearch.loop import (
    LoopConfig,
    bypass_inner_run,
)


def _stub_proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["zf"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


class _FakePopen:
    """Stub for subprocess.Popen used in bypass_inner_run for zf start.

    Stays "alive" (poll() returns None) until terminate() is called.
    Tests provide an on_init callback so writing loop.started into the
    events file can be deferred or triggered by the test scenario.
    """

    def __init__(self, *args, **kwargs):
        self._terminated = False
        self._returncode: int | None = None

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        self._returncode = 0
        return 0

    def terminate(self):
        self._terminated = True
        self._returncode = -15

    def kill(self):
        self._returncode = -9


def _patch_subprocess(run_side_effect):
    """Patch subprocess.run + Popen used by bypass_inner_run.

    Returns a context-manager-like that satisfies both patches at once
    so individual tests don't have to nest two `with patch(...)` blocks.
    """
    from contextlib import contextmanager
    @contextmanager
    def _cm():
        with patch("zf.autoresearch.loop.subprocess.run", side_effect=run_side_effect):
            with patch("zf.autoresearch.loop.subprocess.Popen", _FakePopen):
                yield
    return _cm()


def _make_cfg(tmp_path: Path, *, seed: str = "do X", expected: int = 1) -> LoopConfig:
    yaml = tmp_path / "src.yaml"
    yaml.write_text("project:\n  name: t\n  state_dir: .zf\n")
    return LoopConfig(
        scenarios=["bypass"],
        worktree=tmp_path / "wt",
        parent_state_dir=tmp_path / "wt" / ".zf",
        max_iterations=1,
        output_dir=tmp_path / "out",
        yaml_template=yaml,
        seed_text=seed,
        expected_done=expected,
        inner_wait_timeout=5,
        bypass_autoresearch=True,
    )


def _write_events(state_dir: Path, lines: list[dict]) -> None:
    """Test helper: write events.jsonl. Prepends a synthetic
    loop.started so bypass_inner_run's readiness check (added with
    the Popen refactor) sees the harness as 'ready' and proceeds
    to emit + poll."""
    state_dir.mkdir(parents=True, exist_ok=True)
    all_lines = [{"type": "loop.started", "ts": "t-ready"}] + list(lines)
    (state_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in all_lines) + "\n"
    )


# ---------------------------------------------------------------------------
# Pre-flight: yaml + worktree
# ---------------------------------------------------------------------------


def test_bypass_copies_yaml_template_into_worktree(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run, \
         patch("zf.autoresearch.loop.subprocess.Popen", _FakePopen):
        mock_run.return_value = _stub_proc(0)
        # Pretend the inner harness wrote terminal done evidence.
        def side(cmd, **kw):
            if cmd[:3] == ["zf", "init", "--force"]:
                # Simulate init creating events file
                state = cfg.worktree / ".zf"
                _write_events(state, [{"type": "task.archived", "ts": "t"}])
            return _stub_proc(0)
        mock_run.side_effect = side
        bypass_inner_run(scenario="bypass", run_id="r1", cfg=cfg)
    # yaml was copied into worktree
    dest = cfg.worktree / "zf.yaml"
    assert dest.exists()
    # state_dir rewritten to absolute so all role worktrees share the
    # same truth (no per-worktree .zf fragmentation).
    dest_text = dest.read_text()
    assert "state_dir:" in dest_text
    expected_absolute = (cfg.worktree / ".zf").resolve()
    assert str(expected_absolute) in dest_text


def test_bypass_wipes_existing_state(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    # Pre-create stale state.
    (cfg.worktree / ".zf").mkdir(parents=True)
    (cfg.worktree / ".zf" / "stale.txt").write_text("old")
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run, \
         patch("zf.autoresearch.loop.subprocess.Popen", _FakePopen):
        mock_run.return_value = _stub_proc(0)
        def side(cmd, **kw):
            if cmd[:3] == ["zf", "init", "--force"]:
                _write_events(cfg.worktree / ".zf", [{"type": "task.archived", "ts": "t"}])
            return _stub_proc(0)
        mock_run.side_effect = side
        bypass_inner_run(scenario="bypass", run_id="r1", cfg=cfg)
    # Stale file gone after wipe.
    assert not (cfg.worktree / ".zf" / "stale.txt").exists()


def test_bypass_missing_yaml_template_returns_failed(tmp_path: Path) -> None:
    cfg = LoopConfig(
        scenarios=["s"],
        worktree=tmp_path / "wt",
        parent_state_dir=tmp_path / "wt" / ".zf",
        yaml_template=Path("/nonexistent/path.yaml"),
        seed_text="x",
        expected_done=1,
        inner_wait_timeout=5,
        bypass_autoresearch=True,
    )
    result = bypass_inner_run(scenario="s", run_id="r", cfg=cfg)
    assert result["status"] == "failed"
    assert "yaml_template" in result.get("fatal_event", {}).get("reason", "").lower()


# ---------------------------------------------------------------------------
# Seed emission
# ---------------------------------------------------------------------------


def test_bypass_emits_user_message_with_seed(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path, seed="实现 src/uniq.py 去重排序")
    calls = []
    def side(cmd, **kw):
        calls.append(cmd)
        if cmd[:3] == ["zf", "init", "--force"]:
            _write_events(cfg.worktree / ".zf", [{"type": "task.archived", "ts": "t"}])
        return _stub_proc(0)
    with patch("zf.autoresearch.loop.subprocess.run", side_effect=side), \
         patch("zf.autoresearch.loop.subprocess.Popen", _FakePopen):
        bypass_inner_run(scenario="bypass", run_id="r", cfg=cfg)
    emits = [c for c in calls if c[:3] == ["zf", "emit", "user.message"]]
    assert len(emits) == 1
    # The seed payload must be passed via --payload (JSON-encoded).
    payload_arg = None
    for i, tok in enumerate(emits[0]):
        if tok == "--payload":
            payload_arg = emits[0][i + 1]
            break
    assert payload_arg is not None
    parsed = json.loads(payload_arg)
    assert "实现 src/uniq.py" in parsed.get("text", "")


# ---------------------------------------------------------------------------
# Polling: terminal done count
# ---------------------------------------------------------------------------


def test_bypass_returns_passed_when_archived_count_meets_expected(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path, expected=2)
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run, \
         patch("zf.autoresearch.loop.subprocess.Popen", _FakePopen):
        def side(cmd, **kw):
            if cmd[:3] == ["zf", "init", "--force"]:
                _write_events(cfg.worktree / ".zf", [
                    {"type": "task.archived", "ts": "t1"},
                    {"type": "task.archived", "ts": "t2"},
                    {"type": "task.archived", "ts": "t3"},   # extras OK
                ])
            return _stub_proc(0)
        mock_run.side_effect = side
        result = bypass_inner_run(scenario="bypass", run_id="r", cfg=cfg)
    assert result["status"] == "passed"
    assert result["tasks_done"] >= 2
    assert result["expected_done"] == 2


def test_bypass_returns_passed_when_status_changed_done_meets_expected(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path, expected=1)
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run, \
         patch("zf.autoresearch.loop.subprocess.Popen", _FakePopen):
        def side(cmd, **kw):
            if cmd[:3] == ["zf", "init", "--force"]:
                _write_events(cfg.worktree / ".zf", [
                    {
                        "type": "task.status_changed",
                        "task_id": "TASK-1",
                        "payload": {"from": "in_progress", "to": "done"},
                    },
                ])
            return _stub_proc(0)
        mock_run.side_effect = side
        result = bypass_inner_run(scenario="bypass", run_id="r", cfg=cfg)
    assert result["status"] == "passed"
    assert result["tasks_done"] == 1
    assert result["expected_done"] == 1


def test_bypass_returns_passed_after_rework_when_reworked_task_done(
    tmp_path: Path,
) -> None:
    cfg = _make_cfg(tmp_path, expected=1)
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run, \
         patch("zf.autoresearch.loop.subprocess.Popen", _FakePopen):
        def side(cmd, **kw):
            if cmd[:3] == ["zf", "init", "--force"]:
                _write_events(cfg.worktree / ".zf", [
                    {
                        "type": "judge.failed",
                        "task_id": "TASK-1",
                        "payload": {"reason": "byte mismatch"},
                    },
                    {
                        "type": "task.rework.requested",
                        "task_id": "TASK-1",
                        "payload": {"target_role": "dev"},
                    },
                    {
                        "type": "dev.build.done",
                        "task_id": "TASK-1",
                    },
                    {
                        "type": "judge.passed",
                        "task_id": "TASK-1",
                    },
                    {
                        "type": "task.status_changed",
                        "task_id": "TASK-1",
                        "payload": {"to": "done"},
                    },
                ])
            return _stub_proc(0)
        mock_run.side_effect = side
        result = bypass_inner_run(scenario="bypass", run_id="r", cfg=cfg)

    assert result["status"] == "passed_after_rework"
    assert result["tasks_done"] == 1
    assert result["rework_count"] == 1
    assert result["passed_after_rework"] == 1
    assert result["pending_rework_count"] == 0


def test_bypass_returns_failed_pending_rework_on_timeout(
    tmp_path: Path,
) -> None:
    cfg = _make_cfg(tmp_path, expected=1)
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run, \
         patch("zf.autoresearch.loop.subprocess.Popen", _FakePopen):
        def side(cmd, **kw):
            if cmd[:3] == ["zf", "init", "--force"]:
                _write_events(cfg.worktree / ".zf", [
                    {
                        "type": "judge.failed",
                        "task_id": "TASK-1",
                        "payload": {"reason": "byte mismatch"},
                    },
                    {
                        "type": "task.rework.requested",
                        "task_id": "TASK-1",
                        "payload": {"target_role": "dev"},
                    },
                ])
            return _stub_proc(0)
        mock_run.side_effect = side
        result = bypass_inner_run(scenario="bypass", run_id="r", cfg=cfg)

    assert result["status"] == "failed_pending_rework"
    assert result["tasks_done"] == 0
    assert result["pending_rework_count"] == 1
    assert result["rework_events"][0]["type"] == "task.rework.requested"


def test_bypass_reports_validation_kinds_from_contract_update(
    tmp_path: Path,
) -> None:
    cfg = _make_cfg(tmp_path, expected=1)
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run, \
         patch("zf.autoresearch.loop.subprocess.Popen", _FakePopen):
        def side(cmd, **kw):
            if cmd[:3] == ["zf", "init", "--force"]:
                _write_events(cfg.worktree / ".zf", [
                    {
                        "type": "task.contract.update",
                        "task_id": "TASK-1",
                        "payload": {
                            "contract": {
                                "validation": {
                                    "kind": "byte_exact",
                                    "path": "proof.txt",
                                }
                            }
                        },
                    },
                    {
                        "type": "task.status_changed",
                        "task_id": "TASK-1",
                        "payload": {"to": "done"},
                    },
                ])
            return _stub_proc(0)
        mock_run.side_effect = side
        result = bypass_inner_run(scenario="bypass", run_id="r", cfg=cfg)

    assert result["validation_kinds"] == ["byte_exact"]


def test_bypass_dedupes_terminal_events_for_same_task(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path, expected=2)
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run, \
         patch("zf.autoresearch.loop.subprocess.Popen", _FakePopen):
        def side(cmd, **kw):
            if cmd[:3] == ["zf", "init", "--force"]:
                _write_events(cfg.worktree / ".zf", [
                    {
                        "type": "task.status_changed",
                        "task_id": "TASK-1",
                        "payload": {"to": "done"},
                    },
                    {"type": "task.archived", "task_id": "TASK-1"},
                ])
            return _stub_proc(0)
        mock_run.side_effect = side
        result = bypass_inner_run(scenario="bypass", run_id="r", cfg=cfg)
    assert result["status"] == "failed"
    assert result["tasks_done"] == 1
    assert result["expected_done"] == 2


def test_bypass_returns_failed_on_timeout_with_partial_count(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path, expected=3)
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run, \
         patch("zf.autoresearch.loop.subprocess.Popen", _FakePopen):
        def side(cmd, **kw):
            if cmd[:3] == ["zf", "init", "--force"]:
                _write_events(cfg.worktree / ".zf", [
                    {"type": "task.archived", "ts": "t1"},   # only 1 of 3
                ])
            return _stub_proc(0)
        mock_run.side_effect = side
        result = bypass_inner_run(scenario="bypass", run_id="r", cfg=cfg)
    assert result["status"] == "failed"
    assert result["tasks_done"] == 1
    assert result["expected_done"] == 3


def test_bypass_returns_fatal_on_fatal_event(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run, \
         patch("zf.autoresearch.loop.subprocess.Popen", _FakePopen):
        def side(cmd, **kw):
            if cmd[:3] == ["zf", "init", "--force"]:
                _write_events(cfg.worktree / ".zf", [
                    {"type": "task.orphaned", "ts": "t1", "task_id": "T1"},
                ])
            return _stub_proc(0)
        mock_run.side_effect = side
        result = bypass_inner_run(scenario="bypass", run_id="r", cfg=cfg)
    assert result["status"] == "fatal"
    assert result["fatal_event"]["type"] == "task.orphaned"


def test_bypass_returns_fatal_on_orchestrator_dispatch_failed(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run, \
         patch("zf.autoresearch.loop.subprocess.Popen", _FakePopen):
        def side(cmd, **kw):
            if cmd[:3] == ["zf", "init", "--force"]:
                _write_events(cfg.worktree / ".zf", [
                    {
                        "type": "orchestrator.dispatch_failed",
                        "payload": {"reason": "unknown role arch"},
                    },
                ])
            return _stub_proc(0)
        mock_run.side_effect = side
        result = bypass_inner_run(scenario="bypass", run_id="r", cfg=cfg)
    assert result["status"] == "fatal"
    assert result["tasks_done"] == 0
    assert result["fatal_event"]["type"] == "orchestrator.dispatch_failed"
    assert result["state_dir"] == str(cfg.worktree / ".zf")


# ---------------------------------------------------------------------------
# Cleanup: stop harness on exit
# ---------------------------------------------------------------------------


def test_bypass_rewrites_tmux_session_per_iter(tmp_path: Path) -> None:
    """The yaml copy must replace tmux_session so iter N doesn't share
    a tmux session name with iter N-1 (lingering panes risk)."""
    yaml = tmp_path / "src.yaml"
    yaml.write_text(
        "project:\n  name: t\n  state_dir: .zf\n"
        "session:\n  tmux_session: zf-cangjie-cc\n"
    )
    cfg = LoopConfig(
        scenarios=["s"],
        worktree=tmp_path / "wt",
        parent_state_dir=tmp_path / "wt" / ".zf",
        yaml_template=yaml,
        seed_text="x",
        expected_done=1,
        inner_wait_timeout=5,
        bypass_autoresearch=True,
    )

    def side(cmd, **kw):
        if cmd[:3] == ["zf", "init", "--force"]:
            _write_events(cfg.worktree / ".zf", [{"type": "task.archived", "ts": "t"}])
        return _stub_proc(0)

    with patch("zf.autoresearch.loop.subprocess.run", side_effect=side), \
         patch("zf.autoresearch.loop.subprocess.Popen", _FakePopen):
        bypass_inner_run(scenario="s", run_id="abc-123", cfg=cfg)

    dest = (cfg.worktree / "zf.yaml").read_text()
    # Original "zf-cangjie-cc" must be gone, replaced by run-id-suffixed.
    assert "zf-cangjie-cc" not in dest
    assert "zf-loop-abc-123" in dest


def test_rewrite_state_dir_absolute_also_rewrites_workdirs_and_skills(
    tmp_path: Path,
) -> None:
    # B8: workdirs.root / skills.pool / lock_file nest under a non-.zf state_dir
    # name (.zf-mixed). When state_dir is absolutized they must be rewritten too,
    # else WorkdirManager's PathGuard rejects workdirs as outside state_dir and
    # `zf start` aborts (the calc-fullflow bypass-loop 2s abort).
    from zf.autoresearch.loop_bypass import _rewrite_state_dir_absolute

    yaml_text = (
        "project:\n  name: t\n  state_dir: .zf-mixed\n"
        "runtime:\n  workdirs:\n    root: .zf-mixed/workdirs\n"
        "  skills:\n    pool: .zf-mixed/skills\n"
        "    lock_file: .zf-mixed/skills.lock.json\n"
    )
    abs_sd = tmp_path / ".zf"
    out = _rewrite_state_dir_absolute(yaml_text, abs_sd)

    assert f"state_dir: {abs_sd}" in out
    assert f"root: {abs_sd}/workdirs" in out
    assert f"pool: {abs_sd}/skills" in out
    assert f"lock_file: {abs_sd}/skills.lock.json" in out
    assert ".zf-mixed" not in out  # no stale references left to trip PathGuard


def test_rewrite_state_dir_absolute_no_prefix_collision(tmp_path: Path) -> None:
    # Matching the trailing slash means an old `.zf` value must not corrupt an
    # unrelated `.zf-mixed/...` token elsewhere in the yaml.
    from zf.autoresearch.loop_bypass import _rewrite_state_dir_absolute

    yaml_text = (
        "project:\n  name: t\n  state_dir: .zf\n"
        "runtime:\n  workdirs:\n    root: .zf/workdirs\n"
        "  note:\n    other: .zf-mixed/keep\n"
    )
    abs_sd = tmp_path / ".zf"
    out = _rewrite_state_dir_absolute(yaml_text, abs_sd)

    assert f"root: {abs_sd}/workdirs" in out
    assert ".zf-mixed/keep" in out  # untouched — no `.zf/` prefix collision


def test_bypass_runs_defensive_tmux_kill_on_exit(tmp_path: Path) -> None:
    """zf stop should normally kill the session, but as belt-and-
    suspenders we run tmux kill-session afterwards. Verifies the cmd
    is invoked with the iter-specific session name."""
    cfg = _make_cfg(tmp_path)
    calls = []
    def side(cmd, **kw):
        calls.append(cmd)
        if cmd[:3] == ["zf", "init", "--force"]:
            _write_events(cfg.worktree / ".zf", [{"type": "task.archived", "ts": "t"}])
        return _stub_proc(0)
    with patch("zf.autoresearch.loop.subprocess.run", side_effect=side), \
         patch("zf.autoresearch.loop.subprocess.Popen", _FakePopen):
        bypass_inner_run(scenario="s", run_id="r123", cfg=cfg)
    kill_cmds = [c for c in calls if c[:2] == ["tmux", "kill-session"]]
    assert len(kill_cmds) >= 1
    # Argument must be the per-iter unique session name.
    assert "zf-loop-r123" in " ".join(kill_cmds[0])


def test_bypass_calls_zf_stop_on_exit(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    calls = []
    def side(cmd, **kw):
        calls.append(cmd)
        if cmd[:3] == ["zf", "init", "--force"]:
            _write_events(cfg.worktree / ".zf", [{"type": "task.archived", "ts": "t"}])
        return _stub_proc(0)
    with patch("zf.autoresearch.loop.subprocess.run", side_effect=side), \
         patch("zf.autoresearch.loop.subprocess.Popen", _FakePopen):
        bypass_inner_run(scenario="bypass", run_id="r", cfg=cfg)
    stop_cmds = [c for c in calls if c[:2] == ["zf", "stop"]]
    assert len(stop_cmds) >= 1, "must call zf stop to clean up tmux session"
