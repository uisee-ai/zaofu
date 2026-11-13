"""Read the event evidence needed for an authorized artifact input page."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.rotation import list_archives


_HOOK_PREFIXES = tuple(
    f'{{"type":"{provider}.hook.{phase}_tool_use",'.encode()
    for provider in ("codex", "claude")
    for phase in ("pre", "post")
)


def _irrelevant_hook(line: bytes) -> bool:
    """Skip only unambiguous hooks that cannot carry a read declaration.

    Legacy key escaping, duplicate type keys, signed envelopes, and any hook
    mentioning required_reads are decoded normally. Operation reduction does
    not consume hook events; required-read lookup only consumes events with a
    top-level payload.required_reads field.
    """
    return (
        line.startswith(_HOOK_PREFIXES)
        and line.count(b'"type"') == 1
        and b'"required_reads"' not in line
        and b"\\u" not in line
    )


def _signature(stat: os.stat_result) -> tuple[int, ...]:
    return (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)


@dataclass(frozen=True)
class _FileView:
    signature: tuple[int, ...]
    consumed: int
    prefix_digest: bytes
    events: tuple[ZfEvent, ...]


class _SnapshotChanged(OSError):
    pass


def _prefix_hash(stream: BinaryIO, size: int):
    digest = hashlib.sha256()
    while size:
        chunk = stream.read(min(size, 1024 * 1024))
        if not chunk:
            raise _SnapshotChanged("event prefix was truncated")
        digest.update(chunk)
        size -= len(chunk)
    return digest


class ArtifactContextEvents:
    """Conservative filtered view; see docs/impl/artifact-context-fold.md.

    The cache is local to this reader, never an alternative event authority.
    """

    def __init__(self, log: EventLog, *, preserve_run_aliases: bool = False,
                 preserve_event_ids: tuple[str, ...] = ()) -> None:
        self.log = log
        self.preserve_run_aliases = preserve_run_aliases
        self.preserve_event_ids = tuple(value.encode('ascii') for value in preserve_event_ids)
        self._files: dict[Path, _FileView] = {}

    def read_all(self) -> list[ZfEvent]:
        # A changed snapshot must not publish partial cache updates. Retry once
        # from current paths; an unproven ledger cannot authorize an operation.
        for _ in range(2):
            try:
                return self._read_snapshot()
            except (FileNotFoundError, _SnapshotChanged):
                self._files.clear()
        # Neither EventLog's mtime/size cache nor an unchecked full scan proves
        # completeness after a race: rotation can hide a terminal event.
        raise _SnapshotChanged("event sources remained unstable after retry")

    def _read_snapshot(self) -> list[ZfEvent]:
        if self.log.signer is not None:
            self._files.clear()
        paths = [*list_archives(self.log._archive_dir, suffix=".jsonl"), self.log.path]
        files: dict[Path, _FileView] = {}
        for path in paths:
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            files[path] = self._read_file(path, _signature(stat))
        if paths != [*list_archives(self.log._archive_dir, suffix=".jsonl"), self.log.path]:
            raise _SnapshotChanged("event archive inventory changed")
        for path in paths:
            try:
                current = _signature(path.stat())
            except FileNotFoundError:
                if path in files:
                    raise _SnapshotChanged("event file disappeared")
            else:
                if path not in files or not self._matches_snapshot(path, files[path], current):
                    raise _SnapshotChanged("event file changed during snapshot")
        if self.log.signer is None:
            self._files = files
        return [event for entry in files.values() for event in entry.events]

    def _read_file(self, path: Path, signature: tuple[int, ...]) -> _FileView:
        cached = self._files.get(path) if self.log.signer is None else None
        if cached is not None and cached.signature == signature:
            return cached
        events: list[ZfEvent] = []
        consumed = 0
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            if _signature(os.fstat(handle.fileno())) != signature:
                raise _SnapshotChanged("event file changed before opening")
            if (cached is not None and path == self.log.path
                    and cached.signature[:2] == signature[:2]
                    and signature[-1] > cached.signature[-1]):
                # Size/inode or a short boundary marker cannot prove append:
                # an earlier row could have been rewritten before growth.
                prefix = _prefix_hash(handle, cached.consumed)
                if prefix.digest() == cached.prefix_digest:
                    digest = prefix
                    consumed = cached.consumed
                    events.extend(cached.events)
                else:
                    handle.seek(0)
            while consumed < signature[-1]:
                remaining = signature[-1] - consumed
                line = handle.readline(remaining)
                if not line or (not line.endswith(b"\n") and len(line) < remaining):
                    raise _SnapshotChanged("event file ended before the snapshot boundary")
                if not line.endswith(b"\n"):
                    break
                consumed += len(line)
                digest.update(line)
                if self._skip(line):
                    continue
                event = self.log.decode_line(line.decode("utf-8", errors="replace"))
                if event is not None:
                    events.append(event)
        return _FileView(signature, consumed, digest.digest(), tuple(events))

    def _matches_snapshot(self, path: Path, entry: _FileView, current: tuple[int, ...]) -> bool:
        if current == entry.signature:
            return True
        if (path != self.log.path or current[:2] != entry.signature[:2]
                or current[-1] <= entry.signature[-1]):
            return False
        # A concurrent append may leave our bounded snapshot intact. Prove
        # its complete prefix again instead of replaying unchanged archives.
        # Keep the OLD signature/offset: the unread tail is for the next call.
        with path.open("rb") as stream:
            if _signature(os.fstat(stream.fileno())) != current:
                return False
            if _prefix_hash(stream, entry.consumed).digest() != entry.prefix_digest:
                return False
            if _signature(os.fstat(stream.fileno())) != current:
                return False
        return _signature(path.stat()) == current

    def _skip(self, line: bytes) -> bool:
        return (
            self.log.signer is None
            and _irrelevant_hook(line)
            and not any(event_id in line for event_id in self.preserve_event_ids)
            and not (
                self.preserve_run_aliases
                and any(key in line for key in (
                    b'"run_id"', b'"workflow_run_id"', b'"trace_id"',
                ))
            )
        )


def artifact_context_events(log: EventLog) -> list[ZfEvent]:
    """Share this invocation's ledger view without accepting external rows."""
    view = getattr(log, "_artifact_context_view", None)
    if not isinstance(view, ArtifactContextEvents) or view.log is not log:
        view = ArtifactContextEvents(log)
        setattr(log, "_artifact_context_view", view)
    return view.read_all()
