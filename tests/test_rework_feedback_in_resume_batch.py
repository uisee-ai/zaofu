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
from zf.runtime.workflow_resume_apply import _apply_batch_task_map_ready


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
