"""ZF-E2E-PRDCTL-P1-4: filing guards for workflow resume checkpoints.

凡 stall 类信号消费者必先核完成证据。Incident shapes:
- deepwater 2026-07-11 13:05: false silent_stall arrived *after* three tasks
  completed; order-scoped guards missed the pre-existing closeout evidence
  and RM re-reworked healthy tasks.
- csvstats 2026-07-12: gate checkpoints filed while the stage fanout was
  still aggregating (sibling in flight) and re-filed after the gate proved
  unroutable — 4 paid no-op resume cycles.
"""

from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    WorkflowConfig,
    WorkflowDagConfig,
    ZfConfig,
)
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.workflow_resume import build_workflow_resume_checkpoints


def _config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="filing-guards-test"),
        session=SessionConfig(tmux_session="filing-guards-test"),
        roles=[
            RoleConfig(
                name="dev-lane-0",
                backend="mock",
                publishes=["dev.build.done", "dev.failed"],
            ),
            RoleConfig(
                name="verify-lane-0",
                backend="mock",
                triggers=["static_gate.passed"],
                publishes=["verify.passed", "verify.failed"],
            ),
        ],
        workflow=WorkflowConfig(
            dag=WorkflowDagConfig(
                enabled=True,
                graph_review_test_judge_reconcile=True,
            ),
            rework_routing={"verify.failed": "dev-lane-0"},
        ),
    )


def _task(task_id: str) -> Task:
    return Task(id=task_id, title=task_id, status="in_progress", assigned_to="dev-lane-0")


def _actionable(checkpoints, task_id: str):
    return [
        c for c in checkpoints
        if c.task_id == task_id and c.safe_resume_action != "no_action"
    ]


def test_gate_checkpoint_files_for_true_stall(tmp_path: Path):
    events = [
        ZfEvent(id="e1", type="dev.build.done", actor="dev-lane-0",
                task_id="T-1", payload={"task_id": "T-1"}),
    ]
    checkpoints = build_workflow_resume_checkpoints(
        tmp_path, _config(), events=events, tasks=[_task("T-1")],
    )
    assert _actionable(checkpoints, "T-1")


def test_closeout_evidence_suppresses_filing(tmp_path: Path):
    # Completion evidence exists (verify.passed terminal success) with no
    # later failure — a stall signal after it must not re-file work.
    events = [
        ZfEvent(id="e1", type="dev.build.done", actor="dev-lane-0",
                task_id="T-1", payload={"task_id": "T-1"}),
        ZfEvent(id="e2", type="verify.passed", actor="verify-lane-0",
                task_id="T-1", payload={"task_id": "T-1"}),
        ZfEvent(id="e3", type="dev.build.done", actor="dev-lane-0",
                task_id="T-1", payload={"task_id": "T-1"}),
    ]
    checkpoints = build_workflow_resume_checkpoints(
        tmp_path, _config(), events=events, tasks=[_task("T-1")],
    )
    assert not _actionable(checkpoints, "T-1")
    suppressed = [
        c for c in checkpoints
        if c.task_id == "T-1" and "closeout evidence" in c.reason
    ]
    assert suppressed


def test_failure_after_closeout_still_files(tmp_path: Path):
    # A genuine failure after the closeout re-opens the task for rework.
    events = [
        ZfEvent(id="e1", type="dev.build.done", actor="dev-lane-0",
                task_id="T-1", payload={"task_id": "T-1"}),
        ZfEvent(id="e2", type="verify.passed", actor="verify-lane-0",
                task_id="T-1", payload={"task_id": "T-1"}),
        ZfEvent(id="e3", type="verify.failed", actor="verify-lane-0",
                task_id="T-1", payload={"task_id": "T-1", "reason": "regression"}),
        ZfEvent(id="e4", type="dev.build.done", actor="dev-lane-0",
                task_id="T-1", payload={"task_id": "T-1"}),
    ]
    checkpoints = build_workflow_resume_checkpoints(
        tmp_path, _config(), events=events, tasks=[_task("T-1")],
    )
    assert _actionable(checkpoints, "T-1")


def test_sibling_inflight_fanout_suppresses_filing(tmp_path: Path):
    events = [
        ZfEvent(id="e0", type="fanout.started", actor="zf-cli",
                payload={"fanout_id": "f-impl", "stage_id": "impl"}),
        ZfEvent(id="e1", type="dev.build.done", actor="dev-lane-0",
                task_id="T-1",
                payload={"task_id": "T-1", "fanout_id": "f-impl"}),
    ]
    checkpoints = build_workflow_resume_checkpoints(
        tmp_path, _config(), events=events, tasks=[_task("T-1")],
    )
    assert not _actionable(checkpoints, "T-1")
    assert any(
        "waiting_for_sibling" in c.reason
        for c in checkpoints if c.task_id == "T-1"
    )


def test_aggregate_completion_lifts_sibling_suppression(tmp_path: Path):
    events = [
        ZfEvent(id="e0", type="fanout.started", actor="zf-cli",
                payload={"fanout_id": "f-impl", "stage_id": "impl"}),
        ZfEvent(id="e1", type="dev.build.done", actor="dev-lane-0",
                task_id="T-1",
                payload={"task_id": "T-1", "fanout_id": "f-impl"}),
        ZfEvent(id="e2", type="fanout.aggregate.completed", actor="zf-cli",
                payload={"fanout_id": "f-impl", "status": "completed"}),
    ]
    checkpoints = build_workflow_resume_checkpoints(
        tmp_path, _config(), events=events, tasks=[_task("T-1")],
    )
    assert _actionable(checkpoints, "T-1")


def test_gate_unroutable_history_suppresses_refiling(tmp_path: Path):
    events = [
        ZfEvent(id="e1", type="dev.build.done", actor="dev-lane-0",
                task_id="T-1", payload={"task_id": "T-1"}),
    ]
    first = build_workflow_resume_checkpoints(
        tmp_path, _config(), events=events, tasks=[_task("T-1")],
    )
    pending = _actionable(first, "T-1")
    assert pending
    events.append(ZfEvent(
        id="e2", type="workflow.resume.gate_unroutable", actor="zf-cli",
        task_id="T-1",
        payload={
            "task_id": "T-1",
            "checkpoint_idempotency_key": pending[0].idempotency_key,
            "reason": "no gate dispatcher available in this context",
        },
    ))
    second = build_workflow_resume_checkpoints(
        tmp_path, _config(), events=events, tasks=[_task("T-1")],
    )
    assert not _actionable(second, "T-1")
    assert any(
        "gate-unroutable" in c.reason
        for c in second if c.task_id == "T-1"
    )
