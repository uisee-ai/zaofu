"""Tmux session management for ZaoFu agent harness."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from zf.runtime.tmux_layout import TmuxLayout


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\[\?[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07")

# O-1 (doc 78 ops): vars that bind a tmux client to a nested server/pane.
_NESTED_TMUX_VARS = ("TMUX", "TMUX_PANE")


def tmux_env(base: "dict[str, str] | None" = None) -> dict[str, str]:
    """Return an environment with nested-tmux vars stripped.

    When the watcher/orchestrator runs inside a tmux pane (e.g. launched via a
    background runner that inherits TMUX/TMUX_PANE), tmux treats every `tmux`
    subprocess as a nested client bound to the caller's pane/server. new-session
    warns/refuses and `-t <session>` can resolve against the wrong server →
    "can't find session" and the harness freezes. Stripping TMUX/TMUX_PANE makes
    each command operate on the default server and target the harness's own
    named session deterministically. Always returns a fresh dict (callers may
    mutate it without affecting the source).
    """
    env = dict(os.environ if base is None else base)
    for var in _NESTED_TMUX_VARS:
        env.pop(var, None)
    return env


class TmuxError(Exception):
    pass


@dataclass(frozen=True)
class TmuxTerminationResult:
    """Outcome of a controlled tmux pane/window shutdown."""

    target: str
    pane_pid: str = ""
    term_sent: bool = False
    kill_sent: bool = False
    hard_killed: bool = False
    alive_after_term: bool = False
    alive_after_kill: bool = False


class TmuxSession:
    """Manage a tmux session for the harness.

    In dry_run mode, commands are recorded but not executed.

    ``layout`` (1206 Phase A) controls window/pane placement. Defaults
    to ``WindowPerRoleLayout`` which preserves the legacy one-window-
    per-role semantics; pass ``PaneGridLayout`` to collapse all roles
    into panes of a single window (Phase B activates that path).
    """

    def __init__(
        self,
        session_name: str = "zf",
        dry_run: bool = False,
        layout: "TmuxLayout | None" = None,
    ) -> None:
        self.session_name = session_name
        self.dry_run = dry_run
        self.command_log: list[str] = []
        # Lazy import to avoid a circular import at module load time —
        # tmux_layout itself imports TmuxSession for type hints.
        if layout is None:
            from zf.runtime.tmux_layout import WindowPerRoleLayout
            layout = WindowPerRoleLayout()
        self.layout = layout

    # -- helpers --

    def _run(self, args: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[str]:
        cmd_str = " ".join(args)
        self.command_log.append(cmd_str)

        if self.dry_run:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        try:
            return subprocess.run(
                args,
                capture_output=capture,
                text=True,
                timeout=10,
                check=check,
                env=tmux_env(),
            )
        except subprocess.CalledProcessError as e:
            raise TmuxError(f"tmux command failed: {cmd_str}\n{e.stderr}") from e
        except subprocess.TimeoutExpired as e:
            raise TmuxError(f"tmux command timed out: {cmd_str}") from e

    def _target(self, window: str) -> str:
        # 1206 Phase B: route through layout so pane_grid sends commands
        # to ``session:window.pane_idx`` while window_per_role keeps the
        # historical ``session:window`` form.
        return self.layout.address(self, window)

    # -- session lifecycle --

    def create_session(self) -> None:
        self._run([
            "tmux", "new-session",
            "-d", "-s", self.session_name,
            "-x", "200", "-y", "50",
        ])

    def kill_session(self) -> None:
        self._run(["tmux", "kill-session", "-t", self.session_name], check=False)

    def has_session(self) -> bool:
        if self.dry_run:
            return False
        result = self._run(
            ["tmux", "has-session", "-t", self.session_name],
            check=False,
        )
        return result.returncode == 0

    # -- window management --

    def create_window(self, name: str) -> None:
        self._run([
            "tmux", "new-window",
            "-t", self.session_name,
            "-n", name,
        ])

    def kill_window(self, name: str) -> None:
        # 1206 Phase B: under pane_grid, individual roles share one
        # window — tearing down the whole window would take everyone
        # down. Route to kill-pane when layout says the slot is a pane.
        target = self.layout.resolve(self, name)
        if target.pane is not None:
            self.layout.kill_slot(self, target)
        else:
            self._run(
                ["tmux", "kill-window", "-t", target.address()],
                check=False,
            )

    def terminate_window(
        self,
        name: str,
        *,
        grace_seconds: float = 2.0,
        poll_interval: float = 0.1,
    ) -> TmuxTerminationResult:
        """Best-effort controlled shutdown for a role slot.

        The normal respawn/cancel path should give the foreground worker a
        chance to exit before the pane is destroyed. We signal the tmux pane's
        process group first, escalate to KILL after the grace window, and then
        issue the existing hard tmux teardown to keep layout bookkeeping
        consistent even if the process already exited.
        """
        target = self.layout.resolve(self, name)
        pane_pid = self.pane_pid(name)
        term_sent = self._signal_pane_process_group(pane_pid, "TERM")
        alive_after_term = not self._wait_until_pane_gone(
            name,
            timeout_seconds=grace_seconds,
            poll_interval=poll_interval,
        )
        kill_sent = False
        alive_after_kill = alive_after_term
        if alive_after_term:
            kill_sent = self._signal_pane_process_group(pane_pid, "KILL")
            alive_after_kill = not self._wait_until_pane_gone(
                name,
                timeout_seconds=min(max(poll_interval, 0.0), 0.5),
                poll_interval=poll_interval,
            )

        self._hard_kill_target(target)
        return TmuxTerminationResult(
            target=target.address(),
            pane_pid=pane_pid,
            term_sent=term_sent,
            kill_sent=kill_sent,
            hard_killed=True,
            alive_after_term=alive_after_term,
            alive_after_kill=alive_after_kill,
        )

    def _hard_kill_target(self, target: object) -> None:
        pane = getattr(target, "pane", None)
        if pane is not None:
            self.layout.kill_slot(self, target)  # type: ignore[arg-type]
            return
        address = target.address()  # type: ignore[attr-defined]
        self._run(["tmux", "kill-window", "-t", address], check=False)

    def _signal_pane_process_group(self, pane_pid: str, signal_name: str) -> bool:
        pid = str(pane_pid or "").strip()
        if not pid.isdigit():
            return False
        try:
            pid_int = int(pid)
            if pid_int <= 1:
                return False
        except ValueError:
            return False
        try:
            sig = getattr(signal, f"SIG{signal_name.upper()}")
        except AttributeError:
            return False

        # Use the syscall directly instead of shelling out to /usr/bin/kill.
        # The old argv form `kill -TERM -<pid>` was one missing `--` away from
        # procps parsing `-1731963` as a `-1...` option bundle and broadcasting
        # kill(-1, SIGTERM) to every uid process.
        if self._send_signal(pid_int, sig, process_group=True):
            return True
        # Some systems do not put the foreground command in the pane shell's
        # process group. Fall back to the pane process itself before hard kill.
        return self._send_signal(pid_int, sig, process_group=False)

    def _send_signal(
        self,
        pid: int,
        sig: signal.Signals,
        *,
        process_group: bool,
    ) -> bool:
        target = "killpg" if process_group else "kill"
        self.command_log.append(f"os.{target}({pid}, {sig.name})")
        if self.dry_run:
            return True
        try:
            if process_group:
                os.killpg(pid, sig)
            else:
                os.kill(pid, sig)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            return False

    def _wait_until_pane_gone(
        self,
        name: str,
        *,
        timeout_seconds: float,
        poll_interval: float,
    ) -> bool:
        if self.dry_run:
            return not self.pane_alive(name)
        deadline = time.monotonic() + max(timeout_seconds, 0.0)
        while True:
            if not self.pane_alive(name):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(max(min(poll_interval, 0.5), 0.01))

    # -- pane interaction --

    def send_keys(self, window: str, text: str, *, enter: bool = True,
                  submit_key: str = "Enter",
                  submit_delay_s: float = 0.5) -> None:
        """Send text to the tmux pane, optionally followed by a submit key.

        ``enter=True`` submits via a SEPARATE ``tmux send-keys`` call
        after the text. A single ``send-keys "text" Enter`` does NOT
        work against Claude Code's multi-line TUI — the trailing Enter
        becomes a newline within the draft, not a submit. Splitting
        into two calls gives the TUI a chance to finalize the paste
        before the Enter arrives (B2 fix, test-plan 2026-04-15-2342).

        ``submit_delay_s`` inserts a short sleep between the two calls.
        B-1203-06 R-1 (2026-04-21 mixed-e2e): Codex's ratatui input
        handler loses the Enter if it arrives back-to-back with the
        paste — 500 ms of breathing room is enough to let the input
        buffer settle. Claude handles 0 ms fine but also tolerates
        the extra wait, so we apply it uniformly.

        ``submit_key`` lets the caller override the keystroke used to
        commit the draft. Both Claude and Codex accept ``Enter``; the
        parameter is exposed for future TUIs that need a different
        submit key.
        """
        target = self._target(window)
        self._run(["tmux", "send-keys", "-t", target, text])
        if enter:
            if submit_delay_s > 0:
                import time as _time
                _time.sleep(submit_delay_s)
            self._run(["tmux", "send-keys", "-t", target, submit_key])

    def clear_input(self, window: str) -> None:
        """Clear an unsubmitted provider draft without submitting it."""
        target = self._target(window)
        self._run(["tmux", "send-keys", "-t", target, "C-u"])

    def capture_pane(self, window: str, *, lines: int = 3000) -> str:
        target = self._target(window)
        result = self._run(
            ["tmux", "capture-pane", "-t", target, "-p", "-S", str(-lines)],
            capture=True,
        )
        return result.stdout

    def pane_alive(self, window: str) -> bool:
        if self.dry_run:
            return True
        return bool(self.pane_pid(window))

    def pane_pid(self, window: str) -> str:
        if self.dry_run:
            return ""
        target = self._target(window)
        result = self._run(
            ["tmux", "list-panes", "-t", target, "-F", "#{pane_pid}"],
            check=False,
            capture=True,
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip().splitlines()[0].strip()

    def pane_current_command(self, window: str) -> str:
        if self.dry_run:
            return ""
        target = self._target(window)
        result = self._run(
            [
                "tmux", "display-message",
                "-p", "-t", target,
                "#{pane_current_command}",
            ],
            check=False,
            capture=True,
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    def pane_process_probe(self, window: str, *, max_entries: int = 8) -> dict:
        """Return a bounded process-tree summary for one pane."""
        pane_pid = self.pane_pid(window)
        current_command = self.pane_current_command(window)
        probe = {
            "available": False,
            "pane_pid": pane_pid,
            "current_command": current_command,
            "processes": [],
        }
        if self.dry_run or not pane_pid.isdigit():
            return probe
        result = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=tmux_env(),
        )
        if result.returncode != 0:
            return probe
        rows: dict[str, tuple[str, str]] = {}
        children: dict[str, list[str]] = {}
        for raw in result.stdout.splitlines():
            parts = raw.strip().split(None, 2)
            if len(parts) < 2:
                continue
            pid, ppid = parts[0], parts[1]
            command = parts[2] if len(parts) > 2 else ""
            rows[pid] = (ppid, command)
            children.setdefault(ppid, []).append(pid)
        pending = list(children.get(pane_pid, []))
        descendants: list[dict[str, str]] = []
        seen: set[str] = set()
        while pending and len(descendants) < max_entries:
            pid = pending.pop(0)
            if pid in seen:
                continue
            seen.add(pid)
            ppid, command = rows.get(pid, ("", ""))
            descendants.append({"pid": pid, "ppid": ppid, "command": command})
            pending.extend(children.get(pid, []))
        probe["available"] = True
        probe["processes"] = descendants
        return probe

    def pane_current_path(self, window: str) -> str:
        if self.dry_run:
            return ""
        target = self._target(window)
        result = self._run(
            [
                "tmux", "display-message",
                "-p", "-t", target,
                "#{pane_current_path}",
            ],
            check=False,
            capture=True,
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    def wait_for_prompt(
        self,
        window: str,
        pattern: str,
        *,
        timeout: float = 30.0,
        poll_interval: float = 0.5,
    ) -> bool:
        if self.dry_run:
            return True

        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            output = self.capture_pane(window, lines=50)
            if re.search(pattern, output):
                return True
            time.sleep(poll_interval)
        return False

    # -- utilities --

    @staticmethod
    def strip_ansi(text: str) -> str:
        return _ANSI_RE.sub("", text)
