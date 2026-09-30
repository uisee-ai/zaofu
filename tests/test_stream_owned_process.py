"""Owned SDK process boundary: cancellation, isolation and fail-closed cleanup."""

import asyncio
import os
from pathlib import Path
import signal

import pytest

pytest.importorskip("claude_code_sdk")
from claude_code_sdk import ClaudeCodeOptions

from zf.runtime import stream_owned_process as owned
from zf.runtime.stream_query_lifecycle import StreamCleanupError


pytestmark = [pytest.mark.host, pytest.mark.serial]


def _transport(tmp_path, name, scenario="await-release"):
    root = tmp_path / name
    root.mkdir()
    audit = root / "audit.json"
    options = ClaudeCodeOptions(
        cwd=str(root), env={"ZF_STREAM_AUDIT": str(audit)},
        extra_args={"session-id": name},
    )
    script = Path(__file__).parent / "testkit/fixtures/stream_interruption_cli.py"
    transport = owned.OwnedSubprocessCLITransport(
        prompt=scenario, options=options, cli_path=script,
    )
    return transport, audit


async def _ready(audit):
    async with asyncio.timeout(5):
        while not audit.with_suffix(".ready").exists():
            await asyncio.sleep(0.01)


def test_stop_one_group_preserves_other_sdk_query(tmp_path):
    async def run():
        victim, first_audit = _transport(tmp_path, "victim")
        witness, second_audit = _transport(tmp_path, "witness")
        try:
            await victim.connect()
            await witness.connect()
            await _ready(first_audit)
            await _ready(second_audit)
            first, second = victim._process, witness._process
            assert os.getpgid(first.pid) == first.pid
            assert os.getpgid(second.pid) == second.pid
            assert first.pid != second.pid != os.getpgrp()
            await victim.close()
            assert first.returncode == -signal.SIGTERM
            os.kill(second.pid, 0)
            assert second.returncode is None
            second_audit.with_suffix(".release").touch()
            messages = [m async for m in witness.read_messages()]
            assert messages[-1]["type"] == "result" and not messages[-1]["is_error"]
            await witness.close()
            assert second.returncode == 0
            await victim.close()  # Idempotent; no stale PGID signalling.
        finally:
            await victim.close()
            await witness.close()
    asyncio.run(run())


def test_cancellation_during_spawn_cannot_lose_process_handle(tmp_path, monkeypatch):
    async def run():
        transport, _ = _transport(tmp_path, "cancel-spawn")
        started, release = asyncio.Event(), asyncio.Event()
        original = owned.anyio.open_process
        processes = []

        async def delayed_spawn(*args, **kwargs):
            process = await original(*args, **kwargs)
            processes.append(process)
            started.set()
            await release.wait()
            return process

        monkeypatch.setattr(owned.anyio, "open_process", delayed_spawn)

        async def query():
            try:
                await transport.connect()
            finally:
                await transport.close()

        task = asyncio.create_task(query())
        try:
            await asyncio.wait_for(started.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()  # Raw asyncio cancellation, not an AnyIO cancel scope.
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
        [process] = processes
        assert process.returncode == -signal.SIGTERM
        assert transport._process is None
        with pytest.raises(ProcessLookupError):
            os.kill(process.pid, 0)
    asyncio.run(run())


def test_cancel_during_close_finishes_cleanup_before_raising(tmp_path, monkeypatch):
    async def run():
        transport, audit = _transport(tmp_path, "cancel-close")
        await transport.connect()
        await _ready(audit)
        process = transport._process
        entered, release = asyncio.Event(), asyncio.Event()
        original = transport._close_owned

        async def delayed_close():
            entered.set()
            await release.wait()
            await original()

        monkeypatch.setattr(transport, "_close_owned", delayed_close)
        task = asyncio.create_task(transport.close())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
        assert process.returncode == -signal.SIGTERM
        assert transport._process is None
    asyncio.run(run())


def test_unproven_cleanup_raises_and_preserves_ownership(tmp_path, monkeypatch):
    async def run():
        transport, audit = _transport(tmp_path, "cleanup-failed")
        await transport.connect()
        await _ready(audit)
        process = transport._process
        try:
            with monkeypatch.context() as patch:
                patch.setattr(owned, "_group_running", lambda _pgid: True)
                patch.setattr(transport, "_term_grace_s", 0.02)
                patch.setattr(transport, "_kill_grace_s", 0.02)
                with pytest.raises(StreamCleanupError, match="did not stop"):
                    await transport.close()
            assert transport._process is process
            assert transport._owned_pgid == process.pid
            assert not transport.is_ready()
        finally:
            await transport.close()
    asyncio.run(run())


def test_unowned_group_is_never_signalled(tmp_path, monkeypatch):
    async def run():
        transport, audit = _transport(tmp_path, "ownership-guard")
        await transport.connect()
        await _ready(audit)
        process = transport._process
        try:
            transport._owned_pgid = os.getpgrp()
            with monkeypatch.context() as patch:
                patch.setattr(owned.os, "killpg", lambda *_: pytest.fail("unowned signal"))
                with pytest.raises(StreamCleanupError, match="unowned"):
                    await transport.close()
            assert process.returncode is None
        finally:
            transport._owned_pgid = process.pid
            await transport.close()
    asyncio.run(run())
