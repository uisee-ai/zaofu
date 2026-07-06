"""RF-1: TaskEventIndex must replicate _payload_mentions semantics exactly."""
from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.long_horizon import (
    TaskEventIndex,
    _event_task_match,
    _events_for_task,
)


def _mk(payload, task_id=""):
    return ZfEvent(type="t", actor="a", task_id=task_id, payload=payload)


EVENTS = [
    _mk({"task_id": "T-1"}),                       # 值命中
    _mk({"T-1": "x"}),                              # key 命中(递归含 key)
    _mk({"nested": {"deep": ["T-1", 5]}}),          # 嵌套 list 命中
    _mk({"note": "prefix T-1 suffix"}),             # 子串——精确相等语义下不命中
    _mk({"id": "T-10"}),                            # T-1 是 T-10 的前缀——不命中
    _mk({}, task_id="T-1"),                         # 显式 task_id 字段
    _mk({"n": 7}),                                   # 非字符串叶子
    _mk({"n": "7"}),                                 # str(7) == "7" 命中 needle "7"
]


def test_index_matches_legacy_for_every_event_and_needle():
    idx = TaskEventIndex(EVENTS)
    for needle in ("T-1", "T-10", "7", "x", "prefix T-1 suffix", "missing"):
        legacy = _events_for_task(EVENTS, needle)
        via_index = idx.events_for_task(needle)
        assert [id(e) for e in legacy] == [id(e) for e in via_index], needle
        for i, event in enumerate(EVENTS):
            assert _event_task_match(event, needle) == idx.event_matches_task(i, needle)


def test_relevant_matches_freshness_filter():
    idx = TaskEventIndex(EVENTS)
    for task_id, actor in (("T-1", "a"), ("", "a"), ("T-10", ""), ("", "")):
        legacy = [
            e for e in EVENTS
            if (task_id and _event_task_match(e, task_id)) or (actor and e.actor == actor)
        ]
        assert [id(e) for e in legacy] == [id(e) for e in idx.relevant(task_id=task_id, actor=actor)]


def test_exact_equality_not_substring():
    idx = TaskEventIndex(EVENTS)
    hits = idx.events_for_task("T-1")
    assert EVENTS[3] not in hits  # "prefix T-1 suffix" 不是精确相等
    assert EVENTS[4] not in hits  # "T-10" 不含精确叶子 "T-1"
    assert EVENTS[0] in hits and EVENTS[1] in hits and EVENTS[2] in hits and EVENTS[5] in hits
