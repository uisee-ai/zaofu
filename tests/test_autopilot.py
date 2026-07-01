from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from zf.cli.main import main
from zf.core.config.schema import AutopilotConfig, ProjectConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.autopilot import AUTOPILOT_PROPOSAL_EVENT, run_autopilot_tick


def _state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "kanban.json").write_text("[]\n", encoding="utf-8")
    return sd


def _config(**kwargs) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="test"),
        autopilot=AutopilotConfig(**kwargs),
    )


def test_autopilot_disabled_is_noop(tmp_path: Path):
    sd = _state_dir(tmp_path)
    TaskStore(sd / "kanban.json").add(Task(id="TASK-1", title="stale", status="in_progress"))

    result = run_autopilot_tick(sd, config=_config(enabled=False))

    assert result.enabled is False
    assert result.created_count == 0
    assert [
        event for event in EventLog(sd / "events.jsonl").read_all()
        if event.type == AUTOPILOT_PROPOSAL_EVENT
    ] == []


def test_autopilot_creates_stale_proposal_and_dedupes(tmp_path: Path):
    sd = _state_dir(tmp_path)
    now = datetime(2026, 5, 9, 12, tzinfo=timezone.utc)
    old = (now - timedelta(hours=3)).isoformat()
    TaskStore(sd / "kanban.json").add(Task(
        id="TASK-ST",
        title="stale task",
        status="in_progress",
        created_at=old,
    ))
    EventLog(sd / "events.jsonl").append(ZfEvent(
        type="task.created",
        task_id="TASK-ST",
        ts=old,
    ))

    cfg = _config(enabled=True, stale_after_hours=1)
    first = run_autopilot_tick(sd, config=cfg, now=now)
    second = run_autopilot_tick(sd, config=cfg, now=now)

    assert first.created_count == 1
    assert first.created[0].kind == "stale_task"
    assert first.created[0].action_proposal["action"] == "update-task"
    assert second.created_count == 0
    assert second.skipped_duplicates == 1
    assert TaskStore(sd / "kanban.json").get("TASK-ST").status == "in_progress"
    events = EventLog(sd / "events.jsonl").read_all()
    assert sum(event.type == AUTOPILOT_PROPOSAL_EVENT for event in events) == 1


def test_autopilot_creates_blocked_and_failed_proposals(tmp_path: Path):
    sd = _state_dir(tmp_path)
    now = datetime(2026, 5, 9, 12, tzinfo=timezone.utc)
    TaskStore(sd / "kanban.json").add(Task(
        id="TASK-BLOCK",
        title="blocked task",
        status="blocked",
        blocked_reason="waiting for dependency",
    ))
    TaskStore(sd / "kanban.json").add(Task(
        id="TASK-FAIL",
        title="failed task",
        status="in_progress",
    ))
    EventLog(sd / "events.jsonl").append(ZfEvent(
        type="test.failed",
        actor="test-1",
        task_id="TASK-FAIL",
        ts=(now - timedelta(hours=1)).isoformat(),
        payload={"reason": "pytest failed"},
    ))

    result = run_autopilot_tick(sd, config=_config(enabled=True), now=now)

    kinds = {proposal.kind for proposal in result.created}
    assert "blocked_task" in kinds
    assert "failed_event" in kinds
    assert all(proposal.mode == "proposal_only" for proposal in result.created)


def test_autopilot_dry_run_does_not_write_events(tmp_path: Path):
    sd = _state_dir(tmp_path)
    TaskStore(sd / "kanban.json").add(Task(
        id="TASK-BLOCK",
        title="blocked task",
        status="blocked",
    ))

    result = run_autopilot_tick(sd, config=_config(enabled=True), dry_run=True)

    assert result.created_count == 1
    assert not (sd / "events.jsonl").exists()


def test_autopilot_cli_tick_uses_project_state_dir(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    monkeypatch.chdir(tmp_path)
    state = tmp_path / "runtime-state"
    state.mkdir()
    (state / "kanban.json").write_text("[]\n", encoding="utf-8")
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "  state_dir: runtime-state\n"
        "autopilot:\n"
        "  enabled: true\n"
        "  stale_after_hours: 1\n",
        encoding="utf-8",
    )
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    TaskStore(state / "kanban.json").add(Task(
        id="TASK-CLI",
        title="cli stale",
        status="in_progress",
        created_at=old,
    ))

    result = main(["autopilot", "tick"])

    assert result == 0
    captured = capsys.readouterr()
    assert "Autopilot tick 完成" in captured.out
    events = EventLog(state / "events.jsonl").read_all()
    assert any(event.type == AUTOPILOT_PROPOSAL_EVENT for event in events)
