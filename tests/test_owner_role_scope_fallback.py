"""#H-1 fix: _initial_role_for_ready_task falls back to dev when
plan declares owner_role=arch but scope contains implementation
paths (cangjie 2026-05-21 observation-H).

Pre-fix: plan-level owner_role is taken verbatim. cangjie 2026-05-21
round 2 plan declared owner_role=arch for all 54 vertical (arch skill
default leak). arch single instance overloaded; 4 dev replicas idle.

Fix: when contract.owner_role is arch/critic but scope suggests
writer paths (src/test/packages/.ts/.py), fallback to dev role.
This is a defense-in-depth against plan-level owner_role misconfig.

Refs: tasks/2026-05-21-0930-owner-role-fallback-and-plan-fanout-feasibility-D.md
Cangjie incident: docs/records/incidents/2026-05-21-observation-H-plan-arch-only-owner-role.md
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.task.schema import Task, TaskContract
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


def _make_orch(tmp_path: Path) -> Orchestrator:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").touch()
    (state_dir / "session.yaml").write_text("runtime_state: running\n")
    cfg = ZfConfig(
        project=ProjectConfig(name="hfallback"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(
                name="arch", backend="mock", role_kind="reader",
                triggers=["task.assigned"],
            ),
            RoleConfig(
                name="dev", backend="mock", role_kind="writer",
                triggers=["task.assigned"], replicas=4,
            ),
        ],
    )
    transport = TmuxTransport(TmuxSession(session_name="ht", dry_run=True))
    return Orchestrator(state_dir, cfg, transport)


def _task(task_id: str, owner_role: str, scope: list[str]) -> Task:
    return Task(
        id=task_id,
        title=task_id,
        key=f"F-test:{task_id.lower()}",
        status="backlog",
        priority=3,
        contract=TaskContract(
            behavior=f"test {task_id}",
            verification="pnpm test",
            scope=scope,
            owner_role=owner_role,
        ),
    )


# ─── core fallback cases ─────────────────────────────────────────────────


def test_arch_owner_with_dev_scope_fallbacks_to_dev(tmp_path: Path):
    """plan owner_role=arch + scope='src/foo.ts' → resolved to dev."""
    orch = _make_orch(tmp_path)
    task = _task("TASK-X", owner_role="arch", scope=[
        "src/foo.ts",
        "test/unit/foo.test.ts",
    ])
    target = orch._initial_role_for_ready_task(task)
    assert target == "dev", (
        f"#H-1 fix: arch owner with dev scope should fallback to dev; got {target!r}"
    )


def test_critic_owner_with_packages_scope_fallbacks_to_dev(tmp_path: Path):
    """plan owner_role=critic + scope='packages/ai/index.ts' → resolved dev."""
    orch = _make_orch(tmp_path)
    task = _task("TASK-Y", owner_role="critic", scope=[
        "packages/ai/index.ts",
    ])
    target = orch._initial_role_for_ready_task(task)
    assert target == "dev"


# ─── preservation cases (don't over-trigger fallback) ────────────────────


def test_dev_owner_unchanged(tmp_path: Path):
    """plan owner_role=dev → kept as dev (no fallback needed)."""
    orch = _make_orch(tmp_path)
    task = _task("TASK-Z", owner_role="dev", scope=["src/bar.ts"])
    target = orch._initial_role_for_ready_task(task)
    assert target == "dev"


def test_arch_owner_with_docs_scope_stays_arch(tmp_path: Path):
    """plan owner_role=arch + scope=docs/plans/X.md → stay arch
    (legitimately a design task)."""
    orch = _make_orch(tmp_path)
    task = _task("TASK-DESIGN", owner_role="arch", scope=[
        "docs/plans/cangjie-master-plan.md",
        "docs/specs/foundations/blueprint.md",
    ])
    target = orch._initial_role_for_ready_task(task)
    assert target == "arch"


def test_arch_owner_with_mixed_scope_fallbacks_to_dev(tmp_path: Path):
    """plan owner_role=arch + scope mixed docs+src → fallback dev
    (any dev path triggers fallback,保守看待 implementation)."""
    orch = _make_orch(tmp_path)
    task = _task("TASK-M", owner_role="arch", scope=[
        "docs/specs/note.md",
        "src/zf/cli/foo.ts",
    ])
    target = orch._initial_role_for_ready_task(task)
    assert target == "dev"


def test_owner_instance_takes_precedence(tmp_path: Path):
    """owner_instance pinned (e.g. dev-2) → returned verbatim
    (operator explicitly pinned, don't touch)."""
    from dataclasses import replace
    orch = _make_orch(tmp_path)
    task = _task("TASK-P", owner_role="arch", scope=["src/foo.ts"])
    task = replace(task, contract=replace(task.contract, owner_instance="dev-2"))
    target = orch._initial_role_for_ready_task(task)
    assert target == "dev-2"


def test_empty_scope_arch_owner_unchanged(tmp_path: Path):
    """No scope (impossible per spec_ingest validation but defensive) →
    stays arch (no info to fallback on)."""
    orch = _make_orch(tmp_path)
    task = _task("TASK-EMPTY", owner_role="arch", scope=[])
    target = orch._initial_role_for_ready_task(task)
    assert target == "arch"
