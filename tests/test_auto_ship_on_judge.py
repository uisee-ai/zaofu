"""Auto-ship on judge.passed — terminal-gate ship for the cj-min refactor.

The cangjie auto-ship (``auto_ship_on_candidate_complete``) fires on
``candidate.integration.completed``, which in the cj-min topology happens
BEFORE the candidate-level review→verify→judge gate. Verified against the R18
event order:

    candidate.integration.completed ... review.approved, test.passed, judge.passed

So shipping at integration would merge un-judged code. ``auto_ship_on_judge_passed``
fires after ``judge.passed`` — the terminal quality gate — and resolves the
candidate branch from the judge.passed payload's ``target_ref`` field (R18 shape:
``target_ref="cj-min-candidate-.../CJMIN-R18"``, ``task_id=null``).

These tests pin: loader propagation, the e2e ship on judge.passed, and that the
two flags stay independent (judge flag must not ship the pre-judge integration
event, and the cangjie flag must not ship on judge.passed).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from zf.core.config.loader import load_config
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


def _yaml(tmp_path: Path, **git) -> Path:
    data = {
        "project": {"name": "test", "state_dir": str(tmp_path / ".zf")},
        "roles": [
            {"name": "judge", "backend": "mock", "role_kind": "reader",
             "publishes": ["judge.passed"]},
        ],
        "runtime": {"git": git},
    }
    path = tmp_path / "zf.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_loader_reads_auto_ship_on_judge_passed_true(tmp_path: Path):
    cfg = load_config(_yaml(tmp_path, auto_ship_on_judge_passed=True))
    assert cfg.runtime.git.auto_ship_on_judge_passed is True


def test_loader_defaults_auto_ship_on_judge_passed_false(tmp_path: Path):
    cfg = load_config(_yaml(tmp_path))
    assert cfg.runtime.git.auto_ship_on_judge_passed is False


def _orch(state_dir: Path, *, judge_flag=False, candidate_flag=False,
          ship_target="main"):
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="judge", backend="mock", role_kind="reader",
                       publishes=["judge.passed"]),
        ],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
            git=GitIsolationConfig(
                auto_ship_on_candidate_complete=candidate_flag,
                auto_ship_on_judge_passed=judge_flag,
                ship_target_branch=ship_target,
            ),
        ),
    )
    from zf.runtime.orchestrator import Orchestrator

    log = EventLog(state_dir / "events.jsonl")
    orch = Orchestrator.__new__(Orchestrator)
    orch.state_dir = state_dir
    orch.config = config
    orch.event_log = log
    orch.event_writer = EventWriter(log)
    return orch, log


def _git_head(tmp_path: Path, ref: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", ref], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def test_judge_passed_ships_candidate_end_to_end(tmp_path: Path):
    """judge.passed + auto_ship_on_judge_passed → candidate merged into
    ship_target_branch, resolving the branch from the payload ``target_ref``."""
    from tests.test_ship import _candidate_branch, _candidate_ready, _init_repo

    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")

    pdd_id = "CJMIN-RTEST"
    candidate_head = _candidate_branch(tmp_path, pdd_id, "feature.txt", "shipped\n")
    main_before = _git_head(tmp_path, "main")
    assert main_before != candidate_head

    orch, log = _orch(state_dir, judge_flag=True)
    _candidate_ready(log, pdd_id)

    # judge.passed shape from the R18 fixture: target_ref = candidate branch,
    # task_id null, no pdd_id field — so the candidate MUST resolve via target_ref.
    judge = ZfEvent(
        type="judge.passed",
        actor="zf-cli",
        payload={
            "target_ref": f"candidate/{pdd_id}",
            "task_id": None,
            "fanout_id": "fanout-final-judge-x",
        },
        correlation_id="trace-judge",
    )
    orch._maybe_auto_ship(judge)

    types = [e.type for e in log.read_all()]
    assert "ship.completed" in types, f"judge.passed did not ship; got {types}"
    main_after = _git_head(tmp_path, "main")
    assert main_after != main_before, "ship_target_branch did not advance"
    main_log = subprocess.run(
        ["git", "log", "--format=%H", "main"], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    assert candidate_head in main_log
    completed = next(e for e in log.read_all() if e.type == "ship.completed")
    assert completed.correlation_id == "trace-judge"


def test_replayed_judge_passed_does_not_emit_ship_blocked(tmp_path: Path):
    """Two watcher deliveries of one judge fact share a single ship result."""
    from tests.test_ship import _candidate_branch, _candidate_ready, _init_repo

    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    pdd_id = "CJMIN-REPLAY"
    _candidate_branch(tmp_path, pdd_id, "feature.txt", "shipped\n")
    orch, log = _orch(state_dir, judge_flag=True)
    _candidate_ready(log, pdd_id)
    judge = ZfEvent(
        type="judge.passed",
        actor="zf-cli",
        payload={"target_ref": f"candidate/{pdd_id}"},
        correlation_id="trace-judge-replay",
    )

    orch._maybe_auto_ship(judge)
    orch._maybe_auto_ship(judge)

    events = list(log.read_all())
    assert sum(event.type == "ship.completed" for event in events) == 1
    assert not any(event.type == "ship.blocked" for event in events)


def test_judge_passed_ships_through_apply_housekeeping(tmp_path: Path):
    """LB-1: exercise the real dispatch path, not _maybe_auto_ship directly.

    judge.passed matches an earlier acceptance-evidence `elif` in the
    _apply_housekeeping chain, so the auto-ship `elif` further down was
    unreachable — auto_ship_on_judge_passed silently never fired. Pin that
    judge.passed reaches ship through _apply_housekeeping."""
    from tests.test_ship import _candidate_branch, _candidate_ready, _init_repo

    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")

    pdd_id = "HOUSEKEEP-1"
    candidate_head = _candidate_branch(tmp_path, pdd_id, "feature.txt", "shipped\n")
    main_before = _git_head(tmp_path, "main")

    orch, log = _orch(state_dir, judge_flag=True)
    _candidate_ready(log, pdd_id)

    judge = ZfEvent(
        type="judge.passed",
        actor="zf-cli",
        payload={"target_ref": f"candidate/{pdd_id}", "pdd_id": pdd_id},
        correlation_id="trace-hk",
    )
    orch._promoted_causations = set()
    orch._apply_housekeeping(judge)

    types = [e.type for e in log.read_all()]
    assert "ship.completed" in types, (
        f"judge.passed did not ship through _apply_housekeeping "
        f"(elif shadow?); got {types}"
    )
    assert _git_head(tmp_path, "main") != main_before


def test_judge_passed_prd_flow_resolves_candidate_from_feature_id(tmp_path: Path):
    """LB-1: PRD/issue fanout judge.passed carries target_ref = ship DESTINATION
    (e.g. "main"), not the candidate branch. Auto-ship must ignore that and
    resolve the candidate from feature_id/pdd_id, else it tries to ship "main"
    onto main (no-op) and the deliverable never lands (light baseline 2026-07-06)."""
    from tests.test_ship import _candidate_branch, _candidate_ready, _init_repo

    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")

    pdd_id = "default"
    candidate_head = _candidate_branch(tmp_path, pdd_id, "feature.txt", "shipped\n")
    main_before = _git_head(tmp_path, "main")

    orch, log = _orch(state_dir, judge_flag=True)
    _candidate_ready(log, pdd_id)

    # PRD light judge.passed shape: target_ref is the DESTINATION "main",
    # feature_id/pdd_id name the candidate.
    judge = ZfEvent(
        type="judge.passed",
        actor="zf-cli",
        payload={
            "target_ref": "main",
            "feature_id": pdd_id,
            "pdd_id": pdd_id,
            "task_id": None,
            "fanout_id": "fanout-prd-lanes-final-x",
        },
        correlation_id="trace-prd-judge",
    )
    orch._maybe_auto_ship(judge)

    types = [e.type for e in log.read_all()]
    assert "ship.completed" in types, f"PRD judge.passed did not ship; got {types}"
    main_after = _git_head(tmp_path, "main")
    assert main_after != main_before, "ship_target_branch did not advance"
    main_log = subprocess.run(
        ["git", "log", "--format=%H", "main"], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    assert candidate_head in main_log


def test_judge_flag_off_does_not_ship_even_if_candidate_flag_on(tmp_path: Path):
    """judge.passed must gate on ITS OWN flag — the cangjie candidate-complete
    flag must not make judge.passed ship (else it double/early-ships)."""
    from tests.test_ship import _candidate_branch, _candidate_ready, _init_repo

    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")

    pdd_id = "CJMIN-RTEST2"
    _candidate_branch(tmp_path, pdd_id, "f.txt", "x\n")
    orch, log = _orch(state_dir, judge_flag=False, candidate_flag=True)
    _candidate_ready(log, pdd_id)

    judge = ZfEvent(type="judge.passed", actor="zf-cli",
                    payload={"target_ref": f"candidate/{pdd_id}"})
    orch._maybe_auto_ship(judge)

    assert not any(e.type.startswith("ship.") for e in log.read_all())


def test_candidate_complete_does_not_ship_under_judge_flag_only(tmp_path: Path):
    """The judge flag must not make candidate.integration.completed ship — the
    cangjie pre-judge path stays gated on auto_ship_on_candidate_complete."""
    from tests.test_ship import _candidate_branch, _candidate_ready, _init_repo

    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")

    pdd_id = "CJMIN-RTEST3"
    _candidate_branch(tmp_path, pdd_id, "f.txt", "x\n")
    orch, log = _orch(state_dir, judge_flag=True, candidate_flag=False)
    _candidate_ready(log, pdd_id)

    trigger = ZfEvent(
        type="candidate.integration.completed",
        actor="zf-cli",
        payload={"branch": f"candidate/{pdd_id}", "pdd_id": pdd_id,
                 "quality_status": "passed"},
    )
    orch._maybe_auto_ship(trigger)

    assert not any(e.type.startswith("ship.") for e in log.read_all())
