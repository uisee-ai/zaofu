"""Owned POSIX process groups for the lazy Claude SDK stream-json boundary.

SDK command construction, IO and message parsing remain SDK-owned. Only spawn
and close differ: children in the query's dedicated group must stop before the
query releases its session mutex. Descendants that call setsid are out of scope.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable
from contextlib import suppress
import os
from pathlib import Path
import signal
from subprocess import PIPE

import anyio
from anyio.streams.text import TextReceiveStream, TextSendStream
from claude_code_sdk import ClaudeCodeOptions, query
from claude_code_sdk._errors import CLIConnectionError, CLINotFoundError
from claude_code_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from claude_code_sdk.types import Message

from zf.runtime.stream_query_lifecycle import StreamCleanupError


async def _settle(operation: Awaitable[None]) -> None:
    """Defer raw asyncio cancellation too, not only AnyIO scope cancellation."""
    task = asyncio.create_task(operation)
    cancelled = False
    with anyio.CancelScope(shield=True):
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        task.result()  # A cleanup error takes precedence over cancellation.
    if cancelled:
        raise asyncio.CancelledError


def _group_running(pgid: int) -> bool:
    """Ignore dead zombies; their reaping belongs to their parent/subreaper."""
    proc = Path("/proc")
    if proc.is_dir():
        for entry in proc.iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                fields = (entry / "stat").read_text().rsplit(") ", 1)[1].split()
                if int(fields[2]) == pgid and fields[0] not in {"Z", "X"}:
                    return True
            except (FileNotFoundError, ProcessLookupError):
                continue  # A process disappeared during the scan.
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


class OwnedSubprocessCLITransport(SubprocessCLITransport):
    """SDK 0.0.25 adapter; integration tests exercise the actual SDK seam."""

    _owned_pgid: int | None = None
    _term_grace_s = 1.0
    _kill_grace_s = 1.0

    async def connect(self) -> None:
        if self._process:
            return
        if os.name != "posix":
            raise CLIConnectionError("stream-json owned process groups require POSIX")
        env = {**os.environ, **self._options.env, "CLAUDE_CODE_ENTRYPOINT": "sdk-py"}
        if self._cwd:
            env["PWD"] = self._cwd
        stderr = (
            self._options.debug_stderr
            if "debug-to-stderr" in self._options.extra_args
            and self._options.debug_stderr
            else None
        )

        async def start() -> None:
            # Cancellation must not lose ownership between fork and assignment.
            with anyio.CancelScope(shield=True):
                self._process = await anyio.open_process(
                    self._build_command(), stdin=PIPE, stdout=PIPE, stderr=stderr,
                    cwd=self._cwd, env=env, user=self._options.user,
                    start_new_session=True,
                )
                self._owned_pgid = self._process.pid
                if self._process.stdout:
                    self._stdout_stream = TextReceiveStream(self._process.stdout)
                if self._is_streaming and self._process.stdin:
                    self._stdin_stream = TextSendStream(self._process.stdin)
                elif self._process.stdin:
                    await self._process.stdin.aclose()
                self._ready = True

        try:
            await _settle(start())
        except FileNotFoundError as exc:
            error = (
                CLIConnectionError(f"Working directory does not exist: {self._cwd}")
                if self._cwd and not Path(self._cwd).exists()
                else CLINotFoundError(f"Claude Code not found at: {self._cli_path}")
            )
            self._exit_error = error
            raise error from exc
        except Exception as exc:
            error = CLIConnectionError(f"Failed to start Claude Code: {exc}")
            self._exit_error = error
            raise error from exc

    async def close(self) -> None:
        try:
            await _settle(self._close_owned())
        except Exception as exc:
            raise StreamCleanupError(f"stream-json cleanup failed: {exc}") from exc

    async def _close_owned(self) -> None:
        self._ready = False
        process = self._process
        if process is None:
            return
        pgid = self._owned_pgid
        # Never signal the harness group, nor a transport without spawn ownership.
        if pgid != process.pid or pgid == os.getpgrp():
            raise CLIConnectionError("Refusing to signal an unowned process group")
        with anyio.CancelScope(shield=True):
            for sig, grace in (
                (signal.SIGTERM, self._term_grace_s),
                (signal.SIGKILL, self._kill_grace_s),
            ):
                # Signal even if the leader exited: descendants may remain.
                with suppress(ProcessLookupError):
                    os.killpg(pgid, sig)
                with anyio.move_on_after(grace):
                    await process.wait()
                    while _group_running(pgid):
                        await anyio.sleep(0.02)
                if process.returncode is not None and not _group_running(pgid):
                    break
            else:
                # Preserve handles on failure; never claim successful cleanup.
                raise CLIConnectionError(f"Owned process group {pgid} did not stop")
            with anyio.fail_after(self._kill_grace_s):
                await process.aclose()
            self._process = None
            self._owned_pgid = None
            self._stdout_stream = None
            self._stdin_stream = None
            self._exit_error = None


async def owned_query(*, prompt: str, options: ClaudeCodeOptions) -> AsyncIterator[Message]:
    """String-prompt entry used by StreamJsonTransport (not a general SDK API)."""
    transport = OwnedSubprocessCLITransport(prompt=prompt, options=options)
    try:
        async for message in query(prompt=prompt, options=options, transport=transport):
            yield message
    finally:
        # Also covers connect/setup failures before SDK Query's own finally.
        await transport.close()
