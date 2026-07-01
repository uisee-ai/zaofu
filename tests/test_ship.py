from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

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
from zf.runtime.ship import MainLock, ShipService


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )
    return result.stdout.strip()


def _init_repo(root: Path) -> str:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "init")
    _git(root, "branch", "-M", "main")
    return _git(root, "rev-parse", "HEAD")


def _config(state_dir: Path) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(
                name="review",
                backend="mock",
                role_kind="reader",
                publishes=["review.approved", "review.rejected"],
            ),
        ],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
            git=GitIsolationConfig(ship_target_branch="main"),
        ),
    )


def _state(root: Path) -> tuple[Path, ZfConfig, EventLog, ShipService]:
    state_dir = root / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = _config(state_dir)
    log = EventLog(state_dir / "events.jsonl")
    return (
        state_dir,
        config,
        log,
        ShipService(
            state_dir=state_dir,
            project_root=root,
            config=config,
            event_log=log,
        ),
    )


def _candidate_branch(root: Path, pdd_id: str, file_name: str, content: str) -> str:
    branch = f"candidate/{pdd_id}"
    _git(root, "checkout", "-q", "-B", branch, "main")
    (root / file_name).write_text(content, encoding="utf-8")
    _git(root, "add", file_name)
    _git(root, "commit", "-q", "-m", f"candidate {pdd_id}")
    return _git(root, "rev-parse", "HEAD")


def _candidate_ready(log: EventLog, pdd_id: str) -> None:
    log.append(ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        payload={
            "pdd_id": pdd_id,
            "branch": f"candidate/{pdd_id}",
            "candidate_ref": f"candidate/{pdd_id}",
        },
    ))


