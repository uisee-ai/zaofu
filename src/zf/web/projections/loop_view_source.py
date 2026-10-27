"""Exact source watermarks for the cached Loop view.

The SQLite projection cache is disposable.  Reuse is authorised only by the
current EventLog and the small set of files that ``build_loop_view`` reads.
No read-model sequence is used as a second freshness authority.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zf.core.events.factory import build_event_signer
from zf.core.events.model import ZfEvent
from zf.core.events.segments import iter_event_records, list_event_segments

LOOP_VIEW_SOURCE_FIELD = "_loop_view_source_fingerprint"
_SOURCE_SCHEMA = "loop-view-source.v1"
_LOOP_PROJECTION_FILES = (
    "task_attempts.json",
    "stage_spine.json",
    "workflow_health.json",
)


@dataclass(frozen=True)
class LoopViewSource:
    events: list[tuple[int, ZfEvent]]
    source_seq: int
    fingerprint: str

    @property
    def cacheable(self) -> bool:
        return bool(self.fingerprint)


def _add_text(digest: Any, label: str, value: str) -> None:
    digest.update(label.encode("utf-8", "surrogateescape"))
    digest.update(b"\0")
    digest.update(value.encode("utf-8", "surrogateescape"))
    digest.update(b"\0")


def _add_file_identity(digest: Any, *, label: str, path: Path) -> bool:
    """Add an atomic-write-aware file identity without reading its body."""

    _add_text(digest, "path", label)
    try:
        stat = path.stat()
    except FileNotFoundError:
        _add_text(digest, "identity", "missing")
        return True
    except OSError:
        return False
    _add_text(
        digest,
        "identity",
        ":".join(
            str(value)
            for value in (
                stat.st_dev,
                stat.st_ino,
                stat.st_mode,
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
            )
        ),
    )
    return True


def _decoder_identity(config: Any | None) -> str:
    signer = build_event_signer(config, warn=False)
    signing = getattr(getattr(config, "security", None), "event_signing", None)
    allow_unsigned = bool(getattr(signing, "allow_unsigned_fallback", False))
    if signer is None:
        return f"unsigned:allow-legacy={int(allow_unsigned)}"
    return f"signed:{signer.cache_fingerprint()}:allow-legacy={int(allow_unsigned)}"


def loop_view_source_fingerprint(
    state_dir: Path,
    *,
    config: Any | None = None,
    project_root: Path | None = None,
) -> str:
    """Return a project-scoped, metadata-exact fingerprint of Loop inputs.

    EventLog writes use append, rename or atomic replacement.  Device, inode,
    size, mtime and ctime therefore distinguish append, replacement and
    rotation without reading a multi-megabyte body.  Loop sidecars and zf.yaml
    are also atomic-write state.  A before/after comparison around cache reads
    and source hydration closes concurrent-write races.
    """

    try:
        state_dir = Path(state_dir).resolve()
    except OSError:
        return ""
    digest = hashlib.sha256()
    _add_text(digest, "schema", _SOURCE_SCHEMA)
    _add_text(digest, "state-dir", str(state_dir))
    try:
        _add_text(digest, "decoder", _decoder_identity(config))
    except Exception:
        return ""

    try:
        segments = list_event_segments(state_dir)
    except OSError:
        return ""
    _add_text(digest, "event-segment-count", str(len(segments)))
    for segment in segments:
        if not _add_file_identity(
            digest,
            label=f"event:{segment.kind}:{segment.rel_path}",
            path=segment.path,
        ):
            return ""
    if not any(segment.kind == "active" for segment in segments):
        if not _add_file_identity(
            digest,
            label="event:active:events.jsonl",
            path=state_dir / "events.jsonl",
        ):
            return ""

    dependency_paths = list(
        (f"loop-projection:{name}", state_dir / "projections" / name)
        for name in _LOOP_PROJECTION_FILES
    )
    try:
        root = Path(project_root).resolve() if project_root is not None else None
    except OSError:
        return ""
    config_path = root / "zf.yaml" if root else state_dir / "__no_project_root__"
    dependency_paths.append(("control-plane:zf.yaml", config_path))
    for label, path in dependency_paths:
        if not _add_file_identity(digest, label=label, path=path):
            return ""
    return digest.hexdigest()


def _read_metric_events(state_dir: Path, config=None) -> list[tuple[int, ZfEvent]]:
    from zf.web.projections.metric_facts import refresh_events, events
    try:
        refresh_events(state_dir, config)
        return events(state_dir, include_malformed=True)
    except sqlite3.DatabaseError:
        # A disposable cache must not erase canonical Loop data when corrupt.
        return [(record.seq, record.event) for record in iter_event_records(state_dir, config=config)]


def read_stable_loop_view_source(
    state_dir: Path,
    *,
    config: Any | None = None,
    project_root: Path | None = None,
    attempts: int = 2,
) -> LoopViewSource:
    """Hydrate EventLog once per stable attempt and bind it to its inputs.

    If a writer moves the watermark during both bounded attempts, the returned
    snapshot remains usable for this response but is deliberately not cached.
    """

    state_dir = Path(state_dir)
    last_events: list[tuple[int, ZfEvent]] = []
    for _attempt in range(max(1, attempts)):
        before = loop_view_source_fingerprint(
            state_dir,
            config=config,
            project_root=project_root,
        )
        try:
            last_events = _read_metric_events(state_dir, config)
        except Exception:
            return LoopViewSource(events=[], source_seq=0, fingerprint="")
        after = loop_view_source_fingerprint(
            state_dir,
            config=config,
            project_root=project_root,
        )
        if before and before == after:
            source_seq = last_events[-1][0] if last_events else 0
            return LoopViewSource(
                events=last_events,
                source_seq=source_seq,
                fingerprint=after,
            )
    source_seq = last_events[-1][0] if last_events else 0
    return LoopViewSource(events=last_events, source_seq=source_seq, fingerprint="")
