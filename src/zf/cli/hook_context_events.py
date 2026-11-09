"""Share one conservative ledger view when resolving provider hook context."""
import re
import os
from contextlib import ExitStack

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.artifact_context_events import ArtifactContextEvents
from zf.core.state.rotation import list_archives


def hook_context_events(log: EventLog) -> list[ZfEvent]:
    view = getattr(log, '_hook_context_event_view', None)
    if not isinstance(view, ArtifactContextEvents) or view.log is not log:
        view = ArtifactContextEvents(log, preserve_run_aliases=True)
        setattr(log, '_hook_context_event_view', view)
    return view.read_all()


class HookEventWriter(EventWriter):
    """Resolve hook parents without retaining unrelated telemetry bodies."""

    def _event_by_id(self, event_id: str) -> ZfEvent | None:
        index = getattr(self.event_log, 'index', None)
        cached = index.lookup_event(event_id) if index is not None else None
        if cached is not None:
            return cached
        # Other spellings can be JSON-escaped; preserve the general full read.
        if not re.fullmatch(r'[A-Za-z0-9_-]+', event_id):
            return super()._event_by_id(event_id)
        if self.event_log.signer is not None:
            return super()._event_by_id(event_id)
        try:
            return _reverse_parent(self.event_log, event_id)
        except OSError:
            return super()._event_by_id(event_id)


def _reverse_lines(handle, size: int):
    """Yield complete snapshot lines newest first, ignoring a partial tail."""
    pending = b''
    first = True
    while size:
        count = min(size, 1024 * 1024)
        size -= count
        handle.seek(size)
        parts = (handle.read(count) + pending).split(b'\n')
        pending = parts[0]
        for line in reversed(parts[1:]):
            if first:
                first = False
                continue
            if line:
                yield line
    if not first and pending:
        yield pending


def _reverse_parent(log: EventLog, event_id: str) -> ZfEvent | None:
    archives = list_archives(log._archive_dir, suffix='.jsonl')
    paths = [*archives, log.path]
    needle = event_id.encode('ascii')
    with ExitStack() as stack:
        opened = []
        for path in paths:
            try:
                handle = stack.enter_context(path.open('rb'))
            except FileNotFoundError:
                if path == log.path:
                    continue
                raise
            stat = os.fstat(handle.fileno())
            opened.append((path, handle, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns))
        result = None
        for path, handle, _dev, _ino, size, _mtime in reversed(opened):
            for line in _reverse_lines(handle, size):
                if needle not in line and b'\\u' not in line:
                    continue
                event = log.decode_line(line.decode('utf-8', errors='replace'))
                if event is not None and event.id == event_id:
                    result = event
                    break
            if result is not None:
                break
        if list_archives(log._archive_dir, suffix='.jsonl') != archives:
            raise OSError('event archives changed during parent lookup')
        for path, _handle, dev, ino, size, mtime in opened:
            current = path.stat()
            if ((current.st_dev, current.st_ino) != (dev, ino) or current.st_size < size
                    or (path != log.path and (current.st_size, current.st_mtime_ns) != (size, mtime))):
                raise OSError('event file changed during parent lookup')
        return result
