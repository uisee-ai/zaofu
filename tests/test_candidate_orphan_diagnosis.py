"""An existing orphan alert permits one bound semantic review on its own anchor."""

from dataclasses import replace

from tests.test_candidate_aggregate_review import _mixed
from zf.core.events import ZfEvent
from zf.runtime.candidate_aggregate_review import pending_candidate_reviews
from zf.runtime.task_orphan_recovery import (
    anchor_awaits_candidate_review,
    orphan_allows_candidate_review,
)
from zf.runtime.quiescent_reader_recovery import reconcile_quiescent_reader_handoff
from zf.runtime.run_continuation import human_decisions_block_action
from zf.runtime.run_manager import run_manager_tick


def test_orphan_on_bound_candidate_permits_one_review_and_preserves_other_gates(
    tmp_path,
):
    runtime, failure, _ = _mixed(tmp_path, blocked=True)
    state, cfg, log, writer = (
        runtime.state_dir,
        runtime.config,
        runtime.event_log,
        runtime.event_writer,
    )
    (review,) = pending_candidate_reviews(
        state_dir=state, config=cfg, events=log.read_all()
    )
    run, task = review["workflow_run_id"], review["task_id"]
    assert anchor_awaits_candidate_review(state, cfg, log.read_all(), task)
    assert not anchor_awaits_candidate_review(state, cfg, log.read_all(), "other")
    orphan = writer.append(
        ZfEvent(
            type="task.orphaned",
            task_id=task,
            actor="zf-cli",
            origin="kernel",
            correlation_id=run,
        )
    )
    gate = writer.append(
        ZfEvent(
            type="human.escalate",
            task_id=task,
            actor="orchestrator",
            payload={"failure_class": "orphaned_task", "reason": "orphaned"},
        )
    )
    es = log.read_all()
    decision = {"event_id": gate.id, "blocking_scope": "run", "task_id": task}
    assert not human_decisions_block_action([decision], review, events=es)
    assert human_decisions_block_action(
        [decision], {**review, "action": "stage-replan-new-generation"}, events=es
    )
    assert human_decisions_block_action(
        [decision], {**review, "task_id": "other"}, events=es
    )
    assert human_decisions_block_action(
        [decision], review, events=[e for e in es if e.id != orphan.id]
    )
    assert human_decisions_block_action(
        [decision, {"event_id": "owner-stop", "blocking_scope": "run"}],
        review,
        events=es,
    )
    assert not orphan_allows_candidate_review(
        decision,
        review,
        [replace(e, actor="owner") if e.id == gate.id else e for e in es],
    )
    assert reconcile_quiescent_reader_handoff(
        state_dir=state,
        config=cfg,
        writer=writer,
        project_root=runtime.project_root,
        events=es,
        escalation_id=gate.id,
    )
    for _ in range(2):
        run_manager_tick(
            state_dir=state,
            writer=writer,
            config=cfg,
            project_root=runtime.project_root,
            event_log=log,
            spawn_repairs=False,
        )
    requests = [
        e
        for e in log.read_all()
        if e.type == "orchestrator.semantic.failure.requested"
        and failure.id in e.payload.get("failure_event_ids", [])
    ]
    assert len(requests) == 1
    assert not any(e.type == "runtime.action.attempt.started" for e in log.read_all())
