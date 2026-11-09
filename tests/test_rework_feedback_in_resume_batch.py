"""avbs-r4 F2: resume batch 必须携带 reviewer findings(否则盲 rework)。

r4 实证:workflow_resume_batch 的 task_map.ready 无 rework_feedback 键,
工人 briefing 拿不到 findings,二审 7 条逐字复现、rework diff 为空。
下游管线(orchestrator_fanout rework_feedback → child briefing)本已存在,
断点在 checkpoint 捕获与 batch payload 注入。
"""

from __future__ import annotations

import json
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.workflow_resume import (
    WorkflowBatchResumeCheckpoint,
    _batch_checkpoint_from_event,
    _escalated_batch_checkpoint,
)
from zf.runtime.workflow_resume_apply import (
    _apply_batch_task_map_ready,
    _writer_resume_feedback,
)


_FINDINGS = [
    {
        "severity": "high",
        "task_id": "AVBS-FLOW-001",
        "path": "src/simulation/workflow/WorkflowEngine.ts",
        "message": "Terminal failure does not release claimed resources.",
    },
    {
        "severity": "high",
        "child_id": "review-scene-scene",
        "message": "e2e evidence mounts an inline canvas harness.",
    },
]


def _rejection_event() -> ZfEvent:
    return ZfEvent(
        type="review.rejected",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "pdd_id": "AVBS-PRD-REBUILD-R4",
            "fanout_id": "fanout-avbs-review-evt-x",
            "stage_id": "avbs-review",
            "status": "failed",
            "task_map_ref": "docs/plans/avbs-task-map.json",
            "source_commit": "abc123",
            "candidate_base_commit": "abc123",
            "failed_children": ["review-flow-flow", "review-scene-scene"],
            "findings": _FINDINGS,
        },
    )


def test_batch_checkpoint_captures_findings_as_feedback() -> None:
    checkpoint = _batch_checkpoint_from_event(
        _rejection_event(),
        source_event_type="review.rejected",
        safe_resume_action="repair_failed_children",
        evidence_event_ids=[],
    )
    assert checkpoint.rework_feedback
    assert any("release" in line for line in checkpoint.rework_feedback)
    assert any("AVBS-FLOW-001" in line for line in checkpoint.rework_feedback)


def test_escalated_checkpoint_preserves_feedback() -> None:
    base = _batch_checkpoint_from_event(
        _rejection_event(),
        source_event_type="review.rejected",
        safe_resume_action="repair_failed_children",
        evidence_event_ids=[],
    )
    escalated = _escalated_batch_checkpoint(
        ZfEvent(type="human.escalate", payload={"reason": "cap"}),
        base,
    )
    assert escalated.rework_feedback == base.rework_feedback


def test_apply_batch_task_map_ready_injects_rework_feedback(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    task_map = tmp_path / "task_map.json"
    task_map.write_text(json.dumps({"tasks": []}), encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    checkpoint = WorkflowBatchResumeCheckpoint(
        checkpoint_id="cp-1",
        source_event_id="evt-src",
        source_event_type="review.rejected",
        blocking_event_id="evt-src",
        safe_resume_action="repair_failed_children",
        pdd_id="AVBS-PRD-REBUILD-R4",
        task_map_ref=str(task_map),
        source_commit="abc123",
        candidate_base_commit="abc123",
        failed_children=["review-flow-flow"],
        rework_feedback=["AVBS-FLOW-001: fix resource release"],
    )
    result = _apply_batch_task_map_ready(
        writer,
        checkpoint,
        [],
        reason="test",
        task_ids=["AVBS-FLOW-001"],
        resume_scope="failed_children_only",
        state_dir=state_dir,
    )
    assert result.applied, result.detail
    ready = [e for e in log.read_all() if e.type == "task_map.ready"]
    assert len(ready) == 1
    assert ready[0].payload["rework_feedback"] == [
        "AVBS-FLOW-001: fix resource release",
    ]


def test_current_plan_package_filters_stale_planner_feedback() -> None:
    feedback = [
        "TASK-WP14-B / mandatory_goal_claim_contract_rewritten: stale",
        "TASK-WP14-C / runtime_failure: upstream dependency failed: TASK-WP14-B",
        "TASK-WP14-B / implementation: fix frame governance",
    ]
    assert _writer_resume_feedback(
        {"plan_artifact_package_ref": "artifacts/plan-packages/current.json"},
        feedback,
    ) == ["TASK-WP14-B / implementation: fix frame governance"]


def test_apply_batch_task_map_ready_preserves_current_package_identity(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    task_map = tmp_path / "task_map.json"
    task_map.write_text(json.dumps({"tasks": []}), encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    writer.append(ZfEvent(
        id="evt-task-map",
        type="task_map.ready",
        payload={
            "workflow_run_id": "Run-UPPER",
            "prd_ref": "docs/prd/current.md",
            "task_map_ref": str(task_map),
        },
    ))
    writer.append(ZfEvent(
        type="fanout.started",
        payload={
            "fanout_id": "fanout-1",
            "trigger_event_id": "evt-task-map",
            "expected_children": [{
                "child_id": "child-1",
                "workflow_run_id": "Run-UPPER",
                "payload": {"flow_kind": "issue"},
                "task_map_generation": "generation-1",
                "plan_artifact_package_id": "planpkg-1",
                "plan_artifact_package_ref": "plan-packages/current.json",
                "plan_artifact_package_digest": "package-digest-1",
            }],
        },
    ))
    checkpoint = WorkflowBatchResumeCheckpoint(
        checkpoint_id="cp-package",
        source_event_id="evt-failed",
        source_event_type="integration.failed",
        blocking_event_id="evt-failed",
        safe_resume_action="repair_failed_children",
        pdd_id="PDD-1",
        fanout_id="fanout-1",
        task_map_ref=str(task_map),
        source_commit="abc123",
        candidate_base_commit="abc123",
        failed_children=["child-1"],
    )

    result = _apply_batch_task_map_ready(
        writer,
        checkpoint,
        [],
        reason="retry current package",
        task_ids=["TASK-1"],
        resume_scope="failed_children_only",
        state_dir=state_dir,
    )

    assert result.applied
    ready = [event for event in log.read_all() if event.type == "task_map.ready"]
    resumed = ready[-1].payload
    assert resumed["workflow_run_id"] == "Run-UPPER"
    assert resumed["flow_kind"] == "issue"
    assert resumed["prd_ref"] == "docs/prd/current.md"
    assert resumed["task_map_generation"] == "generation-1"
    assert resumed["plan_artifact_package_id"] == "planpkg-1"
    assert resumed["plan_artifact_package_digest"] == "package-digest-1"
