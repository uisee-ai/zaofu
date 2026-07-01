"""Auto-ship wiring tests — pair of bugs found in cangjie r-next-4 validation.

Bug A: ``runtime.git.auto_ship_on_candidate_complete: true`` in zf.yaml was
silently dropped by ``_build_runtime`` (loader never read the key) so the
field always loaded as False, and ``_maybe_auto_ship`` exited at its first
``getattr`` check without ever attempting a ship. Symptom: zero ship.*
events even when candidate.integration.completed fired.

Bug B: ``_maybe_auto_ship`` wrapped ``ShipService.ship()`` in a bare
``except Exception: pass`` so any uncaught error (bad config, ImportError,
unexpected git failure) vanished without surfacing as a ship.failed event.

These tests pin both fixes.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from zf.core.config.loader import load_config
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.config.schema import (
    GitIsolationConfig,
    ProjectConfig,
    RoleConfig,
    RuntimeConfig,
    WorkdirConfig,
    ZfConfig,
)


def _minimal_yaml(tmp_path: Path, *, auto_ship: bool) -> Path:
    data = {
        "project": {"name": "test", "state_dir": str(tmp_path / ".zf")},
        "roles": [
            {"name": "review", "backend": "mock", "role_kind": "reader",
             "publishes": ["review.approved"]},
        ],
        "runtime": {
            "git": {
                "auto_ship_on_candidate_complete": auto_ship,
            },
        },
    }
    config_path = tmp_path / "zf.yaml"
    config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return config_path


def test_loader_reads_auto_ship_on_candidate_complete_true(tmp_path: Path):
    """Bug A: loader must propagate ``auto_ship_on_candidate_complete: true``."""
    config = load_config(_minimal_yaml(tmp_path, auto_ship=True))
    assert config.runtime.git.auto_ship_on_candidate_complete is True


def test_loader_reads_auto_ship_on_candidate_complete_false(tmp_path: Path):
    config = load_config(_minimal_yaml(tmp_path, auto_ship=False))
    assert config.runtime.git.auto_ship_on_candidate_complete is False


def test_loader_reads_role_spawn_ready_timeout_seconds(tmp_path: Path):
    """B-NEW-3 relapse audit (docs/impl/22 §9.5): loader was dropping
    ``RoleConfig.spawn_ready_timeout_seconds``. orchestrator_lifecycle reads
    ``getattr(role, "spawn_ready_timeout_seconds", 0) or 120.0`` to let
    operators override the cold-boot timeout — but the loader never wired
    the field so the override was dead. Pin the fix.
    """
    data = {
        "project": {"name": "test", "state_dir": str(tmp_path / ".zf")},
        "roles": [
            {"name": "arch", "backend": "mock", "role_kind": "reader",
             "publishes": ["arch.proposal.done"],
             "spawn_ready_timeout_seconds": 90.0},
        ],
        "runtime": {"git": {}},
    }
    cfg_path = tmp_path / "zf.yaml"
    cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    config = load_config(cfg_path)
    arch = next(r for r in config.roles if r.name == "arch")
    assert arch.spawn_ready_timeout_seconds == 90.0


def test_loader_defaults_auto_ship_off_when_unspecified(tmp_path: Path):
    data = {
        "project": {"name": "test", "state_dir": str(tmp_path / ".zf")},
        "roles": [
            {"name": "review", "backend": "mock", "role_kind": "reader",
             "publishes": ["review.approved"]},
        ],
        "runtime": {"git": {}},
    }
    cfg_path = tmp_path / "zf.yaml"
    cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    config = load_config(cfg_path)
    assert config.runtime.git.auto_ship_on_candidate_complete is False


def _orch_config(state_dir: Path, *, auto_ship: bool) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="review", backend="mock", role_kind="reader",
                       publishes=["review.approved"]),
        ],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
            git=GitIsolationConfig(auto_ship_on_candidate_complete=auto_ship),
        ),
    )


def test_maybe_auto_ship_emits_ship_failed_on_exception(tmp_path: Path):
    """Bug B: when ShipService.ship() raises, _maybe_auto_ship must emit
    ship.failed instead of swallowing silently."""
    from zf.runtime.orchestrator import Orchestrator

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")

    config = _orch_config(state_dir, auto_ship=True)
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)

    orch = Orchestrator.__new__(Orchestrator)
    orch.state_dir = state_dir
    orch.config = config
    orch.event_log = log
    orch.event_writer = writer

    trigger = ZfEvent(
        type="candidate.integration.completed",
        actor="zf-cli",
        payload={
            "branch": "candidate/F-deadbeef",
            "pdd_id": "F-deadbeef",
            "quality_status": "passed",
        },
        correlation_id="trace-test",
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated git failure")

    with patch("zf.runtime.ship.ShipService.ship", side_effect=_boom):
        orch._maybe_auto_ship(trigger)

    events = list(log.read_all())
    ship_failed = [e for e in events if e.type == "ship.failed"]
    assert ship_failed, f"expected ship.failed; got {[e.type for e in events]}"
    payload = ship_failed[0].payload
    assert payload["target_ref"] == "candidate/F-deadbeef"
    assert payload["feature_id"] == "F-deadbeef"
    assert "simulated git failure" in payload["error"]
    assert payload["source"] == "auto_ship_exception"
    assert ship_failed[0].correlation_id == "trace-test"


def test_maybe_auto_ship_end_to_end_with_real_git(tmp_path: Path):
    """B-NEW-3 e2e validation via real git, no LLM agents required.

    cangjie r-next-4/r-next-5 were intended to validate B-NEW-3 fix in
    a real 4-task fanout but were blocked by orthogonal bugs (B-NEW-5
    drift refresh loop, B-NEW-6 dispatch stall). This test exercises
    the same code path — Orchestrator._maybe_auto_ship → ShipService
    → real git ff-merge → ship.completed → main HEAD advances — but
    bypasses the agent fanout, so the ship loop can be validated
    independently of the kernel dispatch loop.

    Setup: real git repo with main + candidate/F-XX (1 commit ahead),
    candidate.ready event seeded, auto_ship_on_candidate_complete=True.
    Call _maybe_auto_ship with a candidate.integration.completed event.
    Expect: ship.lock_acquired, ship.started, ship.completed events
    fire; main HEAD points at the candidate commit; ``pdd/F-XX-final``
    tag exists.
    """
    import subprocess
    from tests.test_ship import _init_repo, _candidate_branch, _candidate_ready
    from zf.runtime.orchestrator import Orchestrator

    # ── Step 1: real git repo with candidate branch ahead of main ──
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")

    pdd_id = "F-deadbeef"
    candidate_head = _candidate_branch(
        tmp_path, pdd_id, "feature.txt", "shipped content\n",
    )
    main_head_before = subprocess.run(
        ["git", "rev-parse", "main"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert main_head_before != candidate_head, (
        "test setup: candidate should be ahead of main"
    )

    # ── Step 2: wire orchestrator with auto_ship=True ──
    config = _orch_config(state_dir, auto_ship=True)
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    _candidate_ready(log, pdd_id)  # mark candidate ready so _blockers passes

    orch = Orchestrator.__new__(Orchestrator)
    orch.state_dir = state_dir
    orch.config = config
    orch.event_log = log
    orch.event_writer = writer

    # ── Step 3: fire candidate.integration.completed (matching cangjie shape) ──
    trigger = ZfEvent(
        type="candidate.integration.completed",
        actor="zf-cli",
        payload={
            "branch": f"candidate/{pdd_id}",
            "pdd_id": pdd_id,
            "quality_status": "passed",
            "status": "updated",
        },
        correlation_id="trace-e2e",
    )

    orch._maybe_auto_ship(trigger)

    # ── Step 4: ship.* events must have fired ──
    events_after = list(log.read_all())
    event_types = [e.type for e in events_after]
    assert "ship.lock_acquired" in event_types, (
        f"expected ship.lock_acquired; got {event_types}"
    )
    assert "ship.started" in event_types
    assert "ship.completed" in event_types, (
        f"B-NEW-3 fix did not produce ship.completed; events: {event_types}"
    )
    assert "ship.lock_released" in event_types

    # ── Step 5: main HEAD must advance to (or include) candidate commit ──
    main_head_after = subprocess.run(
        ["git", "rev-parse", "main"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert main_head_after != main_head_before, (
        "main HEAD did not advance — auto-ship did not ff-merge"
    )
    # ff-merge: main HEAD == candidate HEAD; merge commit: main has candidate as parent
    main_log = subprocess.run(
        ["git", "log", "--format=%H", "main"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    assert candidate_head in main_log, (
        f"candidate commit {candidate_head} not in main history; main_log={main_log}"
    )

    # ── Step 6: final tag was created ──
    tag_exists = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/tags/pdd/{pdd_id}-final"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert tag_exists.returncode == 0, (
        f"expected tag pdd/{pdd_id}-final to exist after ship.completed"
    )

    # ── Step 7: ship.completed payload carries the new state ──
    completed = next(e for e in events_after if e.type == "ship.completed")
    assert completed.payload["target_branch"] == "main"
    assert completed.payload["final_commit"]
    assert completed.correlation_id == "trace-e2e", (
        "correlation_id must propagate through ShipService"
    )


def test_maybe_auto_ship_no_op_when_disabled(tmp_path: Path):
    """When auto_ship is False, no ship.* event of any kind should fire."""
    from zf.runtime.orchestrator import Orchestrator

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")

    config = _orch_config(state_dir, auto_ship=False)
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)

    orch = Orchestrator.__new__(Orchestrator)
    orch.state_dir = state_dir
    orch.config = config
    orch.event_log = log
    orch.event_writer = writer

    trigger = ZfEvent(
        type="candidate.integration.completed",
        actor="zf-cli",
        payload={"branch": "candidate/F-deadbeef", "quality_status": "passed"},
    )

    orch._maybe_auto_ship(trigger)

    events = list(log.read_all())
    assert not any(e.type.startswith("ship.") for e in events)