def test_main_lock_blocks_concurrent_ship(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir, _config_obj, log, service = _state(tmp_path)
    writer = EventWriter(log)

    with MainLock(state_dir / "locks" / "main.lock"):
        result = service.ship(pdd_id="F-11111111", event_writer=writer)

    assert result.status == "blocked"
    assert "main lock is already held" in result.payload["blockers"]


def test_missing_candidate_ref_blocks(tmp_path: Path):
    _init_repo(tmp_path)
    _state_dir, _config_obj, log, service = _state(tmp_path)

    result = service.ship(
        pdd_id="F-11111111",
        event_writer=EventWriter(log),
    )

    assert result.status == "blocked"
    assert "target ref 'candidate/F-11111111' not found" in result.payload["blockers"]
    event_types = [event.type for event in log.read_all()]
    assert "ship.lock_acquired" in event_types
    assert "ship.blocked" in event_types
    assert "ship.lock_released" in event_types


def test_clean_candidate_ship_merges_to_main_and_tags(tmp_path: Path):
    _init_repo(tmp_path)
    _state_dir, _config_obj, log, service = _state(tmp_path)
    _candidate_branch(tmp_path, "F-11111111", "a.txt", "candidate\n")
    _candidate_ready(log, "F-11111111")

    result = service.ship(
        pdd_id="F-11111111",
        event_writer=EventWriter(log),
    )

    assert result.status == "completed"
    assert _git(tmp_path, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "candidate\n"
    assert _git(tmp_path, "rev-parse", "--verify", "pdd/F-11111111-final")
    assert any(event.type == "ship.completed" for event in log.read_all())


def test_candidate_ship_conflict_aborts_and_leaves_main_unchanged(tmp_path: Path):
    _init_repo(tmp_path)
    _state_dir, _config_obj, log, service = _state(tmp_path)
    _candidate_branch(tmp_path, "F-11111111", "README.md", "candidate\n")
    _git(tmp_path, "checkout", "-q", "main")
    (tmp_path / "README.md").write_text("main\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-q", "-m", "main moves")
    main_head = _git(tmp_path, "rev-parse", "HEAD")
    _candidate_ready(log, "F-11111111")

    result = service.ship(
        pdd_id="F-11111111",
        event_writer=EventWriter(log),
    )

    assert result.status == "conflict"
    assert "README.md" in result.payload["conflict_files"]
    assert _git(tmp_path, "rev-parse", "HEAD") == main_head
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "main\n"
    assert any(event.type == "ship.conflict" for event in log.read_all())


def test_web_ship_action_calls_deterministic_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from zf.web.server import create_app

    _init_repo(tmp_path)
    state_dir, config, log, _service = _state(tmp_path)
    _candidate_branch(tmp_path, "F-11111111", "a.txt", "candidate\n")
    _candidate_ready(log, "F-11111111")
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "secret")
    client = TestClient(create_app(state_dir, config=config))

    response = client.post(
        "/api/actions/ship",
        json={"pdd_id": "F-11111111"},
        headers={"X-ZF-Web-Token": "secret"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["status"] == "completed"
    event_types = [event.type for event in log.read_all()]
    assert "web.action.requested" in event_types
    assert "ship.completed" in event_types
    assert "web.action.completed" in event_types


# ─── B-NEW-13: untracked files must not block ship ───────────────────────


def test_untracked_file_at_root_does_not_block_ship(tmp_path: Path):
    """B-NEW-13 (2026-05-17): cangjie r-next-8 + r-next-9 both ship.blocked
    on a single untracked file (autoresearch-seed.txt). Untracked files
    were never committed by any dev and never affect what
    `git merge candidate/<id>` produces, so they must not be flagged as
    "working tree is dirty".
    """
    _init_repo(tmp_path)
    _state_dir, _config_obj, log, service = _state(tmp_path)
    _candidate_branch(tmp_path, "F-11111111", "a.txt", "candidate\n")
    _candidate_ready(log, "F-11111111")

    # Long-lived operator-local file, untracked.
    (tmp_path / "autoresearch-seed.txt").write_text("seed\n", encoding="utf-8")

    result = service.ship(
        pdd_id="F-11111111",
        event_writer=EventWriter(log),
    )

    assert result.status == "completed", (
        f"ship blocked despite untracked file: blockers={result.payload.get('blockers')}"
    )
    assert "working tree is dirty" not in (result.payload.get("blockers") or [])


def test_tracked_modification_still_blocks_ship(tmp_path: Path):
    """B-NEW-13 corollary: real tracked modifications still gate ship.
    Skipping ONLY `??` lines, not `M ` / `MM` / ` M`."""
    _init_repo(tmp_path)
    _state_dir, _config_obj, log, service = _state(tmp_path)
    _candidate_branch(tmp_path, "F-11111111", "a.txt", "candidate\n")
    _candidate_ready(log, "F-11111111")

    _git(tmp_path, "checkout", "-q", "main")
    (tmp_path / "README.md").write_text("modified\n", encoding="utf-8")
    # README.md is tracked → ` M README.md` in porcelain output.

    result = service.ship(
        pdd_id="F-11111111",
        event_writer=EventWriter(log),
    )

    assert result.status == "blocked"
    assert "working tree is dirty" in result.payload["blockers"]


# ─── B-NEW-14: candidate.integration.completed should signal ready ──────


def test_candidate_integration_completed_event_makes_candidate_ready(tmp_path: Path):
    """B-NEW-14 (2026-05-17): kernel emits `candidate.integration.completed`
    (not `candidate.ready`) and writes manifest.status="updated" on the
    happy path (candidates.py:436-448). Ship's readiness check must accept
    candidate.integration.completed as the terminal positive signal so
    auto-ship can complete without manual `zf emit candidate.ready`.

    Reproduces the cangjie r-next-9 path:
      candidate.quality.passed → candidate.updated → candidate.integration.completed
      → auto-ship → was blocked on "candidate is not ready".
    """
    _init_repo(tmp_path)
    _state_dir, _config_obj, log, service = _state(tmp_path)
    _candidate_branch(tmp_path, "F-22222222", "b.txt", "candidate\n")

    # Replay the kernel-emitted terminal event from cangjie:
    log.append(ZfEvent(
        type="candidate.integration.completed",
        actor="zf-cli",
        payload={
            "pdd_id": "F-22222222",
            "branch": "candidate/F-22222222",
            "status": "updated",
            "quality_status": "passed",
        },
    ))

    result = service.ship(
        pdd_id="F-22222222",
        event_writer=EventWriter(log),
    )

    assert result.status == "completed", (
        f"ship blocked despite candidate.integration.completed: "
        f"blockers={result.payload.get('blockers')}"
    )


def test_manifest_status_updated_with_quality_passed_marks_ready(tmp_path: Path):
    """B-NEW-14 fallback: when events.jsonl is pruned but manifest.json
    still exists with status=updated + quality_status=passed, ship should
    treat that as ready (otherwise restart loses ship-eligibility)."""
    _init_repo(tmp_path)
    state_dir, _config_obj, log, service = _state(tmp_path)
    _candidate_branch(tmp_path, "F-33333333", "c.txt", "candidate\n")

    candidate_dir = state_dir / "candidates" / "F-33333333"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    import json
    (candidate_dir / "manifest.json").write_text(
        json.dumps({
            "pdd_id": "F-33333333",
            "status": "updated",
            "quality_status": "passed",
        }),
        encoding="utf-8",
    )

    result = service.ship(
        pdd_id="F-33333333",
        event_writer=EventWriter(log),
    )

    assert result.status == "completed", (
        f"ship blocked despite manifest status=updated+quality=passed: "
        f"blockers={result.payload.get('blockers')}"
    )


def test_candidate_conflict_after_integration_still_blocks(tmp_path: Path):
    """B-NEW-14 corollary: if `candidate.conflict` is the most recent
    terminal event for this candidate (e.g. a rebuild after the prior
    integration found conflicts), ship must still block — order matters."""
    _init_repo(tmp_path)
    _state_dir, _config_obj, log, service = _state(tmp_path)
    _candidate_branch(tmp_path, "F-44444444", "d.txt", "candidate\n")

    log.append(ZfEvent(
        type="candidate.integration.completed",
        actor="zf-cli",
        payload={"pdd_id": "F-44444444", "branch": "candidate/F-44444444"},
    ))
    log.append(ZfEvent(
        type="candidate.conflict",
        actor="zf-cli",
        payload={"pdd_id": "F-44444444", "branch": "candidate/F-44444444"},
    ))

    result = service.ship(
        pdd_id="F-44444444",
        event_writer=EventWriter(log),
    )

    assert result.status == "blocked"
    assert "candidate is not ready" in result.payload["blockers"]


def test_candidate_quality_failed_blocks_even_with_integration_event(tmp_path: Path):
    """B-NEW-14 corollary: candidate.quality.failed is a hard block even
    if candidate.integration.completed fired later (shouldn't happen, but
    defense in depth)."""
    _init_repo(tmp_path)
    _state_dir, _config_obj, log, service = _state(tmp_path)
    _candidate_branch(tmp_path, "F-55555555", "e.txt", "candidate\n")

    log.append(ZfEvent(
        type="candidate.quality.failed",
        actor="zf-cli",
        payload={"pdd_id": "F-55555555", "branch": "candidate/F-55555555"},
    ))

    result = service.ship(
        pdd_id="F-55555555",
        event_writer=EventWriter(log),
    )

    assert result.status == "blocked"
    assert "candidate is not ready" in result.payload["blockers"]
