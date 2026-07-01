from __future__ import annotations

import argparse
from pathlib import Path

from zf.cli.recover import _run_fanout_terminal
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


def _state(tmp_path: Path) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    return state_dir


def _args(state_dir: Path, *, apply: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        state_dir=state_dir,
        fanout_id="fanout-final-judge",
        child_id="judge",
        result_event_id="evt-judge-result",
        status="completed",
        stage_id="final-judge",
        trace_id="trace-r5",
        terminal_event="judge.passed",
        aggregate=True,
        apply=apply,
        as_json=True,
    )


def test_recover_fanout_terminal_preview_does_not_append_events(
    tmp_path: Path,
    capsys,
) -> None:
    state_dir = _state(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="fanout.started", payload={"fanout_id": "fanout-final-judge"}))

    rc = _run_fanout_terminal(_args(state_dir, apply=False))

    assert rc == 0
    out = capsys.readouterr().out
    assert "fanout.child.completed" in out
    assert [event.type for event in log.read_all()] == ["fanout.started"]


def test_recover_fanout_terminal_apply_only_appends_terminal_events(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="fanout.started", payload={"fanout_id": "fanout-final-judge"}))

    rc = _run_fanout_terminal(_args(state_dir, apply=True))

    assert rc == 0
    types = [event.type for event in log.read_all()]
    assert types == [
        "fanout.started",
        "fanout.child.completed",
        "fanout.aggregate.completed",
    ]
    assert "task_map.ready" not in types

    second = _run_fanout_terminal(_args(state_dir, apply=True))
    assert second == 0
    assert [event.type for event in log.read_all()] == types
