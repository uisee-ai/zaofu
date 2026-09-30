"""Opt-in host capability canaries for the provider boundary.

These checks deliberately stop at local tool/session capability.  They never
submit an Agent task or contact a remote service, so they are safe to run as a
host-tier preflight before an explicitly authorized provider smoke test.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
import uuid
from types import SimpleNamespace

import pytest

from zf.runtime.tmux import TmuxSession


class ProductionTmuxLifecycleBug(AssertionError):
    """A removed tmux target was still reported as an alive pane."""


def _tool_or_skip(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        pytest.skip(f"host tool unavailable: {name}")
    return path


@pytest.mark.host
def test_codex_cli_read_only_version_and_help_canary() -> None:
    """The installed Codex CLI must answer local metadata commands."""
    binary = _tool_or_skip("codex")
    version = subprocess.run(
        [binary, "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert version.returncode == 0
    assert re.search(r"codex(?:-cli)?\s+\S+", version.stdout, re.IGNORECASE)

    help_result = subprocess.run(
        [binary, "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert help_result.returncode == 0
    assert help_result.stdout.strip()


@pytest.mark.host
def test_tmux_temporary_session_lifecycle_does_not_touch_existing_sessions() -> None:
    """Create, inspect and remove only a uniquely named temporary session."""
    tmux = _tool_or_skip("tmux")
    name = f"zf-testkit-canary-{uuid.uuid4().hex}"
    created = subprocess.run(
        [tmux, "new-session", "-d", "-s", name, "sleep", "30"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert created.returncode == 0, created.stderr
    try:
        inspected = subprocess.run(
            [tmux, "has-session", "-t", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert inspected.returncode == 0, inspected.stderr
    finally:
        removed = subprocess.run(
            [tmux, "kill-session", "-t", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert removed.returncode == 0, removed.stderr


@pytest.mark.host
@pytest.mark.xfail(
    strict=True,
    raises=ProductionTmuxLifecycleBug,
    reason="ZF-TEST-017: pane_alive reports true after terminate_window removes the window",
)
def test_tmux_pane_crash_is_terminated_without_killing_the_session() -> None:
    """A real pane process can be terminated while the harness session survives."""
    _tool_or_skip("tmux")
    session_name = f"zf-testkit-pane-crash-{uuid.uuid4().hex}"
    role_name = "crash-lane"
    session = TmuxSession(session_name=session_name)
    role = SimpleNamespace(name=role_name, instance_id=role_name)
    session.create_session()
    try:
        target = session.layout.create_slot(session, role)
        session.send_keys(role_name, "sleep 30", submit_delay_s=0.0)

        deadline = 2.0
        while deadline > 0 and session.pane_current_command(role_name) not in {
            "sleep",
            "30",
        }:
            time.sleep(0.05)
            deadline -= 0.05
        assert session.pane_alive(role_name)
        assert target.window == role_name

        terminated = session.terminate_window(
            role_name,
            grace_seconds=0.2,
            poll_interval=0.05,
        )

        assert terminated.hard_killed is True
        assert terminated.target.endswith(f":{role_name}")
        if session.pane_alive(role_name):
            raise ProductionTmuxLifecycleBug(
                "pane_alive returned true after terminate_window removed the target window"
            )
        assert session.has_session()
    finally:
        session.kill_session()
