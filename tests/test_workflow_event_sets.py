"""Tests for WorkflowEventSets — PREREQ-B (doc 40 §6 I57).

WorkflowEventSets.baseline() is the single source of truth for the 4
pipeline-event classifications previously hardcoded across
orchestrator_dispatch.py (3 sets) and rework_triage.py (1 set).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from zf.core.workflow.topology import (
    WorkflowEventSets,
    WorkflowTopology,
)


# ---------------------------------------------------------------------------
# Baseline content — locks the canonical set so any future change to
# WorkflowEventSets.baseline() is intentional and reviewed.
# ---------------------------------------------------------------------------


def test_baseline_handoff_success_events_locked() -> None:
    """Locks the canonical handoff_success set."""
    baseline = WorkflowEventSets.baseline()
    assert baseline.handoff_success_events == frozenset({
        "arch.proposal.done",
        "design.critique.done",
        "dev.build.done",
        "impl.child.completed",
        "static_gate.passed",
        "review.approved",
        "verify.passed",
        "test.passed",
        "judge.passed",
    })


def test_baseline_rework_trigger_events_locked() -> None:
    baseline = WorkflowEventSets.baseline()
    assert baseline.rework_trigger_events == frozenset({
        "review.rejected",
        "verify.failed",
        "test.failed",
        "judge.failed",
        "gate.failed",
        "discriminator.failed",
        "task.done.blocked",
        "dev.blocked",   # cangjie-mono drift fix 2026-05-18
        "dev.failed",
        "impl.child.failed",
        "review.child.failed",
        "verify.child.failed",
    })


def test_baseline_stage_progress_is_handoff_plus_rework() -> None:
    baseline = WorkflowEventSets.baseline()
    expected = (
        baseline.handoff_success_events
        | baseline.rework_trigger_events
        | frozenset({"static_gate.skipped"})
    )
    assert baseline.stage_progress_events == expected
    # 9 handoff + 12 rework + 1 equivalent-progress event = 22 distinct events
    assert len(baseline.stage_progress_events) == 22
    assert "static_gate.skipped" not in baseline.handoff_success_events


def test_baseline_rework_triage_adds_static_gate_failed() -> None:
    baseline = WorkflowEventSets.baseline()
    diff = baseline.rework_triage_trigger_events - baseline.rework_trigger_events
    assert diff == frozenset({"static_gate.failed"}), (
        "rework_triage_trigger should be rework_trigger + static_gate.failed"
    )


def test_baseline_is_frozen() -> None:
    """The dataclass should reject mutation."""
    baseline = WorkflowEventSets.baseline()
    with pytest.raises((AttributeError, TypeError)):
        baseline.handoff_success_events = frozenset()  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Dispatch / rework_triage delegation — these are the wire-up grep proofs.
# ---------------------------------------------------------------------------


def test_orchestrator_dispatch_class_attrs_delegate_to_baseline() -> None:
    """DispatchMixin._HANDOFF_SUCCESS_EVENTS / _STAGE_PROGRESS_EVENTS /
    _REWORK_TRIGGER_EVENTS must reference the baseline values; otherwise
    adding a new event to the baseline silently fails to propagate."""
    from zf.runtime.orchestrator_dispatch import DispatchMixin

    baseline = WorkflowEventSets.baseline()
    assert DispatchMixin._HANDOFF_SUCCESS_EVENTS == baseline.handoff_success_events
    assert DispatchMixin._STAGE_PROGRESS_EVENTS == baseline.stage_progress_events
    assert DispatchMixin._REWORK_TRIGGER_EVENTS == baseline.rework_trigger_events


def test_rework_triage_module_constant_delegates_to_baseline() -> None:
    from zf.runtime.rework_triage import REWORK_TRIAGE_TRIGGER_EVENTS

    baseline = WorkflowEventSets.baseline()
    assert REWORK_TRIAGE_TRIGGER_EVENTS == baseline.rework_triage_trigger_events


# ---------------------------------------------------------------------------
# cross_check_topology — drift detection
# ---------------------------------------------------------------------------


@dataclass
class _FakeRole:
    name: str
    publishes: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)


@dataclass
class _FakeConfig:
    roles: list[_FakeRole]
    workflow: object = field(
        default_factory=lambda: type("W", (), {"stages": []})()
    )


def _topology_from_roles(roles: list[_FakeRole]) -> WorkflowTopology:
    return WorkflowTopology.from_config(_FakeConfig(roles=roles))


def test_cross_check_topology_no_drift_for_baseline_events() -> None:
    """A topology that publishes exactly the baseline events should
    produce zero drift."""
    baseline = WorkflowEventSets.baseline()
    roles = [
        _FakeRole(name="arch", publishes=["arch.proposal.done"]),
        _FakeRole(name="critic", publishes=["design.critique.done"]),
        _FakeRole(name="dev", publishes=["dev.build.done"]),
        _FakeRole(name="static_gate", publishes=["static_gate.passed"]),
        _FakeRole(name="review", publishes=[
            "review.approved", "review.rejected",
        ]),
        _FakeRole(name="verify", publishes=[
            "verify.passed", "verify.failed",
        ]),
        _FakeRole(name="test", publishes=[
            "test.passed", "test.failed",
        ]),
        _FakeRole(name="judge", publishes=[
            "judge.passed", "judge.failed",
        ]),
    ]
    topology = _topology_from_roles(roles)
    drift = baseline.cross_check_topology(topology)
    assert drift == [], f"unexpected drift: {drift}"


def test_cross_check_topology_flags_new_success_event() -> None:
    """If a role publishes a new ``.done`` / ``.passed`` event not in the
    baseline, cross_check should flag it."""
    baseline = WorkflowEventSets.baseline()
    roles = [
        _FakeRole(name="newstage", publishes=["newstage.completed"]),
    ]
    topology = _topology_from_roles(roles)
    drift = baseline.cross_check_topology(topology)
    assert len(drift) == 1
    assert "newstage.completed" in drift[0]
    assert "handoff_success_events" in drift[0]


def test_cross_check_topology_flags_new_failure_event() -> None:
    baseline = WorkflowEventSets.baseline()
    roles = [
        _FakeRole(name="newstage", publishes=["newstage.failed"]),
    ]
    topology = _topology_from_roles(roles)
    drift = baseline.cross_check_topology(topology)
    assert len(drift) == 1
    assert "newstage.failed" in drift[0]
    assert "rework_trigger_events" in drift[0]


def test_cross_check_topology_ignores_non_pipeline_suffixes() -> None:
    """Events without success/failure suffix shouldn't appear in drift —
    notes, hooks, telemetry don't need classification."""
    baseline = WorkflowEventSets.baseline()
    roles = [
        _FakeRole(name="x", publishes=[
            "x.note", "x.hook.invoked", "x.telemetry",
        ]),
    ]
    topology = _topology_from_roles(roles)
    drift = baseline.cross_check_topology(topology)
    assert drift == []


