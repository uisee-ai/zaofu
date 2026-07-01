"""Run archive contract tests."""

from __future__ import annotations

import json
import shlex
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.security.signing import EventSigner
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.run_archive import (
    RUN_EVENT_TYPES,
    RunArchiveError,
    RunProjector,
    archive_run,
    read_task_runs,
    reconcile_runs,
    run_and_archive_command,
)


def _project(tmp_path: Path) -> tuple[Path, Path]:
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf"
    state_dir.mkdir(parents=True)
    (project_root / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: owner\n',
        encoding="utf-8",
    )
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    return project_root, state_dir


def _live_state(tmp_path: Path, name: str = "live") -> Path:
    live = tmp_path / name / ".zf"
    live.mkdir(parents=True)
    EventLog(live / "events.jsonl").append(
        ZfEvent(
            type="test.failed",
            actor="test-1",
            task_id="TASK-1",
            payload={"message": "OPENAI_API_KEY=sk-1234567890abcdef"},
        )
    )
    (live / "role_sessions.yaml").write_text(
        "provider_token: sk-1234567890abcdef\n",
        encoding="utf-8",
    )
    return live


def test_task_schema_does_not_gain_run_fields(tmp_path: Path):
    path = tmp_path / "kanban.json"
    store = TaskStore(path)
    store.add(Task(
        id="TASK-1",
        title="contract task",
        contract={
            "behavior": "do x",
            "verification": "pytest",
            "scope": ["src"],
            "acceptance": "exit_code=0",
        },
    ))

    task = TaskStore(path).get("TASK-1")

    assert task.contract.behavior == "do x"
    assert "run_id" not in task.__dict__
    assert "latest_run_id" not in (task.evidence.__dict__ if task.evidence else {})


def test_run_event_contract_and_signed_decode(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log, correlation_id="trace-1")
    created = writer.emit("task.created", actor="zf-cli", task_id="TASK-1")

    event = writer.emit(
        "run.started",
        actor="zf-cli",
        task_id="TASK-1",
        payload={"run_id": "RUN-1", "status": "running"},
    )

    assert event.payload["run_id"] == "RUN-1"
    assert event.task_id == "TASK-1"
    assert event.correlation_id == "trace-1"
    assert event.causation_id == created.id

    signed = EventLog(tmp_path / "signed.jsonl", signer=EventSigner(b"secret"))
    signed.append(event)
    decoded = EventLog(tmp_path / "signed.jsonl", signer=EventSigner(b"secret")).read_all()
    assert decoded[0].type == "run.started"
    assert decoded[0].payload["run_id"] == "RUN-1"


def test_archive_run_writes_manifest_and_redacts(tmp_path: Path):
    project_root, state_dir = _project(tmp_path)
    live = _live_state(tmp_path)

    result = archive_run(
        project_root=project_root,
        state_dir=state_dir,
        live_state_dir=live,
        run_id="RUN-1",
        status="pass",
        trace_id="trace-1",
        test_task_id="TASK-1",
        command="pytest",
        env={
            "OPENAI_API_KEY": "sk-1234567890abcdef",
            "JWT_TOKEN": "aaaaaaaaaa.bbbbbbbbbb.cccccccccc",
        },
    )

    assert result.status == "passed"
    assert result.run_yaml_path.exists()
    assert result.manifest_path.exists()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    paths = {artifact["path"] for artifact in manifest["artifacts"]}
    assert "artifact_manifest.json" not in paths
    assert "run.yaml" in paths
    assert "events.jsonl" in paths
    assert "cost.jsonl" in manifest["missing"]
    assert "env_redacted.json" in manifest["redacted"]
    archive_text = result.artifact_dir.joinpath("env_redacted.json").read_text(encoding="utf-8")
    sessions_text = result.artifact_dir.joinpath("role_sessions.redacted.yaml").read_text(encoding="utf-8")
    assert "sk-1234567890abcdef" not in archive_text
    assert "sk-1234567890abcdef" not in sessions_text
    assert "[REDACTED" in archive_text


@pytest.mark.parametrize("run_id", ["", "../RUN", "a/b", "a\\b", "..", "bad space"])
def test_archive_rejects_unsafe_run_ids(tmp_path: Path, run_id: str):
    project_root, state_dir = _project(tmp_path)
    with pytest.raises(RunArchiveError):
        archive_run(
            project_root=project_root,
            state_dir=state_dir,
            live_state_dir=_live_state(tmp_path),
            run_id=run_id,
            status="passed",
        )


