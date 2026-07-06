"""EventLog — append-only JSONL event store with lazy date archival.

Layout (active+archive):

    .zf/events.jsonl           ← today's active file
    .zf/events/<date>.jsonl    ← historical archived days

Rotation happens lazily on append(): if active file's mtime is not
today, it gets moved to events/<mtime_day>.jsonl via the shared
rotate_if_needed helper (zf.core.state.rotation, G-ROT-0).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from zf.core.events.model import ZfEvent
from zf.core.state.locks import locked_path
from zf.core.state.rotation import list_archives, rotate_if_needed

if TYPE_CHECKING:
    from zf.core.security.signing import EventSigner


_READ_ALL_CACHE: dict[tuple, tuple] = {}
# R11-1 (2026-07-03): append-fold companion. _READ_ALL_CACHE is exact-key
# (mtime,size) so EVERY append invalidated it and forced a full re-decode of
# archives+active for all ~16 read_all consumers (metrics, channel projection,
# run archive, ...). events.jsonl is append-only and archives are immutable,
# so when the archive inventory is unchanged and the active file only grew we
# decode just the tail. Keyed by (path, signer, allow_unsigned) — stable
# across appends.
_READ_ALL_FOLD: dict[tuple, dict] = {}


class EventLog:
    def __init__(
        self,
        path: Path,
        *,
        signer: "EventSigner | None" = None,
        allow_unsigned: bool = False,
    ) -> None:
        self.path = path
        self.signer = signer
        # Read-path tolerance: when a signer is configured, plain
        # (non-envelope) lines are rejected as event.malformed unless the
        # project explicitly opted in to legacy tolerance. Without this,
        # appending one unsigned line bypasses signing entirely (I1).
        self.allow_unsigned = allow_unsigned
        self.index = self._build_index()

    def _build_index(self):
        # Lazy import to avoid a hard dependency cycle during minimal
        # imports; the index is a pure projection and is optional.
        try:
            from zf.core.events.index import EventIndex
        except ImportError:
            return None
        index_path = self.path.parent / "event_index.json"
        index = EventIndex(path=index_path)
        try:
            index.load()
        except Exception:
            pass
        return index

    @property
    def _archive_dir(self) -> Path:
        """Archive sibling directory: .zf/events/ for .zf/events.jsonl."""
        return self.path.parent / self.path.stem

    def append(self, event: ZfEvent) -> None:
        with locked_path(self.path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            rotate_if_needed(self.path, self._archive_dir)
            self._rotate_by_size_if_needed()
            line = self._encode(event)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        if self.index is not None:
            try:
                self.index.observe(event)
                # Flush at coarse cadence — every 5 observes — to keep
                # append latency low while still surfacing the index
                # file early enough that short-lived runs (e.g. tests,
                # E2E smoke) see a representative snapshot. Graceful
                # shutdown should call ``EventLog.close()`` for a final
                # flush. Best-effort: persistence failure is non-fatal
                # because rebuild is possible.
                count = len(self.index._event_by_id)
                if count == 1 or count % 5 == 0:
                    self.index.flush()
            except Exception:
                pass

    def close(self) -> None:
        """Best-effort flush of the in-process index to disk."""
        if self.index is not None:
            try:
                self.index.flush()
            except Exception:
                pass

    def _rotate_by_size_if_needed(self) -> bool:
        raw_limit = os.environ.get("ZF_EVENT_LOG_MAX_ACTIVE_BYTES", "").strip()
        if not raw_limit:
            return False
        try:
            max_bytes = int(raw_limit)
        except ValueError:
            return False
        if max_bytes <= 0 or not self.path.exists():
            return False
        try:
            stat = self.path.stat()
        except OSError:
            return False
        if stat.st_size < max_bytes:
            return False
        day = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
        self._archive_dir.mkdir(parents=True, exist_ok=True)
        ordinal = 1
        while True:
            archive_path = self._archive_dir / f"{day}-{ordinal:04d}{self.path.suffix}"
            if not archive_path.exists():
                break
            ordinal += 1
        self.path.rename(archive_path)
        return True

    def _encode(self, event: ZfEvent) -> str:
        event_json = event.to_json()
        if self.signer is None:
            return event_json
        sig = self.signer.sign(event_json)
        envelope = {"event": json.loads(event_json), "sig": sig}
        return json.dumps(envelope, ensure_ascii=False)

    def _decode(self, line: str) -> ZfEvent | None:
        """Decode one JSONL line. Returns None for non-JSON (silently
        skipped). Schema-invalid payloads (valid JSON but wrong shape)
        return a synthetic ``event.malformed`` ZfEvent in-memory so
        callers of ``read_all`` can surface the corruption; nothing is
        written back to disk, so repeated reads stay idempotent."""
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return None
        if isinstance(obj, dict) and "event" in obj and "sig" in obj:
            # Signed envelope. Verify if we have a signer; else accept as-is.
            event_dict = obj["event"]
            event_json = json.dumps(event_dict, ensure_ascii=False, sort_keys=False)
            if self.signer is not None and not self.signer.verify(event_json, obj["sig"]):
                # Try the canonical encoding the signer would produce
                fresh_json = ZfEvent.from_dict(event_dict).to_json()
                if not self.signer.verify(fresh_json, obj["sig"]):
                    return None
            try:
                return ZfEvent.from_dict(event_dict)
            except (TypeError, KeyError) as e:
                return self._malformed(line, e)
        # Legacy plain event — schema validation before constructing ZfEvent.
        if self.signer is not None and not self.allow_unsigned:
            # Fail-closed: a signed log must not accept unsigned lines,
            # otherwise injecting one plain `judge.passed` line defeats
            # the entire signing feature. Surfaced (not silently dropped)
            # so operators see the tamper signal.
            return self._malformed(
                line,
                ValueError("unsigned event line rejected: event signing is enabled"),
            )
        if not isinstance(obj, dict):
            return self._malformed(line, TypeError("top-level must be object"))
        if "type" not in obj or not isinstance(obj.get("type"), str):
            return self._malformed(line, TypeError("missing or non-string `type`"))
        payload = obj.get("payload")
        if payload is not None and not isinstance(payload, dict):
            return self._malformed(line, TypeError("`payload` must be a dict"))
        try:
            return ZfEvent.from_json(line)
        except (json.JSONDecodeError, TypeError) as e:
            return self._malformed(line, e)

    def decode_line(self, line: str) -> ZfEvent | None:
        """Decode one raw JSONL line using this log's signer.

        Raw-tail consumers such as EventWatcher and Web SSE use this
        instead of assuming every line is a plain event JSON object.
        """
        return self._decode(line.strip())

    @staticmethod
    def _malformed(line: str, error: Exception) -> ZfEvent:
        """Synthetic in-memory event representing a bad log line.

        Not written to disk: repeated reads generate the same synthetic
        event from the same bad line, keeping counts bounded and avoiding
        append-during-decode recursion.
        """
        return ZfEvent(
            type="event.malformed",
            actor="zf-cli",
            payload={
                "line": line[:200],
                "error": str(error),
            },
        )

    def current_offset(self) -> int:
        try:
            return self.path.stat().st_size
        except FileNotFoundError:
            return 0

    def read_from_offset(self, offset: int) -> tuple[list[ZfEvent], int]:
        """Return events appended since `offset`, plus the new end offset.

        Handles rotation: if the active file's current size is smaller than
        `offset` (because the old content got moved to an archive), we start
        from 0 of the new active file.
        """
        if not self.path.exists():
            return [], 0
        current_size = self.path.stat().st_size
        effective_offset = offset if offset <= current_size else 0
        with self.path.open("rb") as f:
            f.seek(effective_offset)
            data = f.read()
        # Only consume up to the last newline. A trailing line without a newline
        # is a partial write: a large event (e.g. a 16KB scan report) is flushed
        # in chunks, and a poll can read the file between chunks. Advancing the
        # offset past such a line drops the event from the live stream forever —
        # decode fails now and the next poll starts after it — even though a full
        # re-read still sees it (why the manifest projector and live reactor can
        # disagree). Leaving it unconsumed lets the next poll re-read it whole.
        last_newline = data.rfind(b"\n")
        if last_newline < 0:
            return [], effective_offset
        complete = data[: last_newline + 1]
        new_offset = effective_offset + len(complete)
        events: list[ZfEvent] = []
        for raw in complete.split(b"\n"):
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            ev = self._decode(line)
            if ev is not None:
                events.append(ev)
        return events, new_offset

    def _parse_file(self, path: Path) -> list[ZfEvent]:
        events, _consumed = self._parse_file_consumed(path)
        return events

    def _parse_file_consumed(self, path: Path) -> tuple[list[ZfEvent], int]:
        """Parse a log file; also return how many bytes were consumed.

        The consumed offset (last complete line boundary) lets read_all's
        fold cache decode only the appended tail on the next call."""
        if not path.exists():
            return [], 0
        try:
            snapshot_size = path.stat().st_size
        except FileNotFoundError:
            return [], 0
        try:
            with path.open("rb") as f:
                data = f.read(snapshot_size)
        except FileNotFoundError:
            return [], 0
        # Decode only the bytes present when the read began. If the active log
        # is being appended concurrently, this prevents readers from chasing a
        # moving EOF. A trailing newline-less row may be a partial write; leave
        # it for a later read, matching read_from_offset().
        last_newline = data.rfind(b"\n")
        if last_newline < 0:
            return [], 0
        data = data[: last_newline + 1]
        events: list[ZfEvent] = []
        for raw in data.split(b"\n"):
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            ev = self._decode(line)
            if ev is not None:
                events.append(ev)
        return events, len(data)

    def _parse_tail(self, path: Path, offset: int, snapshot_size: int) -> tuple[list[ZfEvent], int]:
        """Decode only bytes [offset, snapshot_size) — the appended tail."""
        try:
            with path.open("rb") as f:
                f.seek(offset)
                data = f.read(max(0, snapshot_size - offset))
        except (FileNotFoundError, OSError):
            return [], 0
        last_newline = data.rfind(b"\n")
        if last_newline < 0:
            return [], 0
        data = data[: last_newline + 1]
        events: list[ZfEvent] = []
        for raw in data.split(b"\n"):
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            ev = self._decode(line)
            if ev is not None:
                events.append(ev)
        return events, len(data)

    def read_all(self) -> list[ZfEvent]:
        """Read archives (oldest first) + today's active file.

        Process-wide mtime+size cache: snapshot composition fans out into
        30+ projections that each construct an EventLog and re-read the same
        append-only file (2026-06-11 profile: 15 full reads / 91k json.loads
        per snapshot). events.jsonl is append-only, so (mtime_ns, size) is a
        sound freshness key. Signer identity joins the key because _decode
        verifies with a specific key; otherwise one signer can poison another
        signer's read cache for the same file. Returns a fresh shallow copy so
        callers may mutate the list.
        """
        try:
            stat = self.path.stat()
            if self.signer is None:
                signer_cache_key = "unsigned"
            else:
                fingerprint = getattr(self.signer, "cache_fingerprint", None)
                signer_cache_key = (
                    fingerprint()
                    if callable(fingerprint)
                    else f"signer:{id(self.signer)}"
                )
            key = (
                str(self.path.resolve()),
                stat.st_mtime_ns,
                stat.st_size,
                signer_cache_key,
                self.allow_unsigned,
            )
        except OSError:
            key = None
        if key is not None:
            cached = _READ_ALL_CACHE.get(key)
            if cached is not None:
                return list(cached)
        try:
            archive_inventory = tuple(
                (str(f), f.stat().st_size)
                for f in list_archives(self._archive_dir, suffix=".jsonl")
            )
        except OSError:
            archive_inventory = None
        fold_key = None if key is None else (key[0], key[3], key[4])
        if (
            fold_key is not None
            and archive_inventory is not None
        ):
            entry = _READ_ALL_FOLD.get(fold_key)
            if (
                entry is not None
                and entry["archives"] == archive_inventory
                and key[2] >= entry["consumed"]
            ):
                # Append-only tail: decode just the new bytes.
                tail, tail_bytes = self._parse_tail(
                    self.path, entry["consumed"], key[2],
                )
                folded = entry["events"] + tuple(tail) if tail else entry["events"]
                # Replace the entry wholesale (single dict assignment) instead
                # of mutating in place — a concurrent reader must never see
                # events updated but consumed not yet (it would re-fold the
                # same tail and duplicate rows).
                _READ_ALL_FOLD[fold_key] = {
                    "archives": entry["archives"],
                    "consumed": entry["consumed"] + tail_bytes,
                    "events": folded,
                }
                if len(_READ_ALL_CACHE) > 8:
                    _READ_ALL_CACHE.clear()
                _READ_ALL_CACHE[key] = folded
                return list(folded)
        events: list[ZfEvent] = []
        # Archives in chronological order (oldest first)
        for f in list_archives(self._archive_dir, suffix=".jsonl"):
            events.extend(self._parse_file(f))
        # Today's active file
        active_events, consumed = self._parse_file_consumed(self.path)
        events.extend(active_events)
        if key is not None:
            if len(_READ_ALL_CACHE) > 8:
                _READ_ALL_CACHE.clear()
            events_tuple = tuple(events)
            _READ_ALL_CACHE[key] = events_tuple
            if archive_inventory is not None:
                while len(_READ_ALL_FOLD) > 8:
                    _READ_ALL_FOLD.pop(next(iter(_READ_ALL_FOLD)))
                _READ_ALL_FOLD[fold_key] = {
                    "archives": archive_inventory,
                    "consumed": consumed,
                    "events": events_tuple,
                }
        return events

    def events_for_task(self, task_id: str, *, limit: int | None = None) -> list[ZfEvent]:
        """Return events for one task using the task index when complete.

        Large long-horizon runs commonly render task timelines and recovery
        packets by task id. The index path avoids repeatedly scanning the
        whole event log; fallback preserves correctness whenever the cache is
        missing, stale, or incomplete.
        """
        if self.index is not None:
            try:
                cached = self.index.events_for_task(task_id, limit=limit)
            except Exception:
                cached = None
            if cached is not None:
                return cached
        events = [event for event in self.read_all() if event.task_id == task_id]
        if limit is not None:
            return events[-limit:]
        return events

    def read_days(self, last_days: int = 1) -> list[ZfEvent]:
        """Read only the last N days of events.

        last_days=1 → today (active) only
        last_days=N → today + most recent (N-1) archive days
        """
        events: list[ZfEvent] = []
        if last_days > 1:
            for f in list_archives(
                self._archive_dir,
                last_days=last_days - 1,
                suffix=".jsonl",
            ):
                events.extend(self._parse_file(f))
        events.extend(self._parse_file(self.path))
        return events

    def get_causation_chain(self, event_id: str) -> list[ZfEvent]:
        """Return the causal ancestors of an event in chronological order.

        Walks the causation_id chain backwards from the target event
        until it hits an event with causation_id=None or the chain
        becomes unresolvable. Returns [oldest_ancestor, ..., target].

        Returns an empty list if event_id is not found. Has a cycle
        guard: self-references or loops are truncated safely.
        """
        all_events = self.read_all()
        by_id = {e.id: e for e in all_events}
        if event_id not in by_id:
            return []
        chain: list[ZfEvent] = []
        seen_ids: set[str] = set()
        current_id: str | None = event_id
        while current_id and current_id in by_id and current_id not in seen_ids:
            seen_ids.add(current_id)
            chain.append(by_id[current_id])
            current_id = by_id[current_id].causation_id
        return list(reversed(chain))

    def query(
        self,
        *,
        type: str | None = None,
        event_type: str | None = None,
        task_id: str | None = None,
        actor: str | None = None,
        last: int | None = None,
    ) -> list[ZfEvent]:
        if type is not None and event_type is not None and type != event_type:
            raise ValueError("type and event_type filters must match when both are set")
        effective_type = type if type is not None else event_type
        if task_id is not None:
            events = self.events_for_task(task_id)
        else:
            events = self.read_all()
        if effective_type is not None:
            events = [e for e in events if e.type == effective_type]
        if actor is not None:
            events = [e for e in events if e.actor == actor]
        if last is not None:
            events = events[-last:]
        return events

    def count(self) -> int:
        return len(self.read_all())
