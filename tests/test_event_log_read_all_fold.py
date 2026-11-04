"""RF-11: EventLog.read_all append-fold(内核层增量折叠).

read_all 的 (mtime,size) 精确键缓存在每条 append 后全量重解码归档+active;
append-only + 归档不可变 ⇒ 归档清单不变且 active 只增长时只解码尾部。
这些测试钉住:fold ≡ 全量 rebuild、轮转触发全量、非 JSON 跳过语义一致。
"""

import os
from pathlib import Path

import pytest

from zf.core.events.log import EventLog, _READ_ACTIVE_FOLD, _READ_ALL_CACHE, _READ_ALL_FOLD
from zf.core.events.model import ZfEvent


@pytest.fixture(autouse=True)
def _clear_caches():
    _READ_ACTIVE_FOLD.clear()
    _READ_ALL_CACHE.clear()
    _READ_ALL_FOLD.clear()
    yield
    _READ_ACTIVE_FOLD.clear()
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


def test_read_days_folds_only_new_active_events(log: EventLog, monkeypatch) -> None:
    before = log.read_days(1)
    calls = []
    original = EventLog._parse_tail

    def tail_spy(self, path, offset, size):
        calls.append((offset, size))
        return original(self, path, offset, size)

    monkeypatch.setattr(EventLog, "_parse_tail", tail_spy)
    monkeypatch.setattr(
        EventLog, "_parse_file_consumed",
        lambda self, path: (_ for _ in ()).throw(AssertionError("active log fully reparsed")),
    )
    assert [e.id for e in log.read_days(1)] == [e.id for e in before]
    log.append(ZfEvent(type="verify.passed", actor="t"))
    assert [e.type for e in log.read_days(1)] == [
        "task.dispatched", "dev.build.done", "verify.passed",
    ]
    assert len(calls) == 1


def test_read_days_cache_rebuilds_on_replacement_and_truncation(log: EventLog) -> None:
    log.read_days(1)
    old = log.path.with_suffix(".old")
    log.path.rename(old)
    log.append(ZfEvent(type="run.started", actor="t"))
    log.append(ZfEvent(type="run.updated", actor="t"))
    assert [e.type for e in log.read_days(1)] == ["run.started", "run.updated"]
    line = log.path.read_text().splitlines()[0]
    log.path.write_text(line + "\n")
    assert [e.type for e in log.read_days(1)] == ["run.started"]


def test_read_days_detects_truncate_and_regrow_between_reads(log: EventLog) -> None:
    log.read_days(1)
    previous_size = log.path.stat().st_size
    replacement = [ZfEvent(type="replacement", payload={"data": "x" * previous_size})]
    log.path.write_text("".join(event.to_json() + "\n" for event in replacement))
    assert log.path.stat().st_size > previous_size
    assert [event.type for event in log.read_days(1)] == ["replacement"]


def test_read_days_handles_rotation_during_tail_read(log: EventLog, monkeypatch) -> None:
    log.read_days(1)
    log.append(ZfEvent(type="before.rotation"))
    original = EventLog._parse_tail

    def rotate_before_read(self, path, offset, size):
        path.rename(path.with_suffix(".old"))
        path.write_text(ZfEvent(type="after.rotation").to_json() + "\n")
        return original(self, path, offset, size)

    monkeypatch.setattr(EventLog, "_parse_tail", rotate_before_read)
    assert [e.type for e in log.read_days(1)] == ["after.rotation"]


def test_read_days_fold_waits_for_complete_line(log: EventLog) -> None:
    log.read_days(1)
    line = ZfEvent(type="verify.passed").to_json()
    with log.path.open("a", encoding="utf-8") as handle:
        handle.write(line[:30])
    assert [e.type for e in log.read_days(1)] == ["task.dispatched", "dev.build.done"]
    with log.path.open("a", encoding="utf-8") as handle:
        handle.write(line[30:] + "\n")
    assert [e.type for e in log.read_days(1)][-1] == "verify.passed"


def test_read_days_cache_isolated_by_signing_key(tmp_path: Path) -> None:
    from zf.core.security.signing import EventSigner

    path = tmp_path / "events.jsonl"
    good = EventLog(path, signer=EventSigner(b"good"))
    bad = EventLog(path, signer=EventSigner(b"bad"))
    good.append(ZfEvent(type="judge.passed"))
    assert [e.type for e in good.read_days(1)] == ["judge.passed"]
    assert bad.read_days(1) == []


def test_signed_read_days_detects_same_metadata_tampering(tmp_path: Path) -> None:
    from zf.core.security.signing import EventSigner

    path = tmp_path / "events.jsonl"
    log = EventLog(path, signer=EventSigner(b"secret"))
    log.append(ZfEvent(type="judge.passed"))
    assert [e.type for e in log.read_days(1)] == ["judge.passed"]
    before = path.stat()
    path.write_text(path.read_text().replace("judge.passed", "judge.failed"))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_size == before.st_size
    assert log.read_days(1) == []


def test_read_days_retries_a_transient_empty_tail(log: EventLog, monkeypatch) -> None:
    log.read_days(1)
    log.append(ZfEvent(type="verify.passed"))
    original = EventLog._parse_tail
    calls = 0

    def transient(self, path, offset, size):
        nonlocal calls
        calls += 1
        return ([], 0) if calls == 1 else original(self, path, offset, size)

    monkeypatch.setattr(EventLog, "_parse_tail", transient)
    assert [e.type for e in log.read_days(1)][-1] == "dev.build.done"
    assert [e.type for e in log.read_days(1)][-1] == "verify.passed"


def test_read_days_cache_does_not_share_mutable_occurrences(log: EventLog) -> None:
    log.append(ZfEvent(type="nested", payload={"items": [{"value": "original"}]}))
    first = log.read_days(1)
    first[-1].type = "changed"
    first[-1].payload["items"][0]["value"] = "changed"
    again = log.read_days(1)[-1]
    assert again.type == "nested"
    assert again.payload["items"][0]["value"] == "original"


def test_parse_snapshot_defers_concurrent_append(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "events.jsonl"
    before = ZfEvent(type="before", payload={"message": "完整"})
    after = ZfEvent(type="after")
    path.write_text(before.to_json() + "\n", encoding="utf-8")
    original_size = path.stat().st_size
    original_open = Path.open
    appended = False

    def open_with_append(self, *args, **kwargs):
        nonlocal appended
        if self == path and args and args[0] == "rb" and not appended:
            appended = True
            with original_open(self, "ab") as stream:
                stream.write((after.to_json() + "\n").encode())
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_with_append)
    log = EventLog(path)
    rows, consumed = log._parse_file_consumed(path)
    assert rows == [before]
    assert consumed == original_size
    assert [event.type for event in log._parse_file(path)] == ["before", "after"]


def test_signed_archive_is_reverified_on_each_read(tmp_path: Path) -> None:
    from zf.core.security.signing import EventSigner

    path = tmp_path / "events.jsonl"
    log = EventLog(path, signer=EventSigner(b"secret"))
    log.append(ZfEvent(type="judge.passed"))
    archive_dir = tmp_path / "events"
    archive_dir.mkdir()
    archive = archive_dir / "2026-11-03.jsonl"
    path.rename(archive)
    assert [event.type for event in log.read_days(2)] == ["judge.passed"]
    before = archive.stat()
    archive.write_text(archive.read_text().replace("judge.passed", "judge.failed"))
    os.utime(archive, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert archive.stat().st_size == before.st_size
    assert log.read_days(2) == []
