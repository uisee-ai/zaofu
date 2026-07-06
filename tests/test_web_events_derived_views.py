"""RF-10: fingerprint-memoized derived event views (read_days / event_ref_kv).

The web snapshot used to decode the event log ~12x per build (each projection
re-read it) and re-walk every payload per _refs_from_events caller. These
tests pin the sharing contract:

- events_read_days returns the cached list while the log fingerprint is
  unchanged, and rebuilds after an append.
- _refs_from_events with state_dir (event_ref_kv fast path) is
  result-identical to the direct per-event collect, including the
  identity-check fallback for foreign event objects.
"""

from pathlib import Path

import pytest

from zf.core.events.log import EventLog, ZfEvent
from zf.web.projections.events import (
    _events_with_seq,
    event_ref_kv,
    events_read_days,
)
from zf.web.projections.summaries import _refs_from_events


def _emit(state_dir: Path, etype: str, payload: dict, task_id: str = "T1") -> None:
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type=etype, actor="test", task_id=task_id, payload=payload))


@pytest.fixture()
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    _emit(sd, "task.dispatched", {"branch": "wip/t1", "fanout_id": "f-1"})
    _emit(sd, "verify.child.completed", {"candidate_ref": "cand-9", "commit": "abc1234"})
    return sd


def test_events_read_days_memoized_until_append(state_dir: Path) -> None:
    first = events_read_days(state_dir, 1)
    assert [e.type for e in first] == ["task.dispatched", "verify.child.completed"]
    assert events_read_days(state_dir, 1) is first

    _emit(state_dir, "task.completed", {})
    rebuilt = events_read_days(state_dir, 1)
    assert rebuilt is not first
    assert [e.type for e in rebuilt][-1] == "task.completed"


def test_refs_fast_path_matches_direct_collect(state_dir: Path) -> None:
    events = list(_events_with_seq(state_dir))
    direct = _refs_from_events(events)
    fast = _refs_from_events(events, state_dir=state_dir)
    assert fast == direct
    assert fast["branch"] == "wip/t1"
    assert fast["candidate_ref"] == "cand-9"
    assert fast["commit"] == "abc1234"


def test_refs_fast_path_identity_fallback_for_foreign_events(state_dir: Path) -> None:
    # Same seqs, different objects (e.g. a caller that re-parsed the log):
    # the seq lookup must not serve another object's refs blindly.
    event_ref_kv(state_dir)  # prime the kv map
    foreign = [
        (seq, ZfEvent(type=e.type, actor=e.actor, task_id=e.task_id, payload={"branch": "other"}))
        for seq, e in _events_with_seq(state_dir)
    ]
    refs = _refs_from_events(foreign, state_dir=state_dir)
    assert refs["branch"] == "other"


# ---- R6-1: append-fold(增量折叠) ----

def _clear_event_caches() -> None:
    import zf.web.projections.events as ev

    ev._EVENTS_WITH_SEQ_CACHE.clear()
    ev._DERIVED_CACHE.clear()


def test_append_fold_shares_prefix_and_keeps_epoch(state_dir: Path) -> None:
    import zf.web.projections.events as ev

    _clear_event_caches()
    before, epoch0, _ = ev._events_state(state_dir)
    _emit(state_dir, "task.completed", {"commit": "def5678"})
    after, epoch1, _ = ev._events_state(state_dir)
    assert epoch1 == epoch0, "append must fold, not rebuild"
    assert len(after) == len(before) + 1
    for (s0, e0), (s1, e1) in zip(before, after):
        assert s0 == s1 and e0 is e1, "folded prefix must be the same objects"
    assert after[-1][0] == before[-1][0] + 1
    assert after[-1][1].type == "task.completed"


def test_rotation_triggers_full_rebuild(state_dir: Path) -> None:
    import zf.web.projections.events as ev

    _clear_event_caches()
    _, epoch0, _ = ev._events_state(state_dir)
    # 模拟归档轮转:活跃段整体搬去 events/<date>.jsonl,活跃文件重新开始
    archive_dir = state_dir / "events"
    archive_dir.mkdir(exist_ok=True)
    (state_dir / "events.jsonl").rename(archive_dir / "2026-07-01.jsonl")
    _emit(state_dir, "run.started", {})
    events, epoch1, _ = ev._events_state(state_dir)
    assert epoch1 == epoch0 + 1, "archive change must bump epoch (full rebuild)"
    assert [e.type for _, e in events][-1] == "run.started"
    assert len(events) == 3  # 归档 2 条 + 新活跃 1 条,seq 连续
    assert [s for s, _ in events] == [1, 2, 3]


def test_folded_derived_views_equal_fresh_rebuild(state_dir: Path) -> None:
    import zf.web.projections.events as ev

    _clear_event_caches()
    # prime 所有派生视图
    ev.event_ref_kv(state_dir)
    ev.payload_search_texts(state_dir)
    ev.task_event_index(state_dir)
    ev.events_read_days(state_dir, 1)
    _emit(state_dir, "verify.child.completed", {"candidate_ref": "cand-10", "child_id": "c1"}, task_id="T2")
    folded_kv = ev.event_ref_kv(state_dir)
    folded_texts = ev.payload_search_texts(state_dir)
    folded_index = ev.task_event_index(state_dir)
    folded_days = ev.events_read_days(state_dir, 1)

    _clear_event_caches()  # 全量重建对照
    fresh_kv = ev.event_ref_kv(state_dir)
    fresh_texts = ev.payload_search_texts(state_dir)
    fresh_index = ev.task_event_index(state_dir)
    fresh_days = ev.events_read_days(state_dir, 1)

    assert folded_texts == fresh_texts
    assert set(folded_kv) == set(fresh_kv)
    for seq in fresh_kv:
        assert folded_kv[seq][1] == fresh_kv[seq][1]
    assert [e.type for e in folded_days] == [e.type for e in fresh_days]
    assert [e.id for e in folded_days] == [e.id for e in fresh_days]
    assert folded_index.events_for_task("T2") and (
        [e.id for e in folded_index.events_for_task("T2")]
        == [e.id for e in fresh_index.events_for_task("T2")]
    )


