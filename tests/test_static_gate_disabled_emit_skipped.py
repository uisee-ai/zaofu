"""#J fix: `_maybe_run_static_gate` must emit `static_gate.skipped`
when DAG enabled + static.enabled=False (cangjie 2026-05-21
observation-J).

Pre-fix: `_maybe_run_static_gate` calls `is_static_gate_enabled_for_event`
which returns False when `quality_gates.static.enabled=False`, causing
the orchestrator to silently no-op without emitting any event.
review.triggers=[static_gate.passed] never wakes, P*V* chain stalls
post-dev.build.done.

Fix: `_maybe_run_static_gate` always runs `run_static_gate` when DAG
enabled, and emits the resulting event (which is `static_gate.skipped`
in disabled case). `run_static_gate` already returns
`StaticGateResult(passed=True, skipped=True, skip_reason="...")` for
disabled gates (line 111-114).

Refs: tasks/2026-05-21-1100-static-gate-disabled-emit-skipped-event.md
Cangjie incident: docs/records/incidents/2026-05-21-observation-J-static-gate-disabled-no-event-emit.md
"""

from __future__ import annotations

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
from zf.runtime.static_gate import build_static_gate_event, run_static_gate


def _cfg(*, dag_enabled: bool, static_enabled: bool, commands: list[str] | None = None) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="jt"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        workflow=WorkflowConfig(dag=WorkflowDagConfig(enabled=dag_enabled)),
        quality_gates={
            "static": QualityGateConfig(
                enabled=static_enabled,
                required_checks=commands or [],
            ),
        },
    )


# ─── core: disabled static gate emits skipped via build_static_gate_event ──


def test_run_static_gate_disabled_returns_skipped_result(tmp_path: Path):
    """run_static_gate with enabled=False returns skipped=True."""
    cfg = _cfg(dag_enabled=True, static_enabled=False)
    result = run_static_gate(config=cfg, project_root=tmp_path)
    assert result.skipped is True
    assert result.passed is True
    assert "enabled=False" in (result.skip_reason or "")


def test_build_event_for_skipped_emits_static_gate_skipped(tmp_path: Path):
    """skipped result → event type = static_gate.skipped (not passed/failed)."""
    cfg = _cfg(dag_enabled=True, static_enabled=False)
    result = run_static_gate(config=cfg, project_root=tmp_path)

    trigger = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-X",
        payload={"dispatch_id": "disp-test"},
    )
    event = build_static_gate_event(result, trigger_event=trigger)
    assert event.type == "static_gate.skipped"
    assert event.task_id == "TASK-X"
    # dispatch_id inherited
    assert event.payload.get("dispatch_id") == "disp-test"


# ─── orchestrator integration: _maybe_run_static_gate emits skipped ──────


def test_orchestrator_maybe_run_static_gate_emits_skipped_when_disabled(
    tmp_path: Path,
):
    """When DAG enabled + static.enabled=False + event=dev.build.done →
    _maybe_run_static_gate must emit static_gate.skipped.

    This is the #J fix: previously the function short-circuited via
    is_static_gate_enabled_for_event and emitted nothing, blocking
    review.triggers=static_gate.passed downstream.
    """
    from zf.runtime.orchestrator import Orchestrator
    from zf.runtime.tmux import TmuxSession
    from zf.runtime.transport import TmuxTransport

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").touch()
    (state_dir / "session.yaml").write_text("runtime_state: running\n")

    cfg = _cfg(dag_enabled=True, static_enabled=False, commands=["pnpm test"])
    transport = TmuxTransport(TmuxSession(session_name="sg-skipped-test", dry_run=True))
    orch = Orchestrator(state_dir, cfg, transport)

    trigger = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-X",
        payload={"dispatch_id": "disp-test"},
    )

    # First emit the trigger event so events.jsonl has the cause
    orch.event_writer.append(trigger)

    orch._maybe_run_static_gate(trigger)

    # Read back events.jsonl
    import json
    lines = (state_dir / "events.jsonl").read_text().strip().splitlines()
    events = [json.loads(l) for l in lines]
    skipped_events = [e for e in events if e.get("type") == "static_gate.skipped"]

    assert len(skipped_events) >= 1, (
        f"#J fix: expected static_gate.skipped event after dev.build.done "
        f"with disabled gate; got types: {[e.get('type') for e in events]}"
    )
    skipped = skipped_events[0]
    assert skipped.get("task_id") == "TASK-X"
    assert (skipped.get("payload") or {}).get("skipped") is True


def test_dag_disabled_legacy_noop_still_silent(tmp_path: Path):
    """DAG disabled + static.enabled=False → legacy mode, no event emitted.

    Pre-DAG projects shouldn't get spurious static_gate.skipped events.
    """
    from zf.runtime.orchestrator import Orchestrator
    from zf.runtime.tmux import TmuxSession
    from zf.runtime.transport import TmuxTransport

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").touch()
    (state_dir / "session.yaml").write_text("runtime_state: running\n")

    cfg = _cfg(dag_enabled=False, static_enabled=False)
    transport = TmuxTransport(TmuxSession(session_name="sg-noop-test", dry_run=True))
    orch = Orchestrator(state_dir, cfg, transport)

    trigger = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-X",
        payload={},
    )
    orch.event_writer.append(trigger)
    orch._maybe_run_static_gate(trigger)

    import json
    lines = (state_dir / "events.jsonl").read_text().strip().splitlines()
    events = [json.loads(l) for l in lines]
    static_events = [
        e for e in events
        if e.get("type", "").startswith("static_gate.")
    ]
    assert len(static_events) == 0, (
        f"DAG disabled should not emit any static_gate.* event; got: "
        f"{[e.get('type') for e in static_events]}"
    )
