"""ZF-TEST-111: successful append must sync file bytes before projection."""

from __future__ import annotations

import errno
import fcntl
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.locks import lock_path_for


def test_writer_syncs_complete_prefix_under_lock_before_projecting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    runner = Mock()
    synced: list[bytes] = []
    real_fsync = os.fsync

    def sync(fd: int) -> None:
        if os.readlink(f"/proc/self/fd/{fd}") == str(path):
            # flush must precede fsync, including for small buffered events.
            synced.append(path.read_bytes())
            with lock_path_for(path).open("a+") as contender:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        real_fsync(fd)

    def project(event_log: EventLog, event: ZfEvent) -> list:
        assert event_log is log
        assert synced and synced[-1] == path.read_bytes()
        assert synced[-1].endswith((event.to_json() + "\n").encode())
        return []

    monkeypatch.setattr(os, "fsync", sync)
    runner.run.side_effect = project
    writer = EventWriter(log, projector_runner=runner)
    events = [ZfEvent(type="test.durable", payload={"text": "小事件"}),
              ZfEvent(type="test.durable", payload={"text": "大" * 10000})]
    for event in events:
        writer.append(event)
    assert len(synced) == len(events)
    assert synced[1].startswith(synced[0])
    assert [event.id for event in EventLog(path).read_all()] == [event.id for event in events]


@pytest.mark.parametrize("error", [errno.EIO, errno.ENOSPC])
def test_sync_failure_propagates_without_index_or_projector_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: int,
) -> None:
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    index = Mock()
    log.index = index
    runner = Mock()
    runner.run.return_value = []
    writer = EventWriter(log, projector_runner=runner)
    event = ZfEvent(type="test.sync.failure")
    real_fsync = os.fsync

    def fail(fd: int) -> None:
        if os.readlink(f"/proc/self/fd/{fd}") == str(path):
            raise OSError(error, "injected ledger sync failure")
        real_fsync(fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fail)
        with pytest.raises(OSError) as raised:
            writer.append(event)
        assert raised.value.errno == error
    index.observe.assert_not_called()
    runner.run.assert_not_called()
    # A sync error is an uncertain commit, not permission to truncate the log.
    assert path.read_text() == event.to_json() + "\n"
    with lock_path_for(path).open("a+") as contender:
        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(contender, fcntl.LOCK_UN)
    log.index = None
    later = ZfEvent(type="test.after.failure")
    writer.append(later)
    assert [item.id for item in EventLog(path).read_all()] == [event.id, later.id]
    runner.run.assert_called_once_with(log, later)
