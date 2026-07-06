"""R12-3a: web-api-timing.jsonl 尺寸轮转.

我们为追查 38MB/天事件日志加的观测,自己没有轮转 —— 长驻 server 上会成为
下一个无界增长文件。钉住:超阈值滚动到 .1、新文件从零开始、既有记录保留一代。
"""

from pathlib import Path

import zf.web.perf as perf


def test_rotation_rolls_oversized_log(tmp_path: Path, monkeypatch) -> None:
    sd = tmp_path / ".zf"
    monkeypatch.setattr(perf, "_TIMING_LOG_MAX_BYTES", 500)
    for i in range(20):
        perf.record_timing(
            sd, method="GET", path=f"/api/x/{i}", route="/api/x/{id}",
            status_code=200, elapsed_ms=1.0,
        )
    log = perf.timing_log_path(sd)
    rolled = log.with_suffix(log.suffix + ".1")
    assert rolled.exists(), "oversized log must roll to .1"
    assert log.stat().st_size < 500 + 400, "active log restarts near-empty after roll"
    # 契约:保留一代(.1)——多次滚动丢弃更早代是设计(16MB 阈值下每代≈数万条)。
    # 最近写入的记录必须存活于 活跃文件 或 .1。
    survivors = [
        line for p in (log, rolled) if p.exists()
        for line in p.read_text().splitlines() if line.strip()
    ]
    assert any('"/api/x/19"' in line for line in survivors), "most recent record must survive"
    assert not log.with_suffix(log.suffix + ".2").exists(), "only one rolled generation"


def test_no_rotation_under_threshold(tmp_path: Path) -> None:
    sd = tmp_path / ".zf"
    perf.record_timing(
        sd, method="GET", path="/api/y", route="/api/y",
        status_code=200, elapsed_ms=2.0,
    )
    log = perf.timing_log_path(sd)
    assert log.exists()
    assert not log.with_suffix(log.suffix + ".1").exists()


def test_summarize_still_works_after_rotation(tmp_path: Path, monkeypatch) -> None:
    sd = tmp_path / ".zf"
    monkeypatch.setattr(perf, "_TIMING_LOG_MAX_BYTES", 400)
    for i in range(10):
        perf.record_timing(
            sd, method="GET", path="/api/z", route="/api/z",
            status_code=200, elapsed_ms=float(i),
        )
    summary = perf.summarize_timings(sd)
    assert summary["count"] >= 1
