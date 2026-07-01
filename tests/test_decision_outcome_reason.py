"""EVAL-DECISION-OUTCOME-001 tests — outcome_reason classification."""

from __future__ import annotations

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator import (
    _FAILED_REASONS,
    _NO_ACTION_REASONS,
    _classify_outcome_reason,
)
from zf.runtime.orchestrator_types import OrchestratorDecision


def _d(action: str, reason: str = "") -> OrchestratorDecision:
    return OrchestratorDecision(action=action, reason=reason)


def _ev(event_type: str) -> ZfEvent:
    return ZfEvent(type=event_type, actor="user")


# ---------------------------------------------------------------------------
# enumeration completeness
# ---------------------------------------------------------------------------


def test_no_action_reason_set_size() -> None:
    assert len(_NO_ACTION_REASONS) == 6
    assert "idle_sweep" in _NO_ACTION_REASONS
    assert "out_of_scope" in _NO_ACTION_REASONS


def test_failed_reason_set_size() -> None:
    assert len(_FAILED_REASONS) == 5
    assert "dispatch_blocked_by_rework_cap" in _FAILED_REASONS


# ---------------------------------------------------------------------------
# no_action paths
# ---------------------------------------------------------------------------


def test_no_action_no_trigger_is_idle_sweep() -> None:
    reason = _classify_outcome_reason(
        decision_kind="no_action", decisions=[], triggers=None,
    )
    assert reason == "idle_sweep"


def test_no_action_with_trigger_default_out_of_scope() -> None:
    reason = _classify_outcome_reason(
        decision_kind="no_action", decisions=[], triggers=[_ev("worker.heartbeat")],
    )
    assert reason == "out_of_scope"


def test_no_action_with_rework_cap_reason() -> None:
    reason = _classify_outcome_reason(
        decision_kind="no_action",
        decisions=[_d("skip", reason="rework cap reached for TASK-X")],
        triggers=[_ev("review.rejected")],
    )
    assert reason == "dispatch_blocked_by_rework_cap"


def test_no_action_with_not_ready_reason() -> None:
    reason = _classify_outcome_reason(
        decision_kind="no_action",
        decisions=[_d("skip", reason="task not ready (missing context_refs)")],
        triggers=[_ev("user.message")],
    )
    assert reason == "not_ready"


def test_no_action_with_dependency_reason() -> None:
    reason = _classify_outcome_reason(
        decision_kind="no_action",
        decisions=[_d("skip", reason="blocked by upstream T-1")],
        triggers=[_ev("task.dispatched")],
    )
    assert reason == "awaiting_dependency"


def test_no_action_with_all_steps_done() -> None:
    reason = _classify_outcome_reason(
        decision_kind="no_action",
        decisions=[_d("skip", reason="all steps complete")],
        triggers=[_ev("orchestrator.idle")],
    )
    assert reason == "all_steps_done"


def test_no_action_with_inline_skip() -> None:
    reason = _classify_outcome_reason(
        decision_kind="no_action",
        decisions=[_d("skip", reason="inline override: skip stage critic")],
        triggers=[_ev("user.message")],
    )
    assert reason == "inline_override_skip"


# ---------------------------------------------------------------------------
# blocked / failed paths
# ---------------------------------------------------------------------------


def test_blocked_with_rework_cap() -> None:
    reason = _classify_outcome_reason(
        decision_kind="blocked",
        decisions=[_d("block", reason="rework_cap exceeded")],
        triggers=[_ev("review.rejected")],
    )
    assert reason == "dispatch_blocked_by_rework_cap"


def test_blocked_with_circuit_open() -> None:
    reason = _classify_outcome_reason(
        decision_kind="blocked",
        decisions=[_d("block", reason="circuit breaker open for dev")],
        triggers=[_ev("worker.stuck")],
    )
    assert reason == "circuit_open"


def test_blocked_no_eligible_role() -> None:
    reason = _classify_outcome_reason(
        decision_kind="blocked",
        decisions=[_d("block", reason="no eligible role for trigger")],
        triggers=[_ev("user.message")],
    )
    assert reason == "no_eligible_role"


def test_blocked_gate_evidence_missing() -> None:
    reason = _classify_outcome_reason(
        decision_kind="blocked",
        decisions=[_d("block", reason="terminal evidence missing")],
        triggers=[_ev("judge.passed")],
    )
    assert reason == "gate_evidence_missing"


def test_blocked_scope_violation() -> None:
    reason = _classify_outcome_reason(
        decision_kind="blocked",
        decisions=[_d("block", reason="scope violation detected")],
        triggers=[_ev("dev.build.done")],
    )
    assert reason == "scope_violation"


# ---------------------------------------------------------------------------
# pass-through (dispatch / escalate / wait → empty outcome_reason)
# ---------------------------------------------------------------------------


def test_dispatch_returns_empty_reason() -> None:
    assert _classify_outcome_reason(
        decision_kind="dispatch",
        decisions=[_d("dispatch", reason="ready task")],
        triggers=[_ev("task.dispatched")],
    ) == ""


def test_escalate_returns_empty_reason() -> None:
    assert _classify_outcome_reason(
        decision_kind="escalate",
        decisions=[_d("escalate", reason="stuck worker")],
        triggers=[_ev("worker.stuck")],
    ) == ""


# ---------------------------------------------------------------------------
# payload integration — emit puts outcome_reason in event payload
# ---------------------------------------------------------------------------


def test_emit_decision_recorded_includes_outcome_reason() -> None:
    """The emit path puts outcome_reason in the event payload."""
    from zf.runtime.orchestrator import Orchestrator

    class _FakeWriter:
        appended: list[ZfEvent] = []
        def append(self, ev):
            self.appended.append(ev)

    class _FakeOrch:
        event_writer = _FakeWriter()

    orch = _FakeOrch()
    Orchestrator._emit_decision_recorded(
        orch,  # type: ignore[arg-type]
        [_ev("worker.heartbeat")],
        [],
    )
    assert len(_FakeWriter.appended) == 1
    payload = _FakeWriter.appended[0].payload
    assert "outcome_reason" in payload
    assert payload["outcome_reason"] == "out_of_scope"
    _FakeWriter.appended.clear()
