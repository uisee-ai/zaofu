"""P0-1: WorkflowTopology wired into `zf validate --cold-start`.

Covers the four new capabilities:
- EXTERNAL_EVENTS whitelist for orphan / dead-end filtering
- handler_coverage() vs reactor handlers + wake_patterns
- TopologyReport one-shot aggregation
- `zf validate --cold-start` actually prints the topology section
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from contextlib import redirect_stdout

import pytest

from zf.core.config.loader import load_config
from zf.core.workflow.topology import (
    EXTERNAL_EVENTS,
    WorkflowTopology,
    TopologyReport,
)


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _load(name: str):
    return load_config(EXAMPLES / name)


def test_external_events_whitelist_has_core_kernel_events():
    """Sanity: the whitelist includes the obvious kernel-injected events."""
    for event in [
        "user.message", "task.assigned", "task.dispatched",
        "worker.stuck", "scope.violation", "cost.budget.exceeded",
        "gan.round.started", "discriminator.passed",
        "static_gate.passed", "static_gate.failed", "static_gate.skipped",
        "claude.hook.stop", "codex.hook.pre_tool_use",
    ]:
        assert event in EXTERNAL_EVENTS, f"{event} missing from EXTERNAL_EVENTS"


def test_wake_patterns_cover_exception_and_static_gate_events():
    """Exception events must not be silent, and static gate must wake routing."""
    from zf.runtime.wake_patterns import WAKE_PATTERNS

    wake = set(WAKE_PATTERNS)
    for event in [
        "clarification.needed",
        "dev.blocked",
        "dispatch.silent_stall",
        "worker.stuck",
        "static_gate.passed",
        "static_gate.failed",
        "static_gate.skipped",
    ]:
        assert event in wake


def test_safe_team_topology_clean_modulo_external():
    """safe-team.yaml should have zero orphans/dead-ends after filtering."""
    topology = WorkflowTopology.from_config(_load("safe-team.yaml"))
    assert topology.orphan_events() == []
    assert topology.dead_end_roles() == []


def test_include_external_flag_restores_raw_view():
    """include_external=True gives the unfiltered view (for debugging)."""
    topology = WorkflowTopology.from_config(_load("safe-team.yaml"))
    raw_orphans = topology.orphan_events(include_external=True)
    # safe-team orchestrator publishes task.dispatched/task.created/
    # feature.created — these are kernel events and should be orphans
    # only under include_external=True.
    assert "task.dispatched" in raw_orphans or "feature.created" in raw_orphans


def test_handler_coverage_detects_unwoken_handler():
    """If a reactor handler is missing from wake_patterns, flag it."""
    topology = WorkflowTopology.from_config(_load("safe-team.yaml"))
    # Fake reactor has a handler for `custom.event` but wake_patterns
    # doesn't include it → should appear in unwoken.
    fake_handlers = {"dev.build.done", "custom.event"}
    fake_wake = {"dev.build.done"}
    unhandled, unused, unwoken = topology.handler_coverage(
        reactor_handlers=fake_handlers,
        wake_patterns=fake_wake,
    )
    assert "custom.event" in unwoken


def test_handler_coverage_excludes_external_from_unused():
    """Kernel-injected events handled by reactor aren't "unused"."""
    topology = WorkflowTopology.from_config(_load("safe-team.yaml"))
    fake_handlers = {"gate.failed"}  # external, no role publishes it
    unhandled, unused, _ = topology.handler_coverage(
        reactor_handlers=fake_handlers, wake_patterns=set()
    )
    assert "gate.failed" not in unused


def test_topology_report_aggregates_clean():
    """TopologyReport.has_issues() is False when reactor/wake aligned."""
    from zf.runtime.wake_patterns import WAKE_PATTERNS, reactor_handler_events

    topology = WorkflowTopology.from_config(_load("safe-team.yaml"))
    report = topology.check(
        reactor_handlers=reactor_handler_events(),
        wake_patterns=set(WAKE_PATTERNS),
    )
    assert isinstance(report, TopologyReport)
    assert not report.has_issues()


def test_topology_report_aggregates_detects_issues():
    """TopologyReport.has_issues() is True when handler coverage gaps."""
    topology = WorkflowTopology.from_config(_load("safe-team.yaml"))
    # Narrow handler set — many role-published events become unhandled
    report = topology.check(
        reactor_handlers={"dev.build.done"},
        wake_patterns={"dev.build.done"},
    )
    assert report.has_issues()
    assert "arch.proposal.done" in report.unhandled_events


def test_topology_check_against_real_reactor_and_wake():
    """End-to-end: safe-team against the REAL reactor + wake patterns
    should be clean (no unhandled / unwoken). This is the regression
    guard that catches the LH-3 SUSPEND bug class."""
    from zf.runtime.wake_patterns import WAKE_PATTERNS, reactor_handler_events

    topology = WorkflowTopology.from_config(_load("safe-team.yaml"))
    report = topology.check(
        reactor_handlers=reactor_handler_events(),
        wake_patterns=set(WAKE_PATTERNS),
    )
    assert report.unwoken_events == [], (
        f"Silent route breaks (LH-3 bug class): {report.unwoken_events}"
    )


def test_validate_cold_start_prints_topology_section(tmp_path, monkeypatch):
    """`zf validate --cold-start` must print a 'Workflow Topology:'
    section. This verifies P0-1 wiring into the CLI."""
    # Copy safe-team.yaml into a minimal workspace
    import shutil

    workspace = tmp_path / "ws"
    workspace.mkdir()
    shutil.copy(EXAMPLES / "safe-team.yaml", workspace / "zf.yaml")
    (workspace / "README.md").write_text("x")
    (workspace / "CLAUDE.md").write_text("x")
    (workspace / "src").mkdir()
    (workspace / "tests").mkdir()
    state = workspace / ".zf"
    state.mkdir()
    (state / "events.jsonl").touch()

    from zf.cli import validate as validate_mod

    class _Args:
        path = str(workspace / "zf.yaml")
        cold_start = True
        architecture = False
        instructions = False

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = validate_mod.run(_Args())
    out = buf.getvalue()

    assert "Workflow Topology:" in out
    assert "Orphan events" in out
    assert "Dead-end roles" in out
    # Returns 0 or 1 depending on score, but shouldn't crash
    assert rc in (0, 1)
