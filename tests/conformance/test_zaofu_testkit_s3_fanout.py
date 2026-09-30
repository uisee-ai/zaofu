"""Additional S3 fanout/fanin conformance case."""

from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator import Orchestrator
from tests.testkit.scenarios import isolated_project

from tests.test_fanout_hardening import (
    _RecordingTransport,
    _fanout_id,
    _synth_budget_config,
    _start,
)
from tests.testkit.runtime_case_evidence import attach_runtime_case_evidence


def test_fanin_waits_for_all_children_and_deduplicates_completion(
    tmp_path: Path, request,
) -> None:
    project = isolated_project(tmp_path, run_id="RUN-S3-FANOUT", task_id="TASK-S3")
    state_dir = project.state_dir
    attach_runtime_case_evidence(
        request, state_dir,
        given="fanout has two children and only one completion is initially present",
        when="deliver one child twice, then deliver the second child twice",
        then="fanin dispatches synth exactly once only after all children complete",
        forbidden=["early synth dispatch", "duplicate synth dispatch", "aggregate completion"],
        task_id=project.task_id,
        inputs={"children": ["review-a", "review-b"]},
    )
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    orchestrator = Orchestrator(
        state_dir,
        _synth_budget_config(timeout_seconds=60),
        _RecordingTransport(),  # type: ignore[arg-type]
        project_root=tmp_path,
    )
    _start(orchestrator)
    fanout_id = _fanout_id(log)

    child_b = ZfEvent(
        id="EV-S3-FANOUT-B",
        type="workflow.child.completed",
        actor="review-b",
        correlation_id="trace-s3-fanout",
        payload={
            "fanout_id": fanout_id,
            "child_id": "review-b",
            "run_id": f"run-{fanout_id}-review-b",
            "status": "completed",
        },
    )
    log.append(child_b)
    orchestrator.run_once(events=[child_b])
    orchestrator.run_once(events=[child_b])

    assert not [
        event for event in log.read_all() if event.type == "fanout.synth.dispatched"
    ]
    child_a = ZfEvent(
        id="EV-S3-FANOUT-A",
        type="workflow.child.completed",
        actor="review-a",
        correlation_id="trace-s3-fanout",
        payload={
            "fanout_id": fanout_id,
            "child_id": "review-a",
            "run_id": f"run-{fanout_id}-review-a",
            "status": "completed",
        },
    )
    log.append(child_a)
    orchestrator.run_once(events=[child_a])
    orchestrator.run_once(events=[child_a])

    events = log.read_all()
    synth_dispatches = [
        event for event in events if event.type == "fanout.synth.dispatched"
    ]
    assert len(synth_dispatches) == 1
    assert not [event for event in events if event.type == "fanout.aggregate.completed"]


def test_failed_child_synth_rejects_fanout_once_and_reaches_terminal_state(
    tmp_path: Path, request,
) -> None:
    project = isolated_project(tmp_path, run_id="RUN-S3-FAILURE", task_id="TASK-S3-FAILURE")
    state_dir = project.state_dir
    attach_runtime_case_evidence(
        request, state_dir,
        given="one fanout child completes and another child fails",
        when="reconcile child facts and deliver the reject synth result twice",
        then="one failed aggregate and one rejection are recorded",
        forbidden=["duplicate aggregate", "duplicate rejection", "successful completion"],
        task_id=project.task_id,
        inputs={"children": ["review-a", "review-b"], "failed_child": "review-b"},
    )
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    orchestrator = Orchestrator(
        state_dir,
        _synth_budget_config(timeout_seconds=60),
        transport,  # type: ignore[arg-type]
        project_root=tmp_path,
    )
    _start(orchestrator)
    fanout_id = _fanout_id(log)

    child_a = ZfEvent(
        id="EV-S3-FAIL-A",
        type="workflow.child.completed",
        actor="review-a",
        correlation_id="trace-s3-failure",
        payload={
            "fanout_id": fanout_id,
            "child_id": "review-a",
            "run_id": f"run-{fanout_id}-review-a",
            "status": "completed",
        },
    )
    child_b = ZfEvent(
        id="EV-S3-FAIL-B",
        type="workflow.child.failed",
        actor="review-b",
        correlation_id="trace-s3-failure",
        payload={
            "fanout_id": fanout_id,
            "child_id": "review-b",
            "run_id": f"run-{fanout_id}-review-b",
            "status": "failed",
            "reason": "verification failed",
        },
    )
    orchestrator.run_once(events=[child_a])
    orchestrator.run_once(events=[child_b])
    assert any(event.type == "fanout.synth.dispatched" for event in log.read_all())

    synth = ZfEvent(
        id="EV-S3-FAIL-SYNTH",
        type="fanout.synth.completed",
        actor="review-synth",
        correlation_id="trace-s3-failure",
        payload={
            "fanout_id": fanout_id,
            "recommendation": "reject",
            "summary": "one child failed",
        },
    )
    orchestrator.run_once(events=[synth])
    orchestrator.run_once(events=[synth])

    events = log.read_all()
    aggregates = [
        event for event in events
        if event.type == "fanout.aggregate.completed"
        and event.payload.get("fanout_id") == fanout_id
    ]
    rejected = [
        event for event in events
        if event.type == "review.rejected"
        and event.payload.get("fanout_id") == fanout_id
    ]
    assert len(aggregates) == 1
    assert aggregates[0].payload["status"] == "failed"
    assert len(rejected) == 1
