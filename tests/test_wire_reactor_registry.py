"""P0-2 wire-up: prove EventActionRegistry is actually used by the
runtime Orchestrator (not library-without-callers)."""

from __future__ import annotations

from pathlib import Path


def test_orchestrator_builds_event_registry_in_init():
    """Orchestrator.__init__ calls _build_event_registry()."""
    src = (
        Path(__file__).resolve().parents[1]
        / "src/zf/runtime/orchestrator.py"
    ).read_text()
    assert "self.event_registry = self._build_event_registry()" in src


def test_run_once_resolves_via_registry_not_dict_lookup():
    """run_once uses event_registry.resolve instead of _event_handlers()."""
    src = (
        Path(__file__).resolve().parents[1]
        / "src/zf/runtime/orchestrator.py"
    ).read_text()
    assert "self.event_registry.resolve(event.type)" in src


def test_reactor_mixin_has_builtin_table():
    """The _BUILTIN_HANDLER_METHODS tuple is the source of truth for
    which events have built-in handlers. wake_patterns.py + topology
    check + registry all read from it."""
    src = (
        Path(__file__).resolve().parents[1]
        / "src/zf/runtime/orchestrator_reactor.py"
    ).read_text()
    assert "_BUILTIN_HANDLER_METHODS" in src
    # Core event types listed
    for event in (
        "dev.build.done", "arch.proposal.done", "design.critique.done",
        "review.approved", "review.rejected", "review.suspended",
        "verify.passed", "verify.failed",
        "test.passed", "test.failed", "test.suspended",
        "judge.passed", "judge.failed",
        "dev.blocked", "gate.failed",
    ):
        assert f'"{event}"' in src


def test_config_workflow_accepts_event_actions():
    """WorkflowConfig has an event_actions field."""
    from zf.core.config.schema import WorkflowConfig
    wc = WorkflowConfig(event_actions=[
        {"event": "x", "actions": [{"type": "noop"}]},
    ])
    assert wc.event_actions[0]["event"] == "x"


def test_loader_passes_event_actions_through():
    """config loader wires yaml workflow.event_actions into config."""
    src = (
        Path(__file__).resolve().parents[1]
        / "src/zf/core/config/loader.py"
    ).read_text()
    assert "event_actions=workflow_data.get(\"event_actions\"" in src


def test_reactor_handler_events_reads_from_builtin_table():
    """wake_patterns.reactor_handler_events imports the single source."""
    src = (
        Path(__file__).resolve().parents[1]
        / "src/zf/runtime/wake_patterns.py"
    ).read_text()
    assert "_BUILTIN_HANDLER_METHODS" in src
