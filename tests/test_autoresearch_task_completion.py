"""Completion metrics use admitted pipeline facts without weakening Goal waits."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.e2e import run_mixed
from zf.autoresearch.orchestrator import summarize_events
from zf.autoresearch.task_completion import count_completed_tasks


def _integrated(task_id: str = "T1") -> dict:
    return {
        "type": "task.done", "task_id": task_id, "origin": "kernel",
        "payload": {"authority": "task_integration_receipt"},
    }


def test_completion_counts_task_identities_across_versions() -> None:
    assert count_completed_tasks([
        _integrated(), _integrated(), _integrated("T2"),
        {"type": "task.status_changed", "task_id": "T1", "payload": {"to": "done"}},
        {"type": "task.status_changed", "payload": {"to": "done"}},
    ]) == 3


@pytest.mark.parametrize("event", [
    {"type": "dev.build.done", "task_id": "T1"},
    {**_integrated(), "origin": "worker"},
    {**_integrated(), "payload": {"authority": "compat_projection"}},
    {**_integrated(), "task_id": None},
    {**_integrated(), "payload": "invalid"},
])
def test_completion_ignores_noncanonical_facts(event: dict) -> None:
    assert count_completed_tasks([event]) == 0


def _ledger(root: Path, rows: list[dict]) -> None:
    state = root / ".zf"
    state.mkdir()
    (state / "events.jsonl").write_text("\n".join(map(json.dumps, rows)) + "\n")


def test_pipeline_done_is_visible_to_runner_and_outer_summary(tmp_path: Path) -> None:
    _ledger(tmp_path, [_integrated()])
    assert run_mixed.wait_for_done(tmp_path, 1, 1).done == 1
    assert summarize_events(tmp_path, 1)["tasks_done"] == 1


def test_task_completion_does_not_replace_goal_completion(tmp_path: Path, monkeypatch) -> None:
    _ledger(tmp_path, [_integrated(), {"type": "loop.stopped"}])
    clock = iter([0, 0, 0, 2, 2])
    monkeypatch.setattr(run_mixed.time, "time", lambda: next(clock))
    monkeypatch.setattr(run_mixed.time, "sleep", lambda _: None)
    result = run_mixed.wait_for_done(tmp_path, 1, 1, require_run_goal=True)
    assert result.status == "timeout"
    assert result.done == 1


def test_pipeline_goal_terminal_passes_with_actual_done_count(tmp_path: Path) -> None:
    _ledger(tmp_path, [_integrated(), {"type": "run.goal.completed"}])
    result = run_mixed.wait_for_done(tmp_path, 1, 1, require_run_goal=True)
    assert result.status == "passed"
    assert result.done == 1
