"""Opt-in smoke test for a real Fix worker in an isolated project.

This is deliberately not part of the default suite: it starts a real Codex
process in a unique tmux session and therefore requires provider credentials.
Run with ``ZF_REAL_FIX_WORKER=1 uv run pytest -q
tests/test_real_fix_worker_sandbox.py -s --no-cov``.
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest


def test_real_fix_worker_edits_and_verifies_isolated_project(tmp_path: Path) -> None:
    if os.environ.get("ZF_REAL_FIX_WORKER") != "1":
        pytest.skip("set ZF_REAL_FIX_WORKER=1 to run the real provider smoke test")

    from zf.core.config.schema import ConstraintsConfig, RoleConfig
    from zf.core.events.log import EventLog
    from zf.core.state.role_sessions import RoleSessionRegistry
    from zf.runtime.spawn_coordinator import SpawnCoordinator
    from zf.runtime.tmux import TmuxSession
    from zf.runtime.transport import DispatchContext, TmuxTransport

    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("isolated fix worker sandbox\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(["git", "-C", str(project), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(project), "-c", "user.name=zf", "-c", "user.email=zf@example.invalid", "commit", "-qm", "seed"],
        check=True,
    )
    state_dir = project / ".zf-sandbox"
    state_dir.mkdir()
    briefing = state_dir / "briefings" / "fix.md"
    briefing.parent.mkdir()
    briefing.write_text("real controlled Fix worker smoke test\n", encoding="utf-8")

    instance_id = "issue-fix-sandbox-0"
    role = RoleConfig(
        name="issue-fix-sandbox",
        instance_id=instance_id,
        backend="codex",
        role_kind="writer",
        permission_mode="bypass",
        constraints=ConstraintsConfig(allowed_paths=[str(project)]),
    )
    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml", str(project.resolve())
    )
    event_log = EventLog(state_dir / "events.jsonl")
    session_name = f"zf-fix-sandbox-{uuid.uuid4().hex[:12]}"
    tmux = TmuxSession(session_name=session_name)
    transport = TmuxTransport(tmux)
    coordinator = SpawnCoordinator(
        state_dir=state_dir,
        registry=registry,
        transport=transport,
        project_root=str(project.resolve()),
        event_log=event_log,
    )
    try:
        transport.init()
        coordinator.spawn(role, cwd=project)
        adapter_pattern = "›"
        assert transport.wait_ready(instance_id, adapter_pattern, timeout=30), (
            "real Fix worker did not become ready: "
            f"{transport.readiness_diagnostics(instance_id)}\n"
            f"{transport.capture_log(instance_id, lines=80)}"
        )
        # Match CodexAdapter.post_ready_delay_s: the visual prompt can paint
        # before ratatui has wired the first input handler.
        time.sleep(2.0)
        transport.send_task(
            instance_id,
            briefing,
            (
                "Active task: ISSUE-FIX-SANDBOX\n"
                "You are the controlled Fix worker. In this project, create a file "
                "named fix.txt containing exactly `fix worker initial candidate` "
                "followed by a newline. Do not modify any other file and do not commit. "
                "After writing it, run `test -f fix.txt` and report the result."
            ),
            context=DispatchContext(backend="codex"),
        )
        # Exercise the same bounded recovery used when a provider paints its
        # prompt before accepting the first Enter: one explicit resubmit is
        # allowed after the initial dispatch, without restarting the worker.
        recovery_deadline = time.monotonic() + 10
        while time.monotonic() < recovery_deadline and not (project / "fix.txt").exists():
            time.sleep(1)
        if not (project / "fix.txt").exists():
            tmux.send_keys(instance_id, "", submit_delay_s=0.0)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not (project / "fix.txt").exists():
            time.sleep(1)
        assert (project / "fix.txt").read_text(encoding="utf-8") == "fix worker initial candidate\n"
        first_verification = subprocess.run(
            ["bash", "-lc", "test \"$(cat fix.txt)\" = 'fix worker completed'"],
            cwd=project,
            check=False,
        )
        assert first_verification.returncode != 0

        # A failed dynamic verification routes the same controlled worker to a
        # bounded rework turn; the second candidate must be verified against
        # the exact command that rejected the first one.
        transport.send_task(
            instance_id,
            briefing,
            (
                "Active task: ISSUE-FIX-SANDBOX rework attempt 2\n"
                "The verification command failed because fix.txt has the wrong "
                "content. Overwrite only fix.txt with exactly `fix worker completed` "
                "followed by a newline, then rerun `test \"$(cat fix.txt)\" = "
                "'fix worker completed'` and report the result."
            ),
            context=DispatchContext(backend="codex"),
        )
        rework_deadline = time.monotonic() + 10
        while time.monotonic() < rework_deadline:
            if (project / "fix.txt").read_text(encoding="utf-8") == "fix worker completed\n":
                break
            time.sleep(1)
        if (project / "fix.txt").read_text(encoding="utf-8") != "fix worker completed\n":
            tmux.send_keys(instance_id, "", submit_delay_s=0.0)
        final_deadline = time.monotonic() + 120
        while time.monotonic() < final_deadline:
            if (project / "fix.txt").read_text(encoding="utf-8") == "fix worker completed\n":
                break
            time.sleep(1)
        assert (project / "fix.txt").read_text(encoding="utf-8") == "fix worker completed\n"
        subprocess.run(
            ["bash", "-lc", "test \"$(cat fix.txt)\" = 'fix worker completed'"],
            cwd=project,
            check=True,
        )
    finally:
        if tmux.has_session():
            transport.shutdown()
