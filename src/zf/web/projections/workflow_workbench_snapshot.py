"""Pin a graph read to an append-only ledger prefix without blocking providers."""
from __future__ import annotations

import hashlib
from pathlib import Path

from zf.core.events.segments import list_event_segments
from zf.core.state.locks import locked_path
from zf.web.projections.source_cache import fingerprint


def _prefix_digest(path: Path, size: int) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        remaining = size
        while remaining:
            chunk = stream.read(min(remaining, 1024 * 1024))
            if not chunk:
                raise ValueError("ledger prefix was truncated")
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def capture_snapshot(context, read):
    # Hold the writer lock only while taking the prefix, never while hydrating
    # artifacts or rendering a graph. Rotation also uses this lock.
    with locked_path(context.state_dir / "events.jsonl"):
        events = read()
        segments = [{"path": str(segment.path), "size": segment.size,
                     "sha256": _prefix_digest(segment.path, segment.size)}
                    for segment in list_event_segments(context.state_dir)]
        version = fingerprint(context.state_dir, [])
    return events, {"head_seq": len(events), "ledger_fingerprint": version, "segments": segments}


def snapshot_is_prefix(snapshot: dict) -> bool:
    """Appends are safe; truncation, replacement or edits are not."""
    try:
        return all(_prefix_digest(Path(row["path"]), row["size"]) == row["sha256"]
                   for row in snapshot["segments"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