def test_run_projector_rebuilds_active_and_index_from_events_and_archives(tmp_path: Path):
    project_root, state_dir = _project(tmp_path)
    archive_dir = state_dir / "events"
    archive_dir.mkdir()
    started = ZfEvent(
        type="run.started",
        actor="zf-cli",
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={"run_id": "RUN-A", "live_state_dir": "/tmp/live", "status": "running"},
    )
    archive_dir.joinpath("2026-05-05.jsonl").write_text(
        started.to_json() + "\n",
        encoding="utf-8",
    )
    writer = EventWriter(EventLog(state_dir / "events.jsonl"), correlation_id="trace-1")
    writer.emit("run.heartbeat", actor="zf-cli", task_id="TASK-1", payload={"run_id": "RUN-A"})
    writer.emit("run.started", actor="zf-cli", task_id="TASK-2", payload={"run_id": "RUN-B"})
    writer.emit(
        "run.completed",
        actor="zf-cli",
        task_id="TASK-2",
        payload={"run_id": "RUN-B", "status": "passed", "exit_code": 0},
    )
    archive = archive_run(
        project_root=project_root,
        state_dir=state_dir,
        live_state_dir=_live_state(tmp_path, "live-b"),
        run_id="RUN-B",
        status="passed",
        trace_id="trace-1",
        test_task_id="TASK-2",
    )
    writer.emit(
        "run.archived",
        actor="zf-cli",
        task_id="TASK-2",
        payload={"run_id": "RUN-B", "artifact_dir": str(archive.artifact_dir)},
    )

    projection = RunProjector(project_root=project_root, state_dir=state_dir).rebuild()

    assert [item["run_id"] for item in projection.active["active_runs"]] == ["RUN-A"]
    assert projection.active["active_runs"][0]["heartbeat_at"]
    assert [item["run_id"] for item in projection.index["runs"]] == ["RUN-B"]
    assert read_task_runs(project_root=project_root, state_dir=state_dir, task_id="TASK-2")[0]["run_id"] == "RUN-B"


def test_projector_uses_explicit_project_root_with_external_state_dir(tmp_path: Path):
    project_root = tmp_path / "repo"
    project_root.mkdir()
    state_dir = tmp_path / "runtime-state"
    state_dir.mkdir()
    (project_root / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: external\n  state_dir: ../runtime-state\n',
        encoding="utf-8",
    )

    archive = archive_run(
        project_root=project_root,
        state_dir=state_dir,
        live_state_dir=_live_state(tmp_path),
        run_id="RUN-EXT",
        status="passed",
        test_task_id="TASK-EXT",
    )
    projection = RunProjector(project_root=project_root, state_dir=state_dir).rebuild()
    run_yaml = archive.run_yaml_path.read_text(encoding="utf-8")

    assert "owner_project_id: repo" in run_yaml
    assert projection.index["runs"][0]["run_id"] == "RUN-EXT"


def test_run_wrapper_failure_and_missing_live_state_are_archived(tmp_path: Path):
    project_root, state_dir = _project(tmp_path)
    live = _live_state(tmp_path)

    failed = run_and_archive_command(
        project_root=project_root,
        state_dir=state_dir,
        live_state_dir=live,
        run_id="RUN-FAIL",
        command='python3 -c "raise SystemExit(3)"',
        test_task_id="TASK-FAIL",
    )
    missing = run_and_archive_command(
        project_root=project_root,
        state_dir=state_dir,
        live_state_dir=tmp_path / "missing" / ".zf",
        run_id="RUN-MISSING",
        command='python3 -c "pass"',
        test_task_id="TASK-MISSING",
    )
    timed_out = run_and_archive_command(
        project_root=project_root,
        state_dir=state_dir,
        live_state_dir=live,
        run_id="RUN-TIMEOUT",
        command='python3 -c "import time; time.sleep(5)"',
        test_task_id="TASK-TIMEOUT",
        timeout=0.01,
    )

    events = EventLog(state_dir / "events.jsonl").read_all()
    assert failed.status == "failed"
    assert failed.manifest_path.exists()
    assert missing.status == "abandoned"
    assert missing.manifest_path.exists()
    assert timed_out.status == "failed"
    assert timed_out.manifest_path.exists()
    assert [event.type for event in events if event.payload.get("run_id") == "RUN-FAIL"] == [
        "run.started",
        "run.completed",
        "run.archived",
    ]
    assert [event.type for event in events if event.payload.get("run_id") == "RUN-MISSING"] == [
        "run.started",
        "run.abandoned",
        "run.archived",
    ]
    timeout_events = [event for event in events if event.payload.get("run_id") == "RUN-TIMEOUT"]
    assert [event.type for event in timeout_events] == [
        "run.started",
        "run.completed",
        "run.archived",
    ]
    assert timeout_events[1].payload["reason"] == "timeout"


