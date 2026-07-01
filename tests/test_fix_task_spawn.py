"""β-4: fix-task spawn (ralph failure-classifier Strategy 3).

Per docs/design/36-zero-touch-long-horizon-roadmap.md §7.1 (Strategy 3
only) + backlog backlogs/2026-05-17-1447-zero-touch-beta-self-healing.md
(β-4 section).

When review / test / judge reports a **local CRITICAL** bug spanning
≥2 task ids, zaofu's existing rework path requeues the original task
(覆盖式重做). ralph's better strategy: keep the original task as done,
create a new **fix-task** on the same feature backlog linked via
``contract.fix_of``. Operator can see the lineage; failures don't
discard completed work.

Conservative trigger conditions (avoid noisy fix-task explosions):
  - event.payload.severity == "critical"
  - event.payload.scope == "local"
  - event.payload.affected_task_ids has ≥ 2 entries

When ANY condition is missing, fall through to standard rework_routing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract


# ─── classifier: should_spawn_fix_task ───────────────────────────────────


def _review_rejected(**payload) -> ZfEvent:
    return ZfEvent(
        type="review.rejected",
        actor="review",
        task_id=payload.get("task_id", "TASK-A"),
        payload=payload,
    )


def test_classifier_triggers_on_local_critical_multi_task():
    from zf.runtime.fix_task_spawn import should_spawn_fix_task

    event = _review_rejected(
        severity="critical",
        scope="local",
        affected_task_ids=["TASK-A", "TASK-B"],
        summary="typo in validation regex breaks task A and task B",
    )
    should, reason = should_spawn_fix_task(event)

    assert should is True
    assert "critical" in reason.lower() or "local" in reason.lower() or \
        "multi" in reason.lower()


def test_classifier_no_spawn_on_systemic_critical():
    """systemic-scope failures go to the upstream redo path (Strategy 1),
    not Strategy 3 fix-task spawn."""
    from zf.runtime.fix_task_spawn import should_spawn_fix_task

    event = _review_rejected(
        severity="critical",
        scope="systemic",  # ← upstream
        affected_task_ids=["TASK-A", "TASK-B"],
    )
    should, _reason = should_spawn_fix_task(event)

    assert should is False


def test_classifier_no_spawn_on_minor():
    """MINOR severity → release notes annotation (Strategy 4), not
    fix-task spawn."""
    from zf.runtime.fix_task_spawn import should_spawn_fix_task

    event = _review_rejected(
        severity="minor",
        scope="local",
        affected_task_ids=["TASK-A", "TASK-B"],
    )
    should, _reason = should_spawn_fix_task(event)

    assert should is False


def test_classifier_no_spawn_on_single_task():
    """One task affected → standard rework path (Strategy 2)."""
    from zf.runtime.fix_task_spawn import should_spawn_fix_task

    event = _review_rejected(
        severity="critical",
        scope="local",
        affected_task_ids=["TASK-A"],
    )
    should, _reason = should_spawn_fix_task(event)

    assert should is False


def test_classifier_no_spawn_when_payload_lacks_fields():
    """Conservative default: payloads without severity/scope/affected
    keys → fall through to existing rework. (Doesn't break current
    cangjie events that don't yet emit these fields.)"""
    from zf.runtime.fix_task_spawn import should_spawn_fix_task

    event = _review_rejected(
        summary="some plain rejection without ralph-style annotations",
    )
    should, _reason = should_spawn_fix_task(event)

    assert should is False


def test_classifier_handles_test_failed_and_judge_failed():
    """Strategy 3 applies to any rework trigger event type, not just
    review.rejected."""
    from zf.runtime.fix_task_spawn import should_spawn_fix_task

    for event_type in ("test.failed", "judge.failed", "gate.failed"):
        event = ZfEvent(
            type=event_type,
            actor="test" if "test" in event_type else "judge",
            task_id="TASK-A",
            payload={
                "severity": "critical",
                "scope": "local",
                "affected_task_ids": ["TASK-A", "TASK-B"],
            },
        )
        should, _reason = should_spawn_fix_task(event)
        assert should is True, f"{event_type} should trigger spawn"


# ─── TaskContract.fix_of ─────────────────────────────────────────────────


def test_task_contract_has_fix_of_field():
    """β-4 adds TaskContract.fix_of to link a fix-task to its origin."""
    from zf.core.task.schema import TaskContract

    contract = TaskContract()
    assert hasattr(contract, "fix_of")
    assert contract.fix_of == ""

    contract = TaskContract(fix_of="TASK-ORIGIN")
    assert contract.fix_of == "TASK-ORIGIN"


# ─── build_fix_task_payload ──────────────────────────────────────────────


def test_build_fix_task_creates_linked_payload():
    """Helper that produces a Task ready for TaskStore.add (or kanban)
    with fix_of linkage + inherits feature_id from parent."""
    from zf.runtime.fix_task_spawn import build_fix_task

    parent = Task(
        id="TASK-A",
        title="implement validation",
        contract=TaskContract(
            feature_id="F-deadbeef",
            behavior="add validation regex",
            scope=["validator.py"],
        ),
    )
    trigger = _review_rejected(
        severity="critical",
        scope="local",
        affected_task_ids=["TASK-A", "TASK-B"],
        summary="typo in regex",
    )

    fix_task = build_fix_task(parent, trigger)

    assert fix_task.contract is not None
    assert fix_task.contract.fix_of == "TASK-A"
    assert fix_task.contract.feature_id == "F-deadbeef"
    assert "typo" in fix_task.title.lower() or "fix" in fix_task.title.lower()
    # New task gets its own id, NOT the parent's
    assert fix_task.id != "TASK-A"


# ─── event registration ──────────────────────────────────────────────────


def test_task_fix_spawned_in_known_types():
    from zf.core.events.known_types import KNOWN_EVENT_TYPES

    assert "task.fix_spawned" in KNOWN_EVENT_TYPES


def test_task_fix_spawned_in_wake_patterns():
    from zf.runtime.wake_patterns import WAKE_PATTERNS

    assert "task.fix_spawned" in WAKE_PATTERNS


# ─── wire-up grep ────────────────────────────────────────────────────────


def test_wire_up_orchestrator_dispatch_uses_should_spawn_fix_task():
    """β-4 wire-up grep: _dispatch_rework must check should_spawn_fix_task
    before defaulting to requeue."""
    src = Path(__file__).resolve().parents[1] / "src/zf/runtime/orchestrator_dispatch.py"
    text = src.read_text(encoding="utf-8")
    assert "should_spawn_fix_task" in text or "build_fix_task" in text, (
        "β-4 wire-up missing: orchestrator_dispatch.py does not call "
        "should_spawn_fix_task / build_fix_task"
    )
