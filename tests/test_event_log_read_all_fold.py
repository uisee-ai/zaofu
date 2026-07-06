"""RF-11: EventLog.read_all append-fold(内核层增量折叠).

read_all 的 (mtime,size) 精确键缓存在每条 append 后全量重解码归档+active;
append-only + 归档不可变 ⇒ 归档清单不变且 active 只增长时只解码尾部。
这些测试钉住:fold ≡ 全量 rebuild、轮转触发全量、非 JSON 跳过语义一致。
"""

from pathlib import Path

import pytest

from zf.core.events.log import EventLog, _READ_ALL_CACHE, _READ_ALL_FOLD
from zf.core.events.model import ZfEvent


@pytest.fixture(autouse=True)
def _clear_caches():
    _READ_ALL_CACHE.clear()
    _READ_ALL_FOLD.clear()
    yield
    _READ_ALL_CACHE.clear()
    _READ_ALL_FOLD.clear()


@pytest.fixture()
def log(tmp_path: Path) -> EventLog:
    lg = EventLog(tmp_path / ".zf" / "events.jsonl")
    lg.path.parent.mkdir(parents=True)
    lg.append(ZfEvent(type="task.dispatched", actor="t", task_id="T1"))
    lg.append(ZfEvent(type="dev.build.done", actor="t", task_id="T1"))
    return lg


def _fresh_read(lg: EventLog) -> list[ZfEvent]:
    _READ_ALL_CACHE.clear()
    _READ_ALL_FOLD.clear()
    return lg.read_all()


def test_append_fold_equals_full_rebuild(log: EventLog) -> None:
    log.read_all()  # prime fold entry
    log.append(ZfEvent(type="verify.passed", actor="t", task_id="T2"))
    folded = log.read_all()
    fresh = _fresh_read(log)
    assert [(e.type, e.id, e.task_id) for e in folded] == [
        (e.type, e.id, e.task_id) for e in fresh
    ]
    assert folded[-1].type == "verify.passed"


def test_fold_decodes_only_tail(log: EventLog, monkeypatch) -> None:
    log.read_all()
    calls: list[int] = []
    orig = EventLog._parse_tail

    def spy(self, path, offset, size):
        out = orig(self, path, offset, size)
        calls.append(len(out[0]))
        return out

    monkeypatch.setattr(EventLog, "_parse_tail", spy)
    monkeypatch.setattr(
        EventLog, "_parse_file_consumed",
        lambda self, path: (_ for _ in ()).throw(AssertionError("full re-parse on append")),
    )
    log.append(ZfEvent(type="judge.passed", actor="t"))
    events = log.read_all()
    assert calls == [1], "exactly one tail decode of exactly one event"
    assert events[-1].type == "judge.passed"


def test_rotation_triggers_full_rebuild(log: EventLog) -> None:
    log.read_all()
    archive_dir = log.path.parent / "events"
    archive_dir.mkdir(exist_ok=True)
    log.path.rename(archive_dir / "2026-07-02-0001.jsonl")
    log.append(ZfEvent(type="run.started", actor="t"))
    events = log.read_all()
    fresh = _fresh_read(log)
    assert [e.type for e in events] == [e.type for e in fresh]
    assert [e.type for e in events] == ["task.dispatched", "dev.build.done", "run.started"]


def test_non_json_lines_skipped_same_as_full_parse(log: EventLog) -> None:
    log.read_all()
    with log.path.open("a") as fh:
        fh.write("this-is-not-json\n")
    log.append(ZfEvent(type="task.completed", actor="t"))
    folded = log.read_all()
    fresh = _fresh_read(log)
    assert [e.type for e in folded] == [e.type for e in fresh]
    assert "event.malformed" not in [e.type for e in folded]


def test_truncation_falls_back_to_full_rebuild(log: EventLog) -> None:
    log.read_all()
    # active 被截短(非 append-only 形状)→ 必须全量重建,不 fold
    lines = log.path.read_text().splitlines()
    log.path.write_text(lines[0] + "\n")
    events = log.read_all()
    assert [e.type for e in events] == ["task.dispatched"]
