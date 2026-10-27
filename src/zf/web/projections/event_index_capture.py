"""Reuse verified full index records in the bounded Web event cache."""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.events.segments import EventRecord
from zf.web.projections import events as cache


def capture_cold_index_events(
    state_dir: Path, config: ZfConfig | None, records: Iterable[EventRecord],
) -> tuple[Iterable[EventRecord], dict[str, Any] | None]:
    """Capture a complete index pass for the existing bounded decoded cache.

    Only a source-stable, successfully committed full index can be admitted.
    A partially resumed pass cannot describe the complete event generation.
    """
    key = str(state_dir)
    try:
        fingerprint = cache._event_log_fingerprint(state_dir, config)
        archives = cache._archive_inventory(state_dir)
    except OSError:
        return records, None
    cost = (fingerprint[0] + sum(size for _rel, size in archives)) * cache._DECODED_COST_FACTOR
    with cache._EVENTS_WITH_SEQ_CACHE_LOCK:
        with cache._DERIVED_CACHE_LOCK:
            used = sum(entry["cost"] for entry in cache._EVENTS_WITH_SEQ_CACHE.values())
            used += sum(entry["cost"] for entry in cache._DERIVED_CACHE.values())
            if (key in cache._EVENTS_WITH_SEQ_CACHE or len(cache._EVENTS_WITH_SEQ_CACHE) >= cache._EVENTS_WITH_SEQ_CACHE_MAX
                    or cost + used > cache._CACHE_BUDGET_BYTES):
                return records, None
            available = cache._CACHE_BUDGET_BYTES - used
    capture = {"fp": fingerprint, "archives": archives, "cost": cost, "events": [],
               "segment_ranges": [], "consumed": 0, "raw_bytes": 0, "enabled": True}

    def observe() -> Iterator[EventRecord]:
        for record in records:
            if capture["enabled"]:
                capture["raw_bytes"] += record.raw_length
                # The source might grow after the initial inventory. Stop
                # retaining objects when the configured budget would be exceeded.
                if capture["raw_bytes"] * cache._DECODED_COST_FACTOR > available:
                    capture["events"].clear()
                    capture["segment_ranges"].clear()
                    capture["enabled"] = False
                else:
                    events = capture["events"]
                    ranges = capture["segment_ranges"]
                    index = len(events)
                    events.append((record.seq, record.event))
                    if ranges and ranges[-1][0] == record.raw_segment:
                        segment, start, _ = ranges[-1]
                        ranges[-1] = (segment, start, index + 1)
                    else:
                        ranges.append((record.raw_segment, index, index + 1))
                    if record.raw_segment == "events.jsonl":
                        capture["consumed"] = record.raw_offset + record.raw_length
            yield record

    return observe(), capture


def admit_cold_index_events(state_dir: Path, config: ZfConfig | None, capture: dict[str, Any] | None) -> None:
    """Publish only the unchanged decoded generation after the SQLite commit."""
    if not capture or not capture["enabled"]:
        return
    key = str(state_dir)
    try:
        if capture["fp"] != cache._event_log_fingerprint(state_dir, config):
            return
        with cache._EVENTS_WITH_SEQ_CACHE_LOCK:
            with cache._DERIVED_CACHE_LOCK:
                used = sum(entry["cost"] for entry in cache._EVENTS_WITH_SEQ_CACHE.values())
                used += sum(entry["cost"] for entry in cache._DERIVED_CACHE.values())
                if (key in cache._EVENTS_WITH_SEQ_CACHE or len(cache._EVENTS_WITH_SEQ_CACHE) >= cache._EVENTS_WITH_SEQ_CACHE_MAX
                        or capture["cost"] + used > cache._CACHE_BUDGET_BYTES
                        or capture["fp"] != cache._event_log_fingerprint(state_dir, config)):
                    return
                # A decoded entry may have been evicted independently of its
                # derived entries. Never fold those against a new epoch-zero
                # list whose prefix objects are unrelated.
                for derived_key in [name for name in cache._DERIVED_CACHE if name.startswith(key + "::")]:
                    cache._DERIVED_CACHE.pop(derived_key)
                cache._EVENTS_WITH_SEQ_CACHE[key] = {
                    "segment_ranges": capture["segment_ranges"], "fp": capture["fp"], "epoch": next(cache._EVENT_EPOCH),
                    "events": capture["events"], "archives": capture["archives"],
                    "consumed": capture["consumed"], "cost": capture["cost"], "stamp": next(cache._CACHE_STAMP),
                }
    except OSError:
        return  # Failed admission never affects the committed read model.
    cache._enforce_cache_budget()
