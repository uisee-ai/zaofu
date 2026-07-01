"""#E fix: task contract.quality_gates_override.static.enabled=False
makes _maybe_run_static_gate emit skipped without running yaml-level
required_checks (cangjie 2026-05-21 observation-E).

Pre-fix: yaml `quality_gates.static.required_checks` is global; a
doc-type task with scope=docs/** got rejected because the yaml runs
`pnpm install` even though there's no package.json. cangjie operator
edited zf.yaml to disable static gate globally (workaround #E), but
that broke quality gates for the upcoming code-type tasks.

Fix: TaskContract gains `quality_gates_override: dict` field. When
`task.contract.quality_gates_override.static.enabled=False`, the
orchestrator skips yaml-level required_checks and emits
static_gate.skipped directly. Default empty dict preserves yaml
behavior for backward compat.

Refs: tasks/2026-05-21-0821-static-gate-per-task-quality-gate-override.md
Cangjie incident: docs/records/incidents/2026-05-21-bug-E-static-gate-coupling.md
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    QualityGateConfig,
    RoleConfig,
    SessionConfig,
    WorkflowConfig,
    WorkflowDagConfig,
    ZfConfig,
)
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


def _cfg() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="pertaskoverride"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer",
                          triggers=["task.assigned"])],
        workflow=WorkflowConfig(dag=WorkflowDagConfig(enabled=True)),
        quality_gates={
            # yaml-level: real pnpm command (will fail without package.json)
            "static": QualityGateConfig(
                enabled=True,
                required_checks=["pnpm install --frozen-lockfile"],
            ),
        },
    )


def _make_orch(tmp_path: Path) -> Orchestrator:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").touch()
    (state_dir / "session.yaml").write_text("runtime_state: running\n")
    transport = TmuxTransport(TmuxSession(session_name="pertask", dry_run=True))
    return Orchestrator(state_dir, _cfg(), transport)


def _add_task(orch: Orchestrator, task_id: str, override: dict) -> Task:
    store = TaskStore(orch.state_dir / "kanban.json")
    task = Task(
        id=task_id,
        title=task_id,
        key=f"F-test:{task_id.lower()}",
        status="in_progress",
        contract=TaskContract(
            behavior="t",
            verification="pnpm test",
            scope=["docs/plans/x.md"],
            owner_role="dev",
            quality_gates_override=override,
        ),
    )
    store.add(task)
    return task


# ─── schema / dataclass ──────────────────────────────────────────────────


def test_taskcontract_has_quality_gates_override_field():
    """#E fix: TaskContract supports per-task gate override."""
    c = TaskContract()
    assert hasattr(c, "quality_gates_override")
    assert c.quality_gates_override == {}, "default empty preserves yaml behavior"

    c2 = TaskContract(
        quality_gates_override={"static": {"enabled": False}},
    )
    assert c2.quality_gates_override == {"static": {"enabled": False}}


# ─── core behavior: per-task override emits skipped ──────────────────────


def test_per_task_override_disables_static_gate_emits_skipped(tmp_path: Path):
    """task contract override static.enabled=False → emit
    static_gate.skipped without running yaml pnpm command."""
    orch = _make_orch(tmp_path)
    _add_task(orch, "TASK-DOC", override={"static": {"enabled": False}})

    trigger = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-DOC",
        payload={"dispatch_id": "disp-test"},
    )
    orch.event_writer.append(trigger)
    orch._maybe_run_static_gate(trigger)

    lines = (orch.state_dir / "events.jsonl").read_text().strip().splitlines()
    events = [json.loads(l) for l in lines]
    skipped = [e for e in events if e.get("type") == "static_gate.skipped"]
    failed = [e for e in events if e.get("type") == "static_gate.failed"]

    assert len(skipped) >= 1, (
        f"#E fix: per-task override should emit static_gate.skipped; "
        f"got types: {[e.get('type') for e in events]}"
    )
    assert len(failed) == 0, (
        f"#E fix: should NOT run yaml pnpm command; "
        f"failed events: {failed}"
    )


def test_no_override_runs_yaml_default(tmp_path: Path):
    """No per-task override → yaml-level required_checks runs (will fail
    here because no package.json, but the bug we're testing is per-task,
    so just confirm yaml fires)."""
    orch = _make_orch(tmp_path)
    _add_task(orch, "TASK-CODE", override={})  # empty override

    trigger = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-CODE",
        payload={"dispatch_id": "disp-test"},
    )
    orch.event_writer.append(trigger)
    orch._maybe_run_static_gate(trigger)

    lines = (orch.state_dir / "events.jsonl").read_text().strip().splitlines()
    events = [json.loads(l) for l in lines]
    # yaml-level pnpm command runs; without package.json it fails → static_gate.failed.
    # The point isn't that it failed (env-dependent), but that yaml ran (not skipped via override).
    fired = [
        e for e in events
        if e.get("type", "").startswith("static_gate.")
    ]
    assert len(fired) >= 1, (
        f"yaml-level static_gate should fire when no per-task override; "
        f"got: {[e.get('type') for e in events]}"
    )
    # Confirm it wasn't shortcut-skipped via override
    skipped = [e for e in fired if e.get("type") == "static_gate.skipped"]
    if skipped:
        # If skipped, it must be due to required_checks empty / disabled at yaml level,
        # not per-task override (we passed override={})
        skip_reason = (skipped[0].get("payload") or {}).get("skip_reason", "")
        assert "per-task" not in skip_reason