def test_cross_check_topology_does_not_flag_classified_failures() -> None:
    """static_gate.failed is in rework_triage_trigger; cross_check
    should not flag it even though static_gate.failed isn't in
    rework_trigger_events directly."""
    baseline = WorkflowEventSets.baseline()
    roles = [
        _FakeRole(name="static_gate", publishes=[
            "static_gate.passed", "static_gate.failed",
        ]),
    ]
    topology = _topology_from_roles(roles)
    drift = baseline.cross_check_topology(topology)
    assert drift == []


# ---------------------------------------------------------------------------
# B-NEW-4 / B-NEW-9 / B-NEW-10 regression — adding a new pipeline event
# that's not in the baseline must surface via cross_check_topology,
# preventing silent strand.
# ---------------------------------------------------------------------------


def test_b_new_class_regression_new_pipeline_event_surfaces_in_drift() -> None:
    """If someone adds a new pipeline event in zf.yaml without updating
    WorkflowEventSets.baseline(), `zf validate` must flag it.

    Simulates: P3-style new gate after static_gate that publishes
    `meta_gate.passed` and `meta_gate.failed`.
    """
    baseline = WorkflowEventSets.baseline()
    roles = [
        _FakeRole(name="meta_gate", publishes=[
            "meta_gate.passed", "meta_gate.failed",
        ]),
    ]
    topology = _topology_from_roles(roles)
    drift = baseline.cross_check_topology(topology)
    assert len(drift) == 2, drift
    assert any("meta_gate.passed" in d for d in drift)
    assert any("meta_gate.failed" in d for d in drift)
