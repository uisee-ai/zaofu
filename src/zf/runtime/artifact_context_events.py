"""Read the event evidence needed for an authorized artifact input page."""

from __future__ import annotations

import os
from pathlib import Path

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


class ArtifactContextEvents:
    """One CLI invocation's conservative event view and exact snapshot cache."""

    def __init__(self, log: EventLog) -> None:
        self.log = log
        self._signature: tuple | None = None
        self._events: tuple[ZfEvent, ...] = ()

    def read_all(self) -> list[ZfEvent]:
        paths = [*list_archives(self.log._archive_dir, suffix=".jsonl"), self.log.path]
        snapshots: list[tuple[Path, int, int, int, int]] = []
        for path in paths:
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            snapshots.append((path, stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size))
        signature = tuple(snapshots)
        if self.log.signer is None and self._signature == signature:
            return list(self._events)

        events: list[ZfEvent] = []
        for path, device, inode, _mtime, size in snapshots:
            try:
                with path.open("rb") as handle:
                    opened = os.fstat(handle.fileno())
                    if (opened.st_dev, opened.st_ino) != (device, inode):
                        return self.log.read_all()
                    remaining = size
                    while remaining:
                        line = handle.readline(remaining)
                        if not line or not line.endswith(b"\n"):
                            break
                        remaining -= len(line)
                        if self.log.signer is None and _irrelevant_hook(line):
                            continue
                        event = self.log.decode_line(line.decode("utf-8", errors="replace"))
                        if event is not None:
                            events.append(event)
            except FileNotFoundError:
                return self.log.read_all()
        if self.log.signer is None:
            self._signature = signature
            self._events = tuple(events)
        return events


def artifact_context_events(log: EventLog) -> list[ZfEvent]:
    """Share this invocation's ledger view without accepting external rows."""
    view = getattr(log, "_artifact_context_view", None)
    if not isinstance(view, ArtifactContextEvents) or view.log is not log:
        view = ArtifactContextEvents(log)
        setattr(log, "_artifact_context_view", view)
    return view.read_all()
