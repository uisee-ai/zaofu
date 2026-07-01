"""feishu W2: per-scope debounce queue (doc 99 §4.2)."""

from __future__ import annotations

import threading
import time

from zf.integrations.feishu.pending_queue import PendingQueue


def _collector():
    flushes: list[tuple[str, list]] = []
    done = threading.Event()

    def on_flush(scope, batch):
        flushes.append((scope, batch))
        done.set()

    return flushes, done, on_flush


def test_rapid_messages_collapse_into_one_batch():
    flushes, done, on_flush = _collector()
    q = PendingQueue(80, on_flush)
    for i in range(3):
        q.push("oc_x", {"text": f"m{i}"})
    assert done.wait(2.0)
    time.sleep(0.05)
    # 3 rapid pushes → exactly one flush carrying all three
    assert len(flushes) == 1
    scope, batch = flushes[0]
    assert scope == "oc_x"
    assert [m["text"] for m in batch] == ["m0", "m1", "m2"]


def test_separate_scopes_flush_independently():
    flushes, _done, on_flush = _collector()
    q = PendingQueue(60, on_flush)
    q.push("oc_a", {"text": "a"})
    q.push("oc_b", {"text": "b"})
    time.sleep(0.3)
    scopes = sorted(s for s, _ in flushes)
    assert scopes == ["oc_a", "oc_b"]


def test_block_holds_flush_until_unblock():
    flushes, _done, on_flush = _collector()
    q = PendingQueue(60, on_flush)
    q.push("oc_x", {"text": "first"})
    time.sleep(0.12)  # first batch flushes
    assert len(flushes) == 1

    # simulate a run in progress: block, then messages accumulate silently
    q.block("oc_x")
    q.push("oc_x", {"text": "during-run-1"})
    q.push("oc_x", {"text": "during-run-2"})
    time.sleep(0.2)
    assert len(flushes) == 1  # still held — no flush while blocked

    q.unblock("oc_x")
    time.sleep(0.2)
    assert len(flushes) == 2  # accumulated msgs flush as one fresh batch
    _scope, batch = flushes[1]
    assert [m["text"] for m in batch] == ["during-run-1", "during-run-2"]


def test_cancel_all_drops_pending_timers():
    flushes, _done, on_flush = _collector()
    q = PendingQueue(120, on_flush)
    q.push("oc_x", {"text": "x"})
    q.cancel_all()
    time.sleep(0.25)
    assert flushes == []
