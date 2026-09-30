"""Explicit real tmux -> Codex provider lifecycle canary.

The test is intentionally opt-in.  It exercises the same TmuxSession boundary
used by ZaoFu, while keeping the provider invocation ephemeral and read-only.
It is not part of the ordinary deterministic or merge gate tiers.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from zf.runtime.tmux import TmuxSession
from zf.core.security.redaction import redact_obj


RUN_REAL_TMUX_CODEX = os.environ.get(
    "ZF_RUN_REAL_TMUX_CODEX_CANARY", ""
).strip().lower() in {"1", "true", "yes"}
RUN_REAL_TMUX_CLAUDE = os.environ.get(
    "ZF_RUN_REAL_TMUX_CLAUDE_CANARY", ""
).strip().lower() in {"1", "true", "yes"}


class ProductionTmuxLifecycleBug(AssertionError):
    """A deleted tmux target was still reported as an alive pane."""


def _require_canary() -> str:
    if not RUN_REAL_TMUX_CODEX:
        pytest.skip("set ZF_RUN_REAL_TMUX_CODEX_CANARY=1 for the real tmux tier")
    binary = shutil.which("codex")
    if binary is None:
        pytest.skip("Codex CLI is unavailable")
    if shutil.which("tmux") is None:
        pytest.skip("tmux is unavailable")
    return binary


def _require_claude_canary() -> str:
    if not RUN_REAL_TMUX_CLAUDE:
        pytest.skip("set ZF_RUN_REAL_TMUX_CLAUDE_CANARY=1 for the real tmux tier")
    command = os.environ.get("ZF_REAL_CLAUDE_COMMAND", "").strip()
    if not command:
        command = shutil.which("claude") or ""
    if not command:
        command = (
            "/home/uisee/.vscode/extensions/anthropic.claude-code-2.1.275-linux-x64/"
            "resources/native-binary/claude"
        )
    parts = shlex.split(command)
    if not parts:
        pytest.skip("ZF_REAL_CLAUDE_COMMAND is empty")
    executable = parts[0]
    if Path(executable).is_absolute():
        available = Path(executable).is_file() and os.access(executable, os.X_OK)
    else:
        available = shutil.which(executable) is not None
    if not available:
        pytest.skip("Claude CLI is unavailable; set ZF_REAL_CLAUDE_COMMAND")
    if shutil.which("tmux") is None:
        pytest.skip("tmux is unavailable")
    return command


def _claude_model_args() -> str:
    model = os.environ.get("ZF_REAL_CLAUDE_MODEL", "").strip()
    return f"--model {shlex.quote(model)} " if model else ""


def _claude_result(pane: str) -> dict | None:
    """Decode joined tmux lines without deleting spaces from provider text."""
    for line in pane.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            return event
    return None


def _terminal_event(pane: str) -> tuple[dict, dict] | None:
    """Extract terminal events even when tmux visually wraps JSON lines."""

    # capture-pane returns terminal display lines, so long JSON records may be
    # split in the middle of a key.  Remove display whitespace before locating
    # the two records, then decode the bounded JSON objects from their opening
    # braces.  This keeps the assertion independent of the pane width.
    compact = re.sub(r"\s+", "", pane)
    item_start = compact.find('{"type":"item.completed"')
    turn_start = compact.find('{"type":"turn.completed"')
    if item_start < 0 or turn_start < 0:
        return None

    def decode_object(start: int) -> dict | None:
        decoder = json.JSONDecoder()
        try:
            value, _ = decoder.raw_decode(compact[start:])
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    item = decode_object(item_start)
    turn = decode_object(turn_start)
    if not isinstance(item, dict) or not isinstance(turn, dict):
        return None
    payload = item.get("item")
    if not isinstance(payload, dict) or payload.get("type") != "agent_message":
        return None
    return item, turn


def _run_codex_until_terminal(
    tmux: TmuxSession,
    *,
    window: str,
    binary: str,
    workspace: Path,
    marker: str,
    timeout_s: float = 90,
) -> tuple[dict, dict]:
    command = (
        f"{shlex.quote(binary)} exec --ephemeral --json --sandbox read-only "
        f"--skip-git-repo-check --cd {shlex.quote(str(workspace))} "
        f"{shlex.quote(f'Reply with exactly {marker} and do not call tools.')}"
    )
    tmux.send_keys(window, command, submit_delay_s=0.2)
    deadline = time.monotonic() + timeout_s
    pane = ""
    while time.monotonic() < deadline:
        pane = tmux.capture_pane(window, lines=3000)
        terminal = _terminal_event(pane)
        if terminal is not None:
            item, turn = terminal
            payload = item["item"]
            assert payload.get("text", "").strip() == marker
            assert turn.get("usage")
            return terminal
        time.sleep(0.5)
    raise AssertionError(
        "Codex did not emit item.completed + turn.completed through tmux; "
        f"pane_tail={pane[-4000:]}"
    )


@pytest.mark.host
@pytest.mark.real_provider
@pytest.mark.serial
def test_real_tmux_codex_exec_emits_terminal_events_and_cleans_session(
    tmp_path: Path,
) -> None:
    """Run a read-only ephemeral Codex turn through the real tmux boundary."""

    binary = _require_canary()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session_name = f"zf-real-tmux-{uuid.uuid4().hex}"
    tmux = TmuxSession(session_name=session_name)
    tmux.create_session()
    tmux.create_window("provider")

    marker = "ZF_TMUX_CODEX_OK"
    try:
        _run_codex_until_terminal(
            tmux,
            window="provider",
            binary=binary,
            workspace=workspace,
            marker=marker,
        )
        assert tmux.has_session()
        assert list(workspace.iterdir()) == []
    finally:
        tmux.kill_session()

    check = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert check.returncode != 0


@pytest.mark.host
@pytest.mark.real_provider
@pytest.mark.serial
@pytest.mark.xfail(
    strict=True,
    raises=ProductionTmuxLifecycleBug,
    reason="ZF-TEST-017: pane_alive reports true after terminate_window removes the window",
)
def test_real_tmux_codex_pane_kill_keeps_session_and_allows_restart(
    tmp_path: Path,
) -> None:
    """A killed real Provider pane can be replaced without losing the session."""

    if os.environ.get("ZF_RUN_REAL_TMUX_CODEX_RECOVERY", "").strip().lower() not in {
        "1",
        "true",
        "yes",
    }:
        pytest.skip("set ZF_RUN_REAL_TMUX_CODEX_RECOVERY=1 for the recovery tier")
    binary = _require_canary()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session_name = f"zf-real-tmux-recovery-{uuid.uuid4().hex}"
    tmux = TmuxSession(session_name=session_name)
    tmux.create_session()
    tmux.create_window("provider")
    try:
        # Start a real turn, wait until the provider process has replaced the
        # shell, then terminate only that owned tmux window.  The model may
        # finish early; the invariant under test is still window/session
        # ownership and safe replacement, not model latency.
        command = (
            f"{shlex.quote(binary)} exec --ephemeral --json --sandbox read-only "
            f"--skip-git-repo-check --cd {shlex.quote(str(workspace))} "
            f"{shlex.quote('Reply with exactly ZF_TMUX_KILL_ME and do not call tools.')}"
        )
        tmux.send_keys("provider", command, submit_delay_s=0.2)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if tmux.pane_current_command("provider") not in {"", "bash", "sh", "zsh"}:
                break
            time.sleep(0.2)
        termination = tmux.terminate_window(
            "provider",
            grace_seconds=0.2,
            poll_interval=0.05,
        )
        assert termination.hard_killed is True
        assert tmux.has_session()
        if tmux.pane_alive("provider"):
            raise ProductionTmuxLifecycleBug(
                "pane_alive returned true after terminate_window removed provider window"
            )

        tmux.create_window("provider-restart")
        _run_codex_until_terminal(
            tmux,
            window="provider-restart",
            binary=binary,
            workspace=workspace,
            marker="ZF_TMUX_RESTART_OK",
        )
        assert list(workspace.iterdir()) == []
    finally:
        tmux.kill_session()

    check = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert check.returncode != 0


@pytest.mark.host
@pytest.mark.real_provider
@pytest.mark.serial
def test_real_tmux_claude_print_emits_result_and_cleans_session(
    tmp_path: Path, record_property,
) -> None:
    """Run the Claude print/stream-json protocol through a real tmux pane."""

    command = _require_claude_canary()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session_name = f"zf-real-tmux-claude-{uuid.uuid4().hex}"
    tmux = TmuxSession(session_name=session_name)
    marker = "ZF_TMUX_CLAUDE_OK"
    prompt = f"Reply with exactly {marker} and do not call tools."
    command_line = (
        f"cd {shlex.quote(str(workspace))} && "
        f"{command} -p --safe-mode --tools '' --strict-mcp-config "
        f"--no-session-persistence --verbose "
        f"--output-format stream-json --permission-mode dontAsk "
        f"--permission-prompts none {_claude_model_args()}--system-prompt "
        f"{shlex.quote('Read-only canary. Do not call tools.')} "
        f"{shlex.quote(prompt)}"
    )
    # A separate line prevents the shell's echoed command from imitating exit evidence.
    command_line += "; zf_canary_rc=$?; printf '\\nZF_CLAUDE_EXIT=%s\\n' \"$zf_canary_rc\""
    pane = ""
    terminal = None
    exit_code = None
    try:
        tmux.create_session()
        tmux.create_window("provider")
        tmux.send_keys("provider", command_line, submit_delay_s=0.2)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            pane = subprocess.check_output(
                ["tmux", "capture-pane", "-J", "-p", "-S", "-4000",
                 "-t", f"{session_name}:provider"], text=True, timeout=5,
            )
            terminal = _claude_result(pane)
            exited = re.search(r"^ZF_CLAUDE_EXIT=(\d+)\s*$", pane, re.MULTILINE)
            if exited:
                exit_code = int(exited.group(1))
            if terminal is not None or exit_code is not None:
                break
            time.sleep(0.5)
        # A successful result alone is insufficient: the provider must exit cleanly.
        exit_deadline = min(deadline, time.monotonic() + 2)
        while exit_code is None and time.monotonic() < exit_deadline:
            pane = tmux.capture_pane("provider", lines=4000)
            exited = re.search(r"^ZF_CLAUDE_EXIT=(\d+)\s*$", pane, re.MULTILINE)
            if exited:
                exit_code = int(exited.group(1))
            else:
                time.sleep(0.1)
        assert terminal is not None, (
            "Claude did not emit a result event through tmux; "
            f"pane_tail={redact_obj(pane[-6000:])}"
        )
        assert terminal.get("is_error") is False, redact_obj(terminal)
        assert terminal.get("result") == marker, redact_obj(terminal)
        assert exit_code == 0, redact_obj(pane[-6000:])
        assert tmux.has_session()
        assert tmux.pane_current_path("provider") == str(workspace)
        assert list(workspace.iterdir()) == []
    finally:
        record_property("claude_tmux", json.dumps(redact_obj({
            "session": session_name, "workspace": str(workspace),
            "result": terminal, "exit_code": exit_code, "pane_tail": pane[-6000:],
        }), ensure_ascii=False))
        tmux.kill_session()
        assert not tmux.has_session()


@pytest.mark.host
@pytest.mark.real_provider
@pytest.mark.serial
def test_real_tmux_claude_timeout_is_not_reported_as_success(tmp_path: Path) -> None:
    """A bounded tmux Claude timeout must return to shell without a result."""

    if os.environ.get("ZF_RUN_REAL_TMUX_CLAUDE_TIMEOUT", "").strip().lower() not in {
        "1",
        "true",
        "yes",
    }:
        pytest.skip("set ZF_RUN_REAL_TMUX_CLAUDE_TIMEOUT=1 for the timeout tier")
    command = _require_claude_canary()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session_name = f"zf-real-tmux-claude-timeout-{uuid.uuid4().hex}"
    tmux = TmuxSession(session_name=session_name)
    tmux.create_session()
    tmux.create_window("provider")
    marker = "ZF_TMUX_CLAUDE_TIMEOUT_MUST_NOT_COMPLETE"
    command_line = (
        f"timeout 2s {command} -p --bare --no-session-persistence --verbose "
        f"--output-format stream-json --permission-mode dontAsk "
            f"--permission-prompts none {_claude_model_args()}--system-prompt "
        f"{shlex.quote('Read-only timeout canary. Do not call tools.')} "
        f"{shlex.quote(f'Reply with exactly {marker} and do not call tools.')}"
    )
    try:
        tmux.send_keys("provider", command_line, submit_delay_s=0.2)
        deadline = time.monotonic() + 20
        pane = ""
        started = False
        while time.monotonic() < deadline:
            pane = tmux.capture_pane("provider", lines=4000)
            compact = re.sub(r"\s+", "", pane)
            current = tmux.pane_current_command("provider")
            if current not in {"", "bash", "sh", "zsh"}:
                started = True
            if started and current in {"bash", "sh", "zsh"}:
                break
            time.sleep(0.25)
        compact = re.sub(r"\s+", "", pane)
        assert started, "tmux pane never observed the Claude provider process"
        assert f'"type":"result"' not in compact
        assert f'"result":"{marker}"' not in compact
        assert tmux.has_session()
        assert list(workspace.iterdir()) == []
    finally:
        tmux.kill_session()

    check = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert check.returncode != 0
