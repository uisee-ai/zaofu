"""Integer-compatible event positions bound to an append-only file identity."""

from __future__ import annotations

from pathlib import Path

from zf.core.state.locks import locked_path
from zf.core.state.rotation import list_archives


class EventOffset(int):
    """Keep byte-offset callers compatible while carrying rotation identity.

    Durable consumers must serialize ``identity`` alongside ``int(offset)``;
    a legacy bare integer retains active-file-only semantics.
    """

    identity: tuple[int, int] | None

    def __new__(cls, value: int, identity: tuple[int, int] | None = None):
        result = super().__new__(cls, value)
        result.identity = identity
        return result


def current_offset(path: Path) -> EventOffset:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return EventOffset(0)
    return EventOffset(stat.st_size, (stat.st_dev, stat.st_ino))


def read_tail(
    path: Path, offset: int, *, through: int | None = None,
) -> tuple[bytes, EventOffset]:
    """Capture complete bytes through one immutable watcher boundary.

    Rotation uses the same lock. Only capture bytes under it; decoding and
    runtime reactions happen after releasing the writer lock.
    """
    with locked_path(path):
        active = current_offset(path)
        end_identity = getattr(through, "identity", None) or active.identity
        start_identity = getattr(offset, "identity", None) or end_identity
        paths = [path] if active.identity else []
        if start_identity != active.identity or end_identity != active.identity:
            paths = list_archives(path.parent / path.stem, suffix=path.suffix) + paths
        positions = [(item, current_offset(item)) for item in paths]
        start = next((i for i, (_, pos) in enumerate(positions)
                      if pos.identity == start_identity), None)
        end = next((i for i, (_, pos) in enumerate(positions)
                    if pos.identity == end_identity), None)
        if through is not None and end is None and end_identity is not None:
            raise ValueError("consumed event segment is no longer available")
        if not positions:
            return b"", EventOffset(0)
        if end is None:
            end = len(positions) - 1
        if start is None:
            # Replaced/deleted history cannot justify seeking into the new file.
            start, offset = len(positions) - 1, 0
        limit = min(int(through), int(positions[end][1])) if through is not None else int(positions[end][1])
        if start > end or (start == end and int(offset) > limit and through is not None):
            return b"", EventOffset(int(offset), start_identity)
        chunks = []
        consumed = EventOffset(0)
        for index in range(start, end + 1):
            item, position = positions[index]
            begin = int(offset) if index == start and offset <= position else 0
            finish = limit if index == end else int(position)
            with item.open("rb") as stream:
                stream.seek(begin)
                data = stream.read(finish - begin)
            complete = data[:data.rfind(b"\n") + 1]
            chunks.append(complete)
            consumed = EventOffset(begin + len(complete), position.identity)
        return b"".join(chunks), consumed
