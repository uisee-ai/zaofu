from __future__ import annotations

import json
from pathlib import Path

from tests.e2e.run_mixed import _scan_first_fatal_event, wait_for_done


def _write_events(worktree: Path, events: list[dict | str]) -> None:
    state_dir = worktree / ".zf"
    state_dir.mkdir()
    lines = [
        event if isinstance(event, str) else json.dumps(event)
        for event in events
    ]
    (state_dir / "events.jsonl").write_text("\n".join(lines) + "\n")


def test_wait_for_done_passes_when_expected_done_seen(tmp_path: Path) -> None:
    _write_events(tmp_path, [
        {"type": "task.status_changed", "payload": {"to": "done"}},
    ])

    result = wait_for_done(tmp_path, expected=1, timeout_s=60)

    assert result.status == "passed"
    assert result.done == 1


def test_wait_for_done_fails_fast_on_dispatch_failed(tmp_path: Path) -> None:
    _write_events(tmp_path, [
        {
            "type": "orchestrator.dispatch_failed",
            "task_id": "T1",
            "payload": {"reason": "terminal move failed"},
        },
    ])

    result = wait_for_done(tmp_path, expected=1, timeout_s=60)

    assert result.status == "fatal"
    assert result.fatal_event is not None
    assert result.fatal_event["type"] == "orchestrator.dispatch_failed"


def test_wait_for_done_treats_early_loop_stopped_as_fatal(tmp_path: Path) -> None:
    _write_events(tmp_path, [
        {"type": "loop.stopped", "payload": {"reason": "idle"}},
    ])

    result = wait_for_done(tmp_path, expected=1, timeout_s=60)

    assert result.status == "fatal"
    assert result.fatal_event is not None
    assert result.fatal_event["type"] == "loop.stopped"


def test_wait_for_done_fails_fast_on_task_orphaned(tmp_path: Path) -> None:
    _write_events(tmp_path, [
        {
            "type": "task.orphaned",
            "task_id": "TASK-A",
            "payload": {"role": "dev"},
        },
    ])

    result = wait_for_done(tmp_path, expected=1, timeout_s=60)

    assert result.status == "fatal"
    assert result.fatal_event is not None
    assert result.fatal_event["type"] == "task.orphaned"


def test_scan_ignores_recoverable_worker_stuck(tmp_path: Path) -> None:
    _write_events(tmp_path, [
        {
            "type": "worker.stuck",
            "actor": "dev-1",
            "payload": {"role": "dev"},
        },
    ])

    fatal, offset = _scan_first_fatal_event(
        tmp_path / ".zf" / "events.jsonl",
        done=0,
        expected=1,
    )

    assert fatal is None
    assert offset > 0


def test_wait_for_done_fails_fast_on_worker_stuck_recovery_failed(
    tmp_path: Path,
) -> None:
    _write_events(tmp_path, [
        {
            "type": "worker.stuck.recovery_failed",
            "actor": "dev-1",
            "payload": {"role": "dev"},
        },
    ])

    result = wait_for_done(tmp_path, expected=1, timeout_s=60)

    assert result.status == "fatal"
    assert result.fatal_event is not None
    assert result.fatal_event["type"] == "worker.stuck.recovery_failed"


def test_wait_for_done_ignores_malformed_lines(tmp_path: Path) -> None:
    _write_events(tmp_path, [
        "{not json",
        {"type": "task.status_changed", "payload": {"to": "done"}},
    ])

    result = wait_for_done(tmp_path, expected=1, timeout_s=60)

    assert result.status == "passed"
