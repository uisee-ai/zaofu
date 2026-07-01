"""Bounded id-set for per-process event dedupe.

2026-06-10 review: ``Orchestrator._processed_event_ids`` and
``_promoted_causations`` grew one entry per event with no bound — a
zero-touch run accumulates memory linearly for days. Dedupe only needs
to cover the recent window (the offset cursor already prevents old
replay), so evicting the oldest ids is safe.
"""

from __future__ import annotations

from collections import OrderedDict


class BoundedIdSet:
    """Set-like membership container that evicts its oldest entries.

    Supports the two operations the dedupe call sites use: ``add`` and
    ``in``. Insertion order is the eviction order (FIFO).
    """

    def __init__(self, max_size: int = 50_000) -> None:
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        self._max_size = max_size
        self._items: OrderedDict[str, None] = OrderedDict()

    def add(self, item: str) -> None:
        if item in self._items:
            return
        self._items[item] = None
        while len(self._items) > self._max_size:
            self._items.popitem(last=False)

    def __contains__(self, item: object) -> bool:
        return item in self._items

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)
