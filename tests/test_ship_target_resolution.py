"""ZF-E2E-PRDCTL-P0-2: ship target branch resolution.

Live incident (2026-07-12 csvstats round): auto-ship after judge.passed died
with `git rev-parse main` in a master-based repo because the target branch
default was hardcoded. Resolution order: explicit config (create if missing)
> scan main/master > create main.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from zf.core.config.schema import (
    GitIsolationConfig,
    ProjectConfig,
    RoleConfig,
    RuntimeConfig,
    WorkdirConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.ship import ShipService


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _init_repo(root: Path, default_branch: str) -> str:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "init")
    _git(root, "branch", "-M", default_branch)
    return _git(root, "rev-parse", "HEAD")


def _candidate(root: Path, default_branch: str) -> str:
    _git(root, "checkout", "-q", "-b", "candidate/PDD-X")
    (root / "app.py").write_text("print('hi')\n", encoding="utf-8")
    _git(root, "add", "app.py")
    _git(root, "commit", "-q", "-m", "feat: candidate")
    _git(root, "checkout", "-q", default_branch)
    return "candidate/PDD-X"


def _service(root: Path, tmp_path: Path, ship_target_branch: str) -> tuple[ShipService, EventWriter]:
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[RoleConfig(name="review", backend="mock", role_kind="reader")],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
            git=GitIsolationConfig(ship_target_branch=ship_target_branch),
        ),
    )
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    service = ShipService(
        state_dir=state_dir,
        project_root=root,
        config=config,
        event_log=log,
    )
    return service, writer


def _events(writer: EventWriter) -> list:
    return list(writer.event_log.read_all())


def _seed_candidate_ready(writer: EventWriter) -> None:
    writer.event_log.append(ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        payload={
            "pdd_id": "PDD-X",
            "branch": "candidate/PDD-X",
            "candidate_ref": "candidate/PDD-X",
        },
    ))


def test_scan_resolves_master_when_unset(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root, "master")
    candidate = _candidate(root, "master")
    service, writer = _service(root, tmp_path, ship_target_branch="")
    _seed_candidate_ready(writer)
    result = service.ship(target_ref=candidate, event_writer=writer)
    assert result.status == "completed"
    completed = [e for e in _events(writer) if e.type == "ship.completed"]
    assert completed and completed[-1].payload["target_branch"] == "master"
    assert completed[-1].payload["target_resolved_from"] == "scan"
    assert _git(root, "show", "master:app.py")


def test_scan_prefers_main_when_both_exist(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root, "main")
    _git(root, "branch", "master")
    candidate = _candidate(root, "main")
    service, writer = _service(root, tmp_path, ship_target_branch="")
    _seed_candidate_ready(writer)
    result = service.ship(target_ref=candidate, event_writer=writer)
    assert result.status == "completed"
    completed = [e for e in _events(writer) if e.type == "ship.completed"]
    assert completed[-1].payload["target_branch"] == "main"


def test_creates_main_when_neither_exists(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root, "trunk")
    candidate = _candidate(root, "trunk")
    service, writer = _service(root, tmp_path, ship_target_branch="")
    _seed_candidate_ready(writer)
    result = service.ship(target_ref=candidate, event_writer=writer)
    assert result.status == "completed"
    events = _events(writer)
    created = [e for e in events if e.type == "ship.target.created"]
    assert created and created[-1].payload["target_branch"] == "main"
    completed = [e for e in events if e.type == "ship.completed"]
    assert completed[-1].payload["target_resolved_from"] == "created"
    assert _git(root, "show", "main:app.py")


def test_explicit_missing_branch_is_created(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root, "master")
    candidate = _candidate(root, "master")
    service, writer = _service(root, tmp_path, ship_target_branch="release")
    _seed_candidate_ready(writer)
    result = service.ship(target_ref=candidate, event_writer=writer)
    assert result.status == "completed"
    events = _events(writer)
    created = [e for e in events if e.type == "ship.target.created"]
    assert created and created[-1].payload["target_branch"] == "release"
    assert _git(root, "show", "release:app.py")


def test_explicit_existing_branch_unchanged_behavior(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root, "main")
    candidate = _candidate(root, "main")
    service, writer = _service(root, tmp_path, ship_target_branch="main")
    _seed_candidate_ready(writer)
    result = service.ship(target_ref=candidate, event_writer=writer)
    assert result.status == "completed"
    completed = [e for e in _events(writer) if e.type == "ship.completed"]
    assert completed[-1].payload["target_branch"] == "main"
    assert completed[-1].payload["target_resolved_from"] == "config"