def test_read_days_fold_drops_non_json_placeholder(state_dir: Path) -> None:
    import zf.web.projections.events as ev

    _clear_event_caches()
    ev.events_read_days(state_dir, 1)
    with (state_dir / "events.jsonl").open("a") as fh:
        fh.write("not-json-at-all\n")
    _emit(state_dir, "task.completed", {})
    folded = ev.events_read_days(state_dir, 1)
    # _parse_file 静默跳过非 JSON 行;fold 必须同样不带 segments 占位
    assert "event.malformed" not in [e.type for e in folded]
    assert folded[-1].type == "task.completed"
    # 但 events 表(_events_with_seq)保留占位——两种语义各自成立
    types = [e.type for _, e in ev._events_with_seq(state_dir)]
    assert "event.malformed" in types


def test_cache_budget_evicts_oldest(monkeypatch, tmp_path: Path) -> None:
    """R6-5: 超预算时跨缓存驱逐最老条目,最新条目保留。"""
    import zf.web.projections.events as ev

    _clear_event_caches()
    dirs = []
    for name in ("a", "b", "c"):
        sd = tmp_path / name / ".zf"
        sd.mkdir(parents=True)
        _emit(sd, "task.dispatched", {"branch": f"wip/{name}", "pad": "x" * 200})
        dirs.append(sd)
    # 预算压到只够 ~1 个条目(每条目 cost ≈ 文件字节 × 14)
    one_cost = (dirs[0] / "events.jsonl").stat().st_size * ev._DECODED_COST_FACTOR
    monkeypatch.setattr(ev, "_CACHE_BUDGET_BYTES", int(one_cost * 1.5))
    for sd in dirs:
        ev._events_with_seq(sd)
    assert len(ev._EVENTS_WITH_SEQ_CACHE) < 3, "old entries must be evicted"
    assert str(dirs[-1]) in ev._EVENTS_WITH_SEQ_CACHE, "newest entry must survive"
    # 被逐条目仍可重建(功能只慢不丢)
    assert [e.type for _, e in ev._events_with_seq(dirs[0])] == ["task.dispatched"]


# ---- RF-8: event_topology_kv / fanout_seq_maps 共享视图 ----

def test_topology_kv_fold_equals_rebuild(state_dir: Path) -> None:
    import zf.web.projections.events as ev

    _clear_event_caches()
    ev.event_topology_kv(state_dir)
    _emit(state_dir, "fanout.started", {"fanout_id": "f-9", "topology": "reader", "stage_id": "s1"})
    folded = ev.event_topology_kv(state_dir)
    _clear_event_caches()
    fresh = ev.event_topology_kv(state_dir)
    assert set(folded) == set(fresh)
    for seq in fresh:
        assert folded[seq][1] == fresh[seq][1]
    last = folded[max(folded)]
    assert last[1]["fanout_id"] == "f-9" and last[1]["topology"] == "reader"


def test_fanout_seq_maps_fold_and_parity(state_dir: Path) -> None:
    import zf.web.projections.events as ev

    _clear_event_caches()
    _emit(state_dir, "fanout.started", {"fanout_id": "f-1"})
    _emit(state_dir, "verify.child.completed", {"fanout_id": "f-1"})
    last, started = ev.fanout_seq_maps(state_dir)
    assert started["f-1"] < last["f-1"], "started 记 fanout.started 的 seq,last 记最大 seq"
    # fold:append 后 last 前进、started 不变
    _emit(state_dir, "fanout.child.dispatched", {"fanout_id": "f-1"})
    last2, started2 = ev.fanout_seq_maps(state_dir)
    assert last2["f-1"] == last["f-1"] + 1
    assert started2["f-1"] == started["f-1"]
    # 与全量 rebuild 等价
    _clear_event_caches()
    last3, started3 = ev.fanout_seq_maps(state_dir)
    assert (last3, started3) == (last2, started2)
    # 深层 payload 的 fanout_id 不算(top-level get 语义,与原扫描一致)
    _emit(state_dir, "x.y", {"nested": {"fanout_id": "f-deep"}})
    last4, _ = ev.fanout_seq_maps(state_dir)
    assert "f-deep" not in last4


def test_fanouts_projection_uses_shared_view_consistently(state_dir: Path) -> None:
    """投影级等价:_fanouts 走共享视图与清缓存全量重建输出一致。"""
    import zf.web.projections.events as ev
    from zf.web.projections.fanouts import _fanouts

    _clear_event_caches()
    _emit(state_dir, "fanout.started", {"fanout_id": "f-2", "topology": "writer", "child_id": "c1"})
    _emit(state_dir, "verify.child.completed", {"fanout_id": "f-2", "child_id": "c1", "status": "completed"})
    via_view = _fanouts(state_dir)
    _clear_event_caches()
    fresh = _fanouts(state_dir)
    assert via_view == fresh
    assert via_view and via_view[0]["fanout_id"] == "f-2"
    assert via_view[0]["topology"] == "writer"
