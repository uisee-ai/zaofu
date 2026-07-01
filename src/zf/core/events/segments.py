"""Segment helpers for append-only ``events.jsonl``.

The event log remains the source of truth. This module only gives read-side
consumers a stable view over archived segments plus the active file, including
raw byte offsets that let projections hydrate an exact event row later.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from zf.core.config.schema import ZfConfig
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.rotation import list_archives


@dataclass(frozen=True)
class EventSegment:
    path: Path
    rel_path: str
    kind: str
    ordinal: int
    size: int
    mtime_ns: int

    def to_dict(self) -> dict:
        data = asdict(self)
        data["path"] = str(self.path)
        return data


@dataclass(frozen=True)
class EventManifest:
    schema_version: str
    state_dir: str
    active: str
    segments: list[EventSegment]
    total_bytes: int
    digest: str

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "state_dir": self.state_dir,
            "active": self.active,
            "segments": [segment.to_dict() for segment in self.segments],
            "total_bytes": self.total_bytes,
            "digest": self.digest,
        }


@dataclass(frozen=True)
class EventRecord:
    seq: int
    event: ZfEvent
    raw_segment: str
    raw_offset: int
    raw_length: int
    raw_line: str


@dataclass(frozen=True)
class EventSegmentCursor:
    schema_version: str
    segment: str
    byte_offset: int
    line_no: int
    last_event_id: str
    archive_manifest_digest: str

    def to_dict(self) -> dict:
        return asdict(self)


def list_event_segments(state_dir: Path) -> list[EventSegment]:
    """Return event segments oldest-first, with active ``events.jsonl`` last."""

    state_dir = Path(state_dir)
    active = state_dir / "events.jsonl"
    archive_dir = state_dir / "events"
    archive_paths: list[Path] = []
    archive_paths.extend(list_archives(archive_dir, suffix=".jsonl"))
    future_archive_dir = archive_dir / "archive"
    if future_archive_dir.exists():
        archive_paths.extend(sorted(f for f in future_archive_dir.glob("*.jsonl") if f.is_file()))

    paths: list[tuple[str, Path]] = [("archive", path) for path in sorted(set(archive_paths))]
    if active.exists():
        paths.append(("active", active))

    segments: list[EventSegment] = []
    for ordinal, (kind, path) in enumerate(paths):
        try:
            stat = path.stat()
        except OSError:
            continue
        try:
            rel_path = path.relative_to(state_dir).as_posix()
        except ValueError:
            rel_path = path.as_posix()
        segments.append(EventSegment(
            path=path,
            rel_path=rel_path,
            kind=kind,
            ordinal=ordinal,
            size=int(stat.st_size),
            mtime_ns=int(stat.st_mtime_ns),
        ))
    return segments


def build_event_manifest(state_dir: Path) -> EventManifest:
    segments = list_event_segments(state_dir)
    digest = hashlib.sha256()
    total_bytes = 0
    for segment in segments:
        total_bytes += segment.size
        digest.update(
            f"{segment.ordinal}\0{segment.rel_path}\0{segment.kind}\0"
            f"{segment.size}\0{segment.mtime_ns}\n".encode("utf-8")
        )
    return EventManifest(
        schema_version="event-segment-manifest.v1",
        state_dir=str(Path(state_dir)),
        active="events.jsonl",
        segments=segments,
        total_bytes=total_bytes,
        digest=digest.hexdigest(),
    )


def write_event_manifest(state_dir: Path) -> Path:
    manifest = build_event_manifest(state_dir)
    path = Path(state_dir) / "events" / "manifest.json"
    atomic_write_text(
        path,
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return path


def current_event_cursor(
    state_dir: Path,
    *,
    config: ZfConfig | None = None,
) -> EventSegmentCursor:
    manifest = build_event_manifest(state_dir)
    last_record: EventRecord | None = None
    line_no = 0
    for record in iter_event_records(state_dir, config=config):
        last_record = record
        line_no += 1
    if last_record is None:
        return EventSegmentCursor(
            schema_version="event-segment-cursor.v1",
            segment="",
            byte_offset=0,
            line_no=0,
            last_event_id="",
            archive_manifest_digest=manifest.digest,
        )
    return EventSegmentCursor(
        schema_version="event-segment-cursor.v1",
        segment=last_record.raw_segment,
        byte_offset=last_record.raw_offset + last_record.raw_length,
        line_no=line_no,
        last_event_id=last_record.event.id,
        archive_manifest_digest=manifest.digest,
    )


def cursor_is_stale(state_dir: Path, cursor: EventSegmentCursor | dict) -> bool:
    digest = (
        cursor.archive_manifest_digest
        if isinstance(cursor, EventSegmentCursor)
        else str(cursor.get("archive_manifest_digest") or "")
    )
    return digest != build_event_manifest(state_dir).digest


def iter_event_records(
    state_dir: Path,
    *,
    config: ZfConfig | None = None,
    start_seq: int = 0,
) -> Iterable[EventRecord]:
    """Yield decoded events with global seq and raw segment offsets.

    ``start_seq`` skips already-projected logical rows. Malformed JSON rows
    still consume a seq, matching the Web event table's historical behavior.
    """

    event_log = event_log_from_project(state_dir, config=config)
    seq = 0
    for segment in list_event_segments(state_dir):
        try:
            with segment.path.open("rb") as fh:
                data = fh.read(segment.size)
        except OSError:
            continue
        last_newline = data.rfind(b"\n")
        if last_newline < 0:
            continue
        offset = 0
        for raw in data[: last_newline + 1].splitlines(keepends=True):
            raw_length = len(raw)
            line = raw.decode("utf-8", "replace").strip()
            if line:
                seq += 1
                if seq > start_seq:
                    event = event_log.decode_line(line)
                    if event is None:
                        event = ZfEvent(
                            type="event.malformed",
                            actor="segments",
                            payload={
                                "line": line[:200],
                                "error": "unable to decode event line",
                            },
                        )
                    yield EventRecord(
                        seq=seq,
                        event=event,
                        raw_segment=segment.rel_path,
                        raw_offset=offset,
                        raw_length=raw_length,
                        raw_line=line,
                    )
            offset += raw_length


def count_event_records(state_dir: Path) -> int:
    count = 0
    for segment in list_event_segments(state_dir):
        try:
            with segment.path.open("rb") as fh:
                data = fh.read(segment.size)
        except OSError:
            continue
        last_newline = data.rfind(b"\n")
        if last_newline >= 0:
            count += sum(1 for line in data[: last_newline + 1].splitlines() if line.strip())
    return count


def hydrate_event_at(
    state_dir: Path,
    *,
    segment: str,
    offset: int,
    length: int,
    config: ZfConfig | None = None,
) -> ZfEvent | None:
    path = Path(state_dir) / segment
    try:
        with path.open("rb") as fh:
            fh.seek(max(0, int(offset)))
            raw = fh.read(max(0, int(length)))
    except OSError:
        return None
    line = raw.decode("utf-8", "replace").strip()
    if not line:
        return None
    return event_log_from_project(state_dir, config=config).decode_line(line)
