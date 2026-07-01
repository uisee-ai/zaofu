from __future__ import annotations

import json
from pathlib import Path

from zf.core.config.project_context import ProjectContext
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.project_spine_review import (
    ARTIFACT_EVENT,
    PROPOSAL_EVENT,
    build_project_spine_review,
    create_spine_review_proposal,
    project_spine_review_insight,
    render_spine_review_markdown,
    write_spine_review_artifact,
)


def _context(tmp_path: Path) -> ProjectContext:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    EventLog(state_dir / "events.jsonl").append(ZfEvent(type="loop.started", actor="test"))
    return ProjectContext(
        project_root=tmp_path,
        config_path=tmp_path / "zf.yaml",
        config=None,
        state_dir=state_dir,
    )


def test_spine_review_detects_runtime_fault_and_reflection(tmp_path: Path) -> None:
    context = _context(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "top-agent-plan.md").write_text("# Plan\n", encoding="utf-8")
    TaskStore(context.state_dir / "kanban.json").add(
        Task(id="TASK-FAULT", title="fault", status="in_progress"),
    )
    EventLog(context.state_dir / "events.jsonl").append(ZfEvent(
        type="orchestrator.dispatch_failed",
        actor="orchestrator",
        task_id="TASK-FAULT",
        payload={"reason": "dev_dispatch_recovery_exhausted"},
    ))

    review = build_project_spine_review(context)

    assert review["verdict"] == "pause_and_repair_harness"
    assert review["runtime_spine"]["status"] == "faulted"
    assert review["reflection"]["better_solution"]
    assert review["corrective_actions"][0]["evidence_refs"][0].startswith("event:")


def test_spine_review_artifact_insight_and_proposal(tmp_path: Path) -> None:
    context = _context(tmp_path)
    TaskStore(context.state_dir / "kanban.json").add(
        Task(id="TASK-FAULT", title="fault", status="in_progress"),
    )
    EventLog(context.state_dir / "events.jsonl").append(ZfEvent(
        type="worker.stuck",
        actor="dev-1",
        task_id="TASK-FAULT",
        payload={"worker": "dev-1"},
    ))
    review = build_project_spine_review(context)

    artifact = write_spine_review_artifact(context, review)
    proposal = create_spine_review_proposal(
        context,
        review_id=review["review_id"],
        action="1",
    )
    events = EventLog(context.state_dir / "events.jsonl").read_all()
    insight = project_spine_review_insight(
        context.state_dir,
        project_id=review["project_id"],
    )

    assert (Path(artifact["artifact_dir"]) / "report.md").exists()
    assert (Path(artifact["artifact_dir"]) / "reflection.json").exists()
    assert proposal["proposal"]["schema_version"] == "spine-review.proposal.v1"
    assert any(event.type == ARTIFACT_EVENT for event in events)
    assert any(event.type == PROPOSAL_EVENT for event in events)
    assert insight["schema_version"] == "spine-review-insight.v1"
    assert insight["status"] == "ready"
    assert insight["verdict"] == review["verdict"]


def test_spine_review_split_replan_proposal_emits_unified_replan_event(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "plan.md").write_text("# Plan\n", encoding="utf-8")
    TaskStore(context.state_dir / "kanban.json").add(Task(
        id="TASK-BACKLOG",
        title="candidate task",
        status="backlog",
        contract=TaskContract(
            spec_ref="docs/plan.md",
            plan_ref="docs/plan.md",
            tdd_ref="docs/plan.md",
        ),
    ))
    review = build_project_spine_review(context)
    write_spine_review_artifact(context, review)

    create_spine_review_proposal(
        context,
        review_id=review["review_id"],
        action="1",
    )

    events = EventLog(context.state_dir / "events.jsonl").read_all()
    replan_events = [event for event in events if event.type == "replan.proposal.created"]
    assert review["verdict"] == "split_or_replan"
    assert len(replan_events) == 1
    assert replan_events[0].payload["schema_version"] == "replan-proposal.v1"
    assert replan_events[0].payload["requires_candidate_task_map"] is True


def test_spine_review_reads_supervisor_snapshot_context(tmp_path: Path) -> None:
    context = _context(tmp_path)
    supervisor_dir = context.state_dir / "projections" / "supervisor"
    supervisor_dir.mkdir(parents=True)
    (supervisor_dir / "snapshot.json").write_text(
        json.dumps({
            "schema_version": "supervisor.snapshot.v0",
            "generated_at": "2026-05-27T00:00:00+00:00",
            "attention_summary": {
                "open": 2,
                "by_source": {"plan_integrity": 2},
                "by_severity": {"warn": 2},
            },
            "plan_integrity": {
                "summary": {
                    "active_tasks": 1,
                    "findings": 2,
                    "missing_plan_refs": 1,
                },
            },
            "pause_lifecycle": {"status": "running"},
        }),
        encoding="utf-8",
    )

    review = build_project_spine_review(context)

    supervisor = review["runtime_spine"]["supervisor_snapshot"]
    assert review["runtime_spine"]["status"] == "needs_attention"
    assert supervisor["status"] == "ready"
    assert supervisor["attention_summary"]["open"] == 2
    assert supervisor["plan_integrity_summary"]["missing_plan_refs"] == 1
    assert supervisor["snapshot_ref"]["path"] == "projections/supervisor/snapshot.json"


def test_spine_review_references_previous_same_drift_reflection(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    TaskStore(context.state_dir / "kanban.json").add(
        Task(id="TASK-FAULT", title="fault", status="in_progress"),
    )
    EventLog(context.state_dir / "events.jsonl").append(ZfEvent(
        type="worker.stuck",
        actor="dev-1",
        task_id="TASK-FAULT",
        payload={"worker": "dev-1"},
    ))
    first = build_project_spine_review(context)
    write_spine_review_artifact(context, first)

    second = build_project_spine_review(context)
    previous = second["reflection"]["previous_reflections"]
    markdown = render_spine_review_markdown(second)

    assert previous
    assert previous[0]["review_id"] == first["review_id"]
    assert previous[0]["relationship"] == "same_drift_taxonomy"
    assert previous[0]["reflection_ref"]["path"].endswith("reflection.json")
    assert second["reflection"]["history_judgment"] == "reuse_previous_judgment"
    assert first["review_id"] in markdown
    assert "previous_reflections" in markdown
