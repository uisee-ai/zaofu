"""doc 80 P3 activation (R20-B2): the authorized self-repair dispatch consumer
runs from the watcher tick, not just the operator-run CLI.

R20 dead-ended because autoresearch emitted ``autoresearch.repair.dispatch_requested``
but nothing ran the consumer — the stall never self-healed. ``dispatch_pending_self_repairs``
is that consumer (shared by the CLI and the tick): pending dispatch_requested →
isolated zaofu worktree + briefing + emit ``autoresearch.repair.dispatched`` + (spawn)
a headless agent. Idempotent + events-derived (a dispatched present → not pending).
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.self_repair_runner import (
    CLOSEOUT_REQUIRED,
    DISPATCH_BLOCKED,
    dispatch_pending_self_repairs,
    emit_self_repair_closeouts,
)


def _dispatch_requested(fp: str = "stall:X", attempt: int = 0) -> ZfEvent:
    return ZfEvent(
        type="autoresearch.repair.dispatch_requested",
        payload={
            "fingerprint": fp,
            "attempt": attempt,
            "candidate_id": "C-1",
            "candidate_path": "/x/cand.md",
            "repair_task_payload": {"contract": {"scope": ["src/zf/**"], "verification": "pytest x"}},
        },
    )


def _writer(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    return log, EventWriter(log)


def _git(cwd, *args):
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_pending_dispatch_emits_dispatched_and_spawns(tmp_path):
    log, writer = _writer(tmp_path)
    with patch("zf.runtime.self_repair_runner.subprocess.run") as mrun, \
         patch("zf.runtime.self_repair_runner.subprocess.Popen") as mpopen:
        mrun.return_value = MagicMock(returncode=0, stderr="")
        n = dispatch_pending_self_repairs(
            [_dispatch_requested()], writer,
            root=str(tmp_path),
            spawn=True,
            backend="claude-code",
            tmp_root=str(tmp_path),
        )
    assert n == 1
    dispatched = [e for e in log.read_all() if e.type == "autoresearch.repair.dispatched"]
    assert dispatched, "must emit autoresearch.repair.dispatched (closes the dead-end)"
    assert dispatched[0].payload["fingerprint"] == "stall:X"
    assert dispatched[0].payload["skill"] == "zf-self-repair"
    mpopen.assert_called_once()  # headless self-repair agent spawned


def test_claude_code_backend_uses_claude_cli_spawn_command(tmp_path):
    log, writer = _writer(tmp_path)
    with patch("zf.runtime.self_repair_runner.subprocess.run") as mrun, \
         patch("zf.runtime.self_repair_runner.subprocess.Popen") as mpopen:
        mrun.return_value = MagicMock(returncode=0, stderr="")
        n = dispatch_pending_self_repairs(
            [_dispatch_requested()],
            writer,
            root=str(tmp_path),
            spawn=True,
            backend="claude-code",
            tmp_root=str(tmp_path),
        )
    assert n == 1
    command = mpopen.call_args.args[0]
    assert command[:3] == ["claude", "--dangerously-skip-permissions", "-p"]


def test_codex_backend_uses_codex_exec_spawn_command(tmp_path):
    log, writer = _writer(tmp_path)
    with patch("zf.runtime.self_repair_runner.subprocess.run") as mrun, \
         patch("zf.runtime.self_repair_runner.subprocess.Popen") as mpopen:
        mrun.return_value = MagicMock(returncode=0, stderr="")
        n = dispatch_pending_self_repairs(
            [_dispatch_requested()],
            writer,
            root=str(tmp_path),
            spawn=True,
            backend="codex",
            tmp_root=str(tmp_path),
        )
    assert n == 1
    command = mpopen.call_args.args[0]
    assert command[:4] == [
        "codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "-C",
    ]
    assert command[5].startswith("# Authorized self-repair")


def test_spawn_without_backend_is_blocked_not_guessed(tmp_path):
    log, writer = _writer(tmp_path)
    with patch("zf.runtime.self_repair_runner.subprocess.run") as mrun, \
         patch("zf.runtime.self_repair_runner.subprocess.Popen") as mpopen:
        mrun.return_value = MagicMock(returncode=0, stderr="")
        n = dispatch_pending_self_repairs(
            [_dispatch_requested()],
            writer,
            root=str(tmp_path),
            spawn=True,
            backend="",
            tmp_root=str(tmp_path),
        )

    events = log.read_all()
    assert n == 1
    mpopen.assert_not_called()
    blocked = [event for event in events if event.type == DISPATCH_BLOCKED]
    assert blocked
    assert blocked[-1].payload["reason"] == "self_repair_backend_not_configured"


def test_no_pending_is_no_op(tmp_path):
    log, writer = _writer(tmp_path)
    n = dispatch_pending_self_repairs([], writer, root=str(tmp_path), tmp_root=str(tmp_path))
    assert n == 0
    assert not list(log.read_all())


def test_idempotent_when_already_dispatched(tmp_path):
    """A dispatched already present → not pending → no re-dispatch (doc 80 inv 4)."""
    log, writer = _writer(tmp_path)
    events = [
        _dispatch_requested(),
        ZfEvent(type="autoresearch.repair.dispatched", payload={"fingerprint": "stall:X", "attempt": 0}),
    ]
    with patch("zf.runtime.self_repair_runner.subprocess.run") as mrun:
        mrun.return_value = MagicMock(returncode=0, stderr="")
        n = dispatch_pending_self_repairs(events, writer, root=str(tmp_path), tmp_root=str(tmp_path))
    assert n == 0


def test_no_spawn_still_prepares(tmp_path):
    """spawn=False still emits dispatched (prepare) — the dead-end is closed even
    without the agent launch; the spawn is the extra unattended step."""
    log, writer = _writer(tmp_path)
    with patch("zf.runtime.self_repair_runner.subprocess.run") as mrun, \
         patch("zf.runtime.self_repair_runner.subprocess.Popen") as mpopen:
        mrun.return_value = MagicMock(returncode=0, stderr="")
        n = dispatch_pending_self_repairs(
            [_dispatch_requested()], writer, root=str(tmp_path), spawn=False, tmp_root=str(tmp_path),
        )
    assert n == 1
    assert any(e.type == "autoresearch.repair.dispatched" for e in log.read_all())
    mpopen.assert_not_called()


def test_spawn_failure_emits_dispatch_blocked(tmp_path):
    log, writer = _writer(tmp_path)
    with patch("zf.runtime.self_repair_runner.subprocess.run") as mrun, \
         patch("zf.runtime.self_repair_runner.subprocess.Popen") as mpopen:
        mrun.return_value = MagicMock(returncode=0, stderr="")
        mpopen.side_effect = FileNotFoundError("codex not found")
        n = dispatch_pending_self_repairs(
            [_dispatch_requested()],
            writer,
            root=str(tmp_path),
            spawn=True,
            backend="codex",
            tmp_root=str(tmp_path),
        )

    events = log.read_all()
    assert n == 1
    assert any(event.type == "autoresearch.repair.dispatched" for event in events)
    blocked = [event for event in events if event.type == DISPATCH_BLOCKED]
    assert blocked
    assert blocked[-1].payload["reason"] == "spawn_failed"
    assert blocked[-1].payload["backend"] == "codex"


def test_closeout_required_emitted_when_repair_worktree_has_commit(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "init")
    branch = "self-repair/stall-X-a0"
    worktree = tmp_path / "worktree"
    _git(root, "worktree", "add", "-B", branch, str(worktree), "HEAD")
    (worktree / "fix.txt").write_text("fix\n", encoding="utf-8")
    _git(worktree, "add", "fix.txt")
    _git(worktree, "commit", "-q", "-m", "fix: repair stall")
    log, writer = _writer(tmp_path)
    dispatched = ZfEvent(
        type="autoresearch.repair.dispatched",
        payload={
            "fingerprint": "stall:X",
            "attempt": 0,
            "candidate_id": "C-1",
            "branch": branch,
            "worktree": str(worktree),
        },
    )

    count = emit_self_repair_closeouts([dispatched], writer, root=str(root))

    assert count == 1
    closeout = [event for event in log.read_all() if event.type == CLOSEOUT_REQUIRED][-1]
    assert closeout.payload["fingerprint"] == "stall:X"
    assert closeout.payload["source_title"] == "fix: repair stall"
    assert closeout.payload["restart_required"] is False
    assert (
        closeout.payload["restart_strategy"]
        == "control_plane_restart_preserve_run_manager"
    )
    assert closeout.payload["safe_boundary"] == "terminal_or_operator_approved_checkpoint"
    assert closeout.payload["state_snapshot_required"] is True
    assert closeout.payload["replay_required"] is True
    assert closeout.payload["auto_merge"] is False
    assert closeout.payload["risk_classification"]["risk"] == "high"
    assert closeout.payload["risk_classification"]["human_approval_required"] is True
    assert closeout.payload["changed_files"] == ["fix.txt"]
    assert closeout.payload["verification_plan"][0]["command"] == "git diff --check"
    assert closeout.payload["continuation"]["resume_original_workflow"] is True
    assert closeout.payload["continuation"]["restart_required"] is False
    assert (
        closeout.payload["continuation"]["resume_strategy"]
        == "snapshot_replay_then_preserve_run_manager_control_plane_restart"
    )

    second = emit_self_repair_closeouts(
        [dispatched, closeout],
        writer,
        root=str(root),
    )
    assert second == 0
