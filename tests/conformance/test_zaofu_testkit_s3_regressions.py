"""Strict regression probes discovered by the broad impact-closure run.

These probes intentionally remain strict xfails while the corresponding
production issues are open.  They keep the defects executable in the TestKit
registry without treating a known failure as an unclassified harness error.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.xfail(strict=True, reason="ZF-TEST-028")
def test_external_housekeeping_events_have_wake_patterns() -> None:
    """Externally emitted housekeeping events must wake the watcher."""

    source = (ROOT / "src/zf/runtime/orchestrator.py").read_text(encoding="utf-8")
    wake = (ROOT / "src/zf/runtime/wake_patterns.py").read_text(encoding="utf-8")
    for event_type in (
        "artifact.delivery.verified",
        "run.resumed",
        "self_issue.assessment.requested",
    ):
        assert event_type in wake, event_type


@pytest.mark.xfail(strict=True, reason="ZF-TEST-029")
def test_run_once_housekeeping_count_stays_within_frozen_budget() -> None:
    """The measured run_once housekeeping count must not exceed its budget."""
    from tests.test_tick_mechanism_budget import (
        RUN_ONCE_HOUSEKEEPING_BUDGET,
        _run_once_calls,
    )

    housekeeping, _ = _run_once_calls()
    assert len(housekeeping) <= RUN_ONCE_HOUSEKEEPING_BUDGET


@pytest.mark.xfail(strict=True, reason="ZF-TEST-031")
def test_dependency_barrier_dispatches_downstream_role(tmp_path: Path) -> None:
    """A satisfied dependency barrier must dispatch its downstream role."""

    from tests.test_workflow_dependency_barrier import (
        test_orchestrator_run_once_reconciles_and_dispatches_downstream,
    )

    test_orchestrator_run_once_reconciles_and_dispatches_downstream(tmp_path)
