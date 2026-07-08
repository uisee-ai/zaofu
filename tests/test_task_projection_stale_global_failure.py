"""Web task projection: a global failure that predates a task must not be
attributed to it.

feishu e2e regression: a ZaoFu project reused across many rounds accumulates
stale ``prd.blocked`` / candidate failures from earlier runs (task_id=None,
matched only by a shared/empty context ref such as feature_id). Without a
temporal guard, ``_workflow_events_with_candidate_context`` injects a phantom
``review.rejected`` onto a brand-new task -> verify_state=failed -> the webkanban
card shows "blocked" while the task is actually in_progress. The guard: a global
failure whose append-order seq is BEFORE the task's first event cannot be its
failure.
"""

from __future__ import annotations

from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.web.projections.tasks import _workflow_events_with_candidate_context


def _task() -> Task:
    return Task(
        id="TASK-RACING",
        title="three.js racing demo",
        status="in_progress",
        assigned_to="prd-author",
        contract=TaskContract(feature_id="F-RACING"),
    )


def _created() -> ZfEvent:
    return ZfEvent(
        type="task.created", actor="zf-cli", task_id="TASK-RACING",
        payload={"task_id": "TASK-RACING"},
    )


def _prd_blocked(reason: str) -> ZfEvent:
    # task_id=None, matched to the task only by the shared feature_id ref.
    return ZfEvent(
        type="prd.blocked", actor="zf-cli",
        payload={"feature_id": "F-RACING", "reason": reason},
    )


def test_global_failure_before_task_creation_not_attributed(tmp_path: Path):
    stale = _prd_blocked("stale failure from an earlier round")
    created = _created()
    all_events = [(0, stale), (1, created)]  # stale precedes the task
    task_events = [(1, created)]

    out = _workflow_events_with_candidate_context(
        _task(), task_events, all_events, state_dir=tmp_path,
    )

    assert not [e for _, e in out if e.type == "review.rejected"]


def test_global_failure_during_task_lifecycle_is_attributed(tmp_path: Path):
    """Control: the temporal guard must not silence a real, current failure that
    happens after the task's first event."""
    created = _created()
    fresh = _prd_blocked("real failure in this task's own run")
    all_events = [(0, created), (1, fresh)]  # failure during the task
    task_events = [(0, created)]

    out = _workflow_events_with_candidate_context(
        _task(), task_events, all_events, state_dir=tmp_path,
    )

    assert [e for _, e in out if e.type == "review.rejected"]