def test_run_wrapper_publishes_active_projection_before_command_finishes(tmp_path: Path):
    project_root, state_dir = _project(tmp_path)
    live = _live_state(tmp_path)
    probe = tmp_path / "probe_active.py"
    probe.write_text(
        """
import json
import sys
from pathlib import Path

active = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
run_ids = [item.get("run_id") for item in active.get("active_runs", [])]
if "RUN-ACTIVE" not in run_ids:
    raise SystemExit(f"RUN-ACTIVE missing from active projection: {run_ids}")
""".lstrip(),
        encoding="utf-8",
    )

    result = run_and_archive_command(
        project_root=project_root,
        state_dir=state_dir,
        live_state_dir=live,
        run_id="RUN-ACTIVE",
        command=(
            f"{shlex.quote(sys.executable)} {shlex.quote(str(probe))} "
            f"{shlex.quote(str(state_dir / 'runs' / 'active.json'))}"
        ),
        test_task_id="TASK-ACTIVE",
    )

    assert result.status == "passed"
    active = json.loads((state_dir / "runs" / "active.json").read_text(encoding="utf-8"))
    assert active["active_runs"] == []


def test_run_wrapper_marks_owner_test_task_done_on_passed_run(tmp_path: Path):
    project_root, state_dir = _project(tmp_path)
    live = _live_state(tmp_path)
    task_store = TaskStore(state_dir / "kanban.json")
    task_store.add(Task(id="TASK-PASS", title="provider validation", status="in_progress"))

    result = run_and_archive_command(
        project_root=project_root,
        state_dir=state_dir,
        live_state_dir=live,
        run_id="RUN-PASS",
        command='python3 -c "pass"',
        test_task_id="TASK-PASS",
    )

    task = TaskStore(state_dir / "kanban.json").get("TASK-PASS")
    events = [
        event for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.task_id == "TASK-PASS"
    ]
    status_event = next(event for event in events if event.type == "task.status_changed")

    assert result.status == "passed"
    assert task is not None
    assert task.status == "done"
    assert status_event.payload == {
        "from": "in_progress",
        "to": "done",
        "source": "run_completed",
        "run_id": "RUN-PASS",
        "trigger_event": "run.completed",
    }
    assert status_event.causation_id == next(
        event.id for event in events if event.type == "run.completed"
    )


def test_reconcile_archives_stale_active_run_idempotently(tmp_path: Path):
    project_root, state_dir = _project(tmp_path)
    live = _live_state(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(
            type="run.started",
            ts=old,
            actor="zf-cli",
            task_id="TASK-STALE",
            payload={
                "run_id": "RUN-STALE",
                "live_state_dir": str(live),
                "status": "running",
            },
        )
    )

    first = reconcile_runs(
        project_root=project_root,
        state_dir=state_dir,
        stale_after_seconds=1,
    )
    second = reconcile_runs(
        project_root=project_root,
        state_dir=state_dir,
        stale_after_seconds=1,
    )

    assert first.stalled == 1
    assert first.abandoned == 1
    assert first.archived == 1
    assert second.inspected == 0
    assert (state_dir / "runs" / "RUN-STALE" / "artifact_manifest.json").exists()
    events = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
    assert "run.stalled" in events
    assert "run.abandoned" in events


def test_run_status_set_is_canonical():
    assert RUN_EVENT_TYPES == {
        "run.started",
        "run.heartbeat",
        "run.stalled",
        "run.cancelled",
        "run.completed",
        "run.archived",
        "run.abandoned",
    }
