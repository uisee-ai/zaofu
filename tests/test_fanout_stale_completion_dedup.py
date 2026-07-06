"""RF-7A: a re-emitting lane must not be amplified into stale_completion spam.

r10 evidence: one superseded child re-sent verify.child.completed every ~7s
(new event id each time, no ack loop); the identity-stale sweep emitted a fresh
fanout.child.stale_completion per source event — 1954 rows for one child.
The record is per (fanout_id, child_id): once a stale completion is on the log
for that pair, later re-emissions stay silent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "kanban.json").write_text("[]", encoding="utf-8")
    (sd / "events.jsonl").write_text("", encoding="utf-8")
    return sd


def _orchestrator_guard(sd: Path):
    """Bind the guard against a minimal object (it only uses event_log)."""
    from zf.runtime.orchestrator_fanout import FanoutCoordinationMixin

    class _Host:
        event_log = EventLog(sd / "events.jsonl")

    host = _Host()
    return lambda **kw: FanoutCoordinationMixin._fanout_stale_completion_recorded(host, **kw)


def test_second_reemission_is_deduped_per_child(state_dir: Path):
    log = EventLog(state_dir / "events.jsonl")
    recorded = _orchestrator_guard(state_dir)

    # 第一条 completed 尚无记录 → 允许发射
    assert recorded(fanout_id="f-1", child_id="c-1", source_event_id="evt-a") is False

    log.append(ZfEvent(
        type="fanout.child.stale_completion", actor="zf-cli",
        payload={"fanout_id": "f-1", "child_id": "c-1",
                 "result_event_id": "evt-a", "reason": "superseded_by_latest_fanout"},
    ))

    # lane 重发(新事件 id)→ 旧键(per source_event_id)会放行造成放大;新键必须挡住
    assert recorded(fanout_id="f-1", child_id="c-1", source_event_id="evt-b") is True
    # 不同 child / 不同 fanout 不受影响
    assert recorded(fanout_id="f-1", child_id="c-2", source_event_id="evt-c") is False
    assert recorded(fanout_id="f-2", child_id="c-1", source_event_id="evt-d") is False
