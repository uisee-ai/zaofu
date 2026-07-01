"""Lightweight in-process index for events.jsonl high-frequency lookups.

``EventWriter._event_by_id`` / ``_latest_task_event`` and
``cli/hook_recv._resolve_causation`` all call ``EventLog.read_all()`` at
append-or-hook frequency. For a long-running project this is O(N) per
call and degrades roughly to O(N²) over a day.

This module adds an in-process index that ``EventLog`` updates on
``append`` and reads on lookup. The persisted JSON projection is
optional and intentionally only trusted for id maps. Full event payloads
in ``event_index.json`` are compatibility/debug output, not an
authorization surface; cold-start lookups without in-memory payloads
fall back to ``events.jsonl`` scans.

Indexed lookups:
    * ``lookup_event_by_id(event_id) -> ZfEvent | None``
    * ``lookup_latest_event_by_task_id(task_id) -> ZfEvent | None``
    * ``lookup_events_by_task_id(task_id, limit) -> list[ZfEvent] | None``
    * ``lookup_latest_dispatch_for_actor(actor) -> ZfEvent | None``
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.core.state.atomic_io import atomic_write_text


_DISPATCH_EVENT_TYPES = frozenset({
    "task.dispatched",
    "fanout.child.dispatched",
    # B3 (R25 ISSUE-005): synth dispatches must register like children,
    # or the synth worker's hooks resolve no causation and go orphan.
    "fanout.synth.dispatched",
})


_INDEX_FILE_CACHE: dict[tuple, dict] = {}


@dataclass
class EventIndex:
    """In-memory + on-disk projection of the most useful event lookups.

    The index is a *cache*, not a source of truth. ``events.jsonl``
    remains the canonical log. Any failure to persist or load the
    projection downgrades to legacy ``read_all`` scans.
    """

    path: Path | None = None
    _event_by_id: dict[str, dict] = field(default_factory=dict)
    _latest_by_task: dict[str, str] = field(default_factory=dict)
    _event_ids_by_task: dict[str, list[str]] = field(default_factory=dict)
    _latest_dispatch_by_actor: dict[str, str] = field(default_factory=dict)
    _dirty: bool = False
    _max_entries: int = 50_000
    _max_task_events_per_task: int = 5_000

    def observe(self, event: ZfEvent) -> None:
        """Update in-memory index for a newly appended event.

        Stores the full event dict so cheap lookups can reconstruct a
        ``ZfEvent`` without re-reading ``events.jsonl``. The cost is
        bounded by ``_max_entries`` (drops oldest on overflow).
        """
        if not getattr(event, "id", None):
            return
        self._event_by_id[event.id] = asdict(event)
        if event.task_id:
            self._latest_by_task[event.task_id] = event.id
            task_event_ids = self._event_ids_by_task.setdefault(event.task_id, [])
            if not task_event_ids or task_event_ids[-1] != event.id:
                task_event_ids.append(event.id)
            if len(task_event_ids) > self._max_task_events_per_task:
                del task_event_ids[:-self._max_task_events_per_task]
        if event.type in _DISPATCH_EVENT_TYPES:
            for actor in _dispatch_actor_keys(event):
                self._latest_dispatch_by_actor[actor] = event.id
        if len(self._event_by_id) > self._max_entries:
            excess = len(self._event_by_id) - self._max_entries
            for _ in range(excess):
                self._event_by_id.pop(next(iter(self._event_by_id)), None)
        self._dirty = True

    def lookup(self, event_id: str) -> dict | None:
        return self._event_by_id.get(event_id)

    def lookup_event(self, event_id: str) -> ZfEvent | None:
        data = self._event_by_id.get(event_id)
        if data is None:
            return None
        try:
            return ZfEvent.from_dict(dict(data))
        except (TypeError, KeyError):
            return None

    def latest_event_for_task(self, task_id: str) -> ZfEvent | None:
        event_id = self._latest_by_task.get(task_id)
        if event_id is None:
            return None
        return self.lookup_event(event_id)

    def events_for_task(self, task_id: str, *, limit: int | None = None) -> list[ZfEvent] | None:
        """Return cached task events in chronological order.

        ``None`` means the cache is incomplete or unavailable; callers
        should fall back to scanning ``events.jsonl``. An empty list means
        the task is known to have no cached events.
        """
        event_ids = self._event_ids_by_task.get(task_id)
        if event_ids is None:
            return None
        selected = event_ids[-limit:] if limit is not None else event_ids
        events: list[ZfEvent] = []
        for event_id in selected:
            event = self.lookup_event(event_id)
            if event is None:
                return None
            events.append(event)
        return events

    def latest_dispatch_event_for_actor(self, actor: str) -> ZfEvent | None:
        event_id = self._latest_dispatch_by_actor.get(actor)
        if event_id is None:
            return None
        return self.lookup_event(event_id)

    def latest_for_task(self, task_id: str) -> str | None:
        return self._latest_by_task.get(task_id)

    def latest_dispatch_for_actor(self, actor: str) -> str | None:
        return self._latest_dispatch_by_actor.get(actor)

    # ---- persistence ----

    def load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        # Same fan-out pathology as EventLog.read_all (see log.py): every
        # projection re-parses event_index.json. Cache the parsed payload by
        # (path, mtime_ns, size).
        try:
            stat = self.path.stat()
            key = (str(self.path.resolve()), stat.st_mtime_ns, stat.st_size)
        except OSError:
            key = None
        if key is not None and key in _INDEX_FILE_CACHE:
            data = _INDEX_FILE_CACHE[key]
        else:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return
            if key is not None:
                if len(_INDEX_FILE_CACHE) > 8:
                    _INDEX_FILE_CACHE.clear()
                _INDEX_FILE_CACHE[key] = data
        if not isinstance(data, dict):
            return
        # Do not hydrate full events from the projection file. events.jsonl is
        # the append-only source of truth; a tampered event_index.json must not
        # be able to authorize forged facts for task timelines, latest-task
        # lookup, or hook causation. Persisted event_by_id remains a legacy
        # debug/compatibility output written by flush(), but readers only trust
        # in-process events observed from EventLog.append().
        latest_task = data.get("latest_event_by_task_id") or {}
        if isinstance(latest_task, dict):
            self._latest_by_task = {str(k): str(v) for k, v in latest_task.items()}
        task_events = data.get("event_ids_by_task_id") or {}
        if isinstance(task_events, dict):
            self._event_ids_by_task = {
                str(k): [str(item) for item in v if isinstance(item, str)]
                for k, v in task_events.items()
                if isinstance(v, list)
            }
        latest_dispatch = data.get("latest_dispatch_by_actor") or {}
        if isinstance(latest_dispatch, dict):
            self._latest_dispatch_by_actor = {
                str(k): str(v) for k, v in latest_dispatch.items()
            }

    def flush(self) -> None:
        if self.path is None or not self._dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Read-merge-write: every flush re-reads id maps from disk so
            # concurrent writers (e.g. ``zf start`` watcher + ``zf emit`` +
            # ``zf stop``) converge rather than clobbering each other. Disk
            # full-event payloads are deliberately not trusted or merged.
            disk_latest_task: dict = {}
            disk_task_events: dict = {}
            disk_latest_dispatch: dict = {}
            if self.path.exists():
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        disk_latest_task = (
                            data.get("latest_event_by_task_id") or {}
                        )
                        disk_task_events = data.get("event_ids_by_task_id") or {}
                        disk_latest_dispatch = (
                            data.get("latest_dispatch_by_actor") or {}
                        )
                except (OSError, json.JSONDecodeError):
                    pass
            merged_events = dict(self._event_by_id)
            # 2026-06-10 review: ``observe`` caps the in-memory map but the
            # persisted projection can grow forever on long-horizon runs.
            # Apply the same cap to the observed events we are willing to
            # write. Id-map entries that point to non-resident payloads fall
            # back to the event-log scan on lookup.
            if len(merged_events) > self._max_entries:
                ordered = sorted(
                    merged_events.items(),
                    key=lambda kv: (kv[1] or {}).get("ts", "")
                    if isinstance(kv[1], dict) else "",
                )
                merged_events = dict(ordered[-self._max_entries:])

            def _pick_latest(
                disk_map: dict, mem_map: dict, events: dict,
            ) -> dict:
                out = dict(disk_map)
                for key, ev_id in mem_map.items():
                    if key not in out:
                        out[key] = ev_id
                        continue
                    incumbent = out[key]
                    incumbent_ts = (events.get(incumbent) or {}).get("ts", "")
                    candidate_ts = (events.get(ev_id) or {}).get("ts", "")
                    # Lexicographic compare on ISO-8601 ts strings = chronological.
                    if candidate_ts > incumbent_ts:
                        out[key] = ev_id
                return out

            merged_latest_task = _pick_latest(
                disk_latest_task, self._latest_by_task, merged_events,
            )

            def _merge_task_events(disk_map: dict, mem_map: dict) -> dict:
                out: dict[str, list[str]] = {}
                for task_id, values in disk_map.items():
                    if isinstance(values, list):
                        out[str(task_id)] = [
                            str(item) for item in values if isinstance(item, str)
                        ]
                for task_id, values in mem_map.items():
                    base = out.setdefault(str(task_id), [])
                    seen = set(base)
                    for event_id in values:
                        event_id = str(event_id)
                        if event_id in merged_events and event_id not in seen:
                            base.append(event_id)
                            seen.add(event_id)
                    base.sort(key=lambda ev_id: (merged_events.get(ev_id) or {}).get("ts", ""))
                    if len(base) > self._max_task_events_per_task:
                        del base[:-self._max_task_events_per_task]
                return out

            merged_task_events = _merge_task_events(
                disk_task_events,
                self._event_ids_by_task,
            )
            merged_latest_dispatch = _pick_latest(
                disk_latest_dispatch,
                self._latest_dispatch_by_actor,
                merged_events,
            )
            # Refresh in-memory id maps so the next ``observe`` sees the
            # union — without this, two processes can take turns wiping each
            # other's task/dispatch ids. Full event payloads remain only the
            # in-process events this instance has observed from append().
            self._event_by_id = merged_events
            self._latest_by_task = merged_latest_task
            self._event_ids_by_task = merged_task_events
            self._latest_dispatch_by_actor = merged_latest_dispatch
            payload = {
                "event_by_id": merged_events,
                "latest_event_by_task_id": merged_latest_task,
                "event_ids_by_task_id": merged_task_events,
                "latest_dispatch_by_actor": merged_latest_dispatch,
            }
            atomic_write_text(
                self.path,
                json.dumps(payload, ensure_ascii=False) + "\n",
            )
            self._dirty = False
        except OSError:
            # Persistence failure is non-fatal — the index keeps working
            # in memory; next cold start will rebuild from event tail.
            return

    def rebuild_from_events(self, events) -> int:
        self._event_by_id.clear()
        self._latest_by_task.clear()
        self._event_ids_by_task.clear()
        self._latest_dispatch_by_actor.clear()
        count = 0
        for event in events:
            self.observe(event)
            count += 1
        self.flush()
        return count


def _dispatch_actor_keys(event: ZfEvent) -> tuple[str, ...]:
    """Return actor keys that may later emit provider hooks for a dispatch."""
    keys: list[str] = []
    payload = event.payload if isinstance(event.payload, dict) else {}
    for value in (
        payload.get("assignee"),
        payload.get("role"),
        payload.get("role_instance"),
        event.actor,
    ):
        actor = str(value or "").strip()
        if actor and actor not in keys:
            keys.append(actor)
    return tuple(keys)
