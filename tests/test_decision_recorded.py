"""ZF-ORCH-ACT-001 — orchestrator.decision.recorded summary event tests.

Acceptance §2: every run_once wake (with trigger or with decisions)
emits exactly one decision.recorded event, classified by what
happened (dispatch / blocked / escalate / wait / no_action).
"""

from __future__ import annotations

import pytest

from zf.core.events.known_types import KNOWN_EVENT_TYPES
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_types import OrchestratorDecision


# ---------------------------------------------------------------------------
# Event-registry wire-up
# ---------------------------------------------------------------------------


def test_decision_recorded_is_known_event() -> None:
    assert "orchestrator.decision.recorded" in KNOWN_EVENT_TYPES


def test_decision_recorded_NOT_in_wake_patterns() -> None:
    """Orchestrator self-emits this event at end of run_once. If it
    were in WAKE_PATTERNS, EventWatcher would trigger a new run_once
    on next tick → infinite loop. Must stay OUT of WAKE_PATTERNS."""
    from zf.runtime.wake_patterns import WAKE_PATTERNS

    assert "orchestrator.decision.recorded" not in WAKE_PATTERNS


# ---------------------------------------------------------------------------
# _emit_decision_recorded semantics — unit-tested against a fake
# orchestrator that captures the emitted event.
# ---------------------------------------------------------------------------


class _FakeWriter:
    def __init__(self) -> None:
        self.appended: list[ZfEvent] = []

    def append(self, event: ZfEvent) -> None:
        self.appended.append(event)


class _FakeOrch:
    """Carries only the attributes _emit_decision_recorded reads."""

    def __init__(self) -> None:
        self.event_writer = _FakeWriter()


def _trigger(event_type: str = "dev.build.done") -> ZfEvent:
    return ZfEvent(type=event_type, actor="dev-1", payload={})


def _decision(action: str, **kw) -> OrchestratorDecision:
    return OrchestratorDecision(action=action, **kw)


def test_silent_wake_does_not_emit() -> None:
    """No trigger + no decisions → no emit (idle noise filter)."""
    orch = _FakeOrch()
    Orchestrator._emit_decision_recorded(orch, None, [])  # type: ignore[arg-type]
    assert orch.event_writer.appended == []


def test_dispatch_decision_classified_correctly() -> None:
    orch = _FakeOrch()
    decisions = [_decision("dispatch", task_id="TASK-1", role="dev",
                            reason="trigger fired")]
    Orchestrator._emit_decision_recorded(
        orch, [_trigger("user.message")], decisions,
    )  # type: ignore[arg-type]
    assert len(orch.event_writer.appended) == 1
    event = orch.event_writer.appended[0]
    assert event.type == "orchestrator.decision.recorded"
    assert event.payload["decision"] == "dispatch"
    assert event.payload["trigger_event_type"] == "user.message"
    assert event.payload["task_id"] == "TASK-1"
    assert event.payload["target_role"] == "dev"
    assert event.payload["decision_count"] == 1


def test_block_decision_classified_correctly() -> None:
    orch = _FakeOrch()
    decisions = [_decision("block", task_id="TASK-2",
                            reason="rework cap reached")]
    Orchestrator._emit_decision_recorded(
        orch, [_trigger("test.failed")], decisions,
    )  # type: ignore[arg-type]
    assert orch.event_writer.appended[0].payload["decision"] == "blocked"


def test_escalate_decision_classified_correctly() -> None:
    orch = _FakeOrch()
    decisions = [_decision("respawn", task_id="TASK-3")]
    Orchestrator._emit_decision_recorded(
        orch, [_trigger("worker.stuck")], decisions,
    )  # type: ignore[arg-type]
    assert orch.event_writer.appended[0].payload["decision"] == "escalate"


def test_wait_decision_classified_for_non_action_actions() -> None:
    """capture / skip etc. are bookkeeping but happened → wait."""
    orch = _FakeOrch()
    decisions = [_decision("capture", task_id="TASK-4")]
    Orchestrator._emit_decision_recorded(
        orch, [_trigger("agent.usage")], decisions,
    )  # type: ignore[arg-type]
    assert orch.event_writer.appended[0].payload["decision"] == "wait"


def test_no_decisions_but_with_trigger_classifies_as_no_action() -> None:
    """A wake triggered by an event but producing no decisions →
    no_action (kernel acknowledged the event but had nothing to do)."""
    orch = _FakeOrch()
    Orchestrator._emit_decision_recorded(
        orch, [_trigger("worker.heartbeat")], [],
    )  # type: ignore[arg-type]
    assert orch.event_writer.appended[0].payload["decision"] == "no_action"


def test_dispatch_wins_over_other_classifications() -> None:
    """If a wake has mixed decisions (dispatch + block), dispatch
    wins — at least one piece of forward progress is the signal
    operators most want surfaced."""
    orch = _FakeOrch()
    decisions = [
        _decision("dispatch", task_id="TASK-A", role="dev"),
        _decision("block", task_id="TASK-B"),
    ]
    Orchestrator._emit_decision_recorded(orch, None, decisions)  # type: ignore[arg-type]
    assert orch.event_writer.appended[0].payload["decision"] == "dispatch"


def test_reasons_are_collected_into_payload() -> None:
    orch = _FakeOrch()
    decisions = [
        _decision("dispatch", task_id="TASK-1", reason="first reason"),
        _decision("dispatch", task_id="TASK-2", reason="second reason"),
    ]
    Orchestrator._emit_decision_recorded(orch, None, decisions)  # type: ignore[arg-type]
    payload = orch.event_writer.appended[0].payload
    assert "first reason" in payload["reasons"]
    assert "second reason" in payload["reasons"]


def test_first_task_role_extracted_for_summary() -> None:
    """When multiple decisions touch different tasks, the summary
    event records the first task/role for breadcrumb purposes."""
    orch = _FakeOrch()
    decisions = [
        _decision("dispatch", task_id="TASK-FIRST", role="dev"),
        _decision("dispatch", task_id="TASK-SECOND", role="review"),
    ]
    Orchestrator._emit_decision_recorded(orch, None, decisions)  # type: ignore[arg-type]
    payload = orch.event_writer.appended[0].payload
    assert payload["task_id"] == "TASK-FIRST"
    assert payload["target_role"] == "dev"


def test_payload_caps_actions_and_reasons_lists() -> None:
    """Very chatty wakes shouldn't bloat events.jsonl. Lists are
    truncated to 20 actions / 10 reasons."""
    orch = _FakeOrch()
    decisions = [_decision("dispatch", reason=f"r-{i}") for i in range(50)]
    Orchestrator._emit_decision_recorded(orch, None, decisions)  # type: ignore[arg-type]
    payload = orch.event_writer.appended[0].payload
    assert len(payload["actions"]) <= 20
    assert len(payload["reasons"]) <= 10


# ---------------------------------------------------------------------------
# Wire-up grep proof — Orchestrator.run_once must invoke
# _emit_decision_recorded.
# ---------------------------------------------------------------------------


def test_orchestrator_has_emit_decision_recorded_method() -> None:
    assert hasattr(Orchestrator, "_emit_decision_recorded")


def test_run_once_invokes_emit_decision_recorded() -> None:
    """Source-level grep proof — _emit_decision_recorded must be
    called from run_once (else Class D library-without-callers)."""
    import inspect

    source = inspect.getsource(Orchestrator.run_once)
    assert "_emit_decision_recorded" in source, (
        "run_once does not invoke _emit_decision_recorded"
    )
