from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.e2e import run_mixed
from tests.e2e.run_mixed import (
    _commit_initialized_flow_baseline,
    _scan_first_fatal_event,
    prepare_flow_source,
    reset_state,
    submit_flow,
    wait_for_done,
)


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


def test_reset_state_supports_fresh_worktree_without_state_dir(
    tmp_path: Path,
) -> None:
    reset_state(tmp_path)

    state_dir = tmp_path / ".zf"
    assert (state_dir / "events.jsonl").read_text(encoding="utf-8") == ""
    assert (state_dir / "kanban.json").read_text(encoding="utf-8") == "[]"
    assert (state_dir / "feature_list.json").read_text(encoding="utf-8") == "[]"


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


def test_scan_defers_pane_dead_when_bounded_retry_was_requested(
    tmp_path: Path,
) -> None:
    _write_events(tmp_path, [
        {
            "type": "orchestrator.dispatch_failed",
            "id": "evt-failed",
            "ts": "2026-08-11T00:00:00+00:00",
            "payload": {
                "dead_reason": "pane_dead",
                "trigger_event_id": "evt-trigger",
            },
        },
        {
            "type": "orchestrator.dispatch.retry_requested",
            "payload": {
                "source": "dispatch_failure_recovery",
                "trigger_event_id": "evt-trigger",
                "max_attempts": 1,
            },
        },
    ])

    fatal, offset = _scan_first_fatal_event(
        tmp_path / ".zf" / "events.jsonl",
        done=0,
        expected=1,
    )

    assert fatal is None
    assert offset > 0


def test_scan_keeps_recent_pane_dead_open_for_retry_pairing(
    tmp_path: Path,
) -> None:
    _write_events(tmp_path, [
        {
            "type": "orchestrator.dispatch_failed",
            "id": "evt-failed",
            "ts": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "dead_reason": "pane_dead",
                "trigger_event_id": "evt-trigger",
            },
        },
    ])

    fatal, offset = _scan_first_fatal_event(
        tmp_path / ".zf" / "events.jsonl",
        done=0,
        expected=1,
    )

    assert fatal is None
    assert offset == 0


def test_wait_for_flow_requires_run_goal_completion(tmp_path: Path) -> None:
    _write_events(tmp_path, [
        {"type": "task.status_changed", "payload": {"to": "done"}},
        {"type": "run.goal.completed", "payload": {"run_id": "run-1"}},
    ])

    result = wait_for_done(
        tmp_path,
        expected=1,
        timeout_s=60,
        require_run_goal=True,
    )

    assert result.status == "passed"


@pytest.mark.parametrize("ignored_docs", [False, True])
def test_prepare_flow_source_commits_only_controller_input(
    tmp_path: Path,
    ignored_docs: bool,
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "autoresearch@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Autoresearch E2E"],
        cwd=tmp_path,
        check=True,
    )

    if ignored_docs:
        (tmp_path / ".gitignore").write_text("docs/\n", encoding="utf-8")
    (tmp_path / "unrelated.txt").write_text("another session's staged work\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "unrelated.txt"], cwd=tmp_path, check=True)

    request_id = prepare_flow_source(
        tmp_path,
        flow_kind="prd",
        source_ref="docs/prd/autoresearch.md",
        seeds=["实现一个最小恢复验证。"],
    )

    tracked = subprocess.run(
        ["git", "show", "--pretty=", "--name-only", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert request_id.startswith("autoresearch-")
    assert tracked == ["docs/prd/autoresearch.md"]
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=tmp_path,
        check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    assert staged == ["unrelated.txt"]


def test_submit_flow_uses_production_intake_clarify_submit_entry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    commands: list[list[str]] = []

    def _record(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr(run_mixed, "_run", _record)

    assert submit_flow(
        tmp_path,
        flow_kind="prd",
        source_ref="docs/prd/autoresearch.md",
        target_root=".",
        seeds=["目标"],
        request_id="autoresearch-test",
    )
    assert [command[1:3] for command in commands] == [
        ["flow", "intake"],
        ["flow", "clarify"],
        ["flow", "submit"],
    ]


def test_initialized_flow_baseline_commits_only_managed_paths(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "AGENTS.md").write_text("before\n", encoding="utf-8")
    (tmp_path / "zf.yaml").write_text("version: '1.0'\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "--", "AGENTS.md", "zf.yaml"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "base"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "AGENTS.md").write_text("after\n", encoding="utf-8")
    (tmp_path / "zf.yaml").write_text("version: '1.0'\nproject: {}\n", encoding="utf-8")

    commit = _commit_initialized_flow_baseline(tmp_path)

    tracked = subprocess.run(
        ["git", "show", "--pretty=", "--name-only", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert len(commit) == 40
    assert tracked == ["AGENTS.md", "zf.yaml"]
    assert not subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=no"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


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
