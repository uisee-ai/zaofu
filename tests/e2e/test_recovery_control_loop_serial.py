from __future__ import annotations

import json
from pathlib import Path

from zf.core.config.schema import (
    FanoutAggregateConfig,
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    WorkflowStrictTriggersConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.run_manager import run_manager_tick
from zf.runtime.run_manager_rework_triage import TRIAGE_RECORDED, TRIAGE_REQUESTED
from zf.runtime.supervisor_inspection import run_supervisor_inspection
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


TRACE_ID = "serial-recovery-trace"
FEATURE_ID = "ISSUE-SERIAL-RECOVERY"
OLD_TASK_ID = "TASK-OLD"
NEW_TASK_ID = "TASK-CORE"


def _config(*, with_orchestrator: bool = True) -> ZfConfig:
    roles = [
        RoleConfig(name="dev", backend="mock", max_rework_attempts=3),
        RoleConfig(name="review", backend="mock", role_kind="reader"),
        RoleConfig(
            name="flow-discovery",
            backend="mock",
            role_kind="reader",
            skills=["zf-gap-task-synth"],
        ),
        RoleConfig(name="verify", backend="mock", role_kind="reader"),
    ]
    if with_orchestrator:
        roles.append(RoleConfig(name="orchestrator", backend="mock"))
    return ZfConfig(
        project=ProjectConfig(name="serial-recovery-e2e"),
        session=SessionConfig(tmux_session="zf-serial-recovery-e2e"),
        workflow=WorkflowConfig(
            strict_triggers=WorkflowStrictTriggersConfig(rework_attempts_gte=3),
            stages=[
                WorkflowStageConfig(
                    id="issue-post-verify-discovery",
                    trigger="flow.discovery.requested",
                    topology="fanout_reader",
                    roles=["flow-discovery"],
                ),
                WorkflowStageConfig(
                    id="issue-gap-impl",
                    trigger="task_map.ready",
                    topology="fanout_writer_scoped",
                    roles=["dev"],
                    task_map="${task_map_ref}",
                    synthesize_canonical_tasks=True,
                    aggregate=FanoutAggregateConfig(
                        mode="candidate_integration",
                        success_event="candidate.ready",
                        failure_event="integration.failed",
                    ),
                ),
                WorkflowStageConfig(
                    id="issue-gap-verify",
                    trigger="candidate.ready",
                    topology="fanout_reader",
                    roles=["verify"],
                    aggregate=FanoutAggregateConfig(
                        mode="wait_for_all",
                        child_success_event="verify.child.completed",
                        child_failure_event="verify.child.failed",
                        success_event="verify.passed",
                        failure_event="verify.failed",
                    ),
                ),
            ],
        ),
        roles=roles,
    )


def _state(tmp_path: Path) -> tuple[Path, EventLog, EventWriter, TaskStore]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir(parents=True)
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    return state_dir, log, EventWriter(log), TaskStore(state_dir / "kanban.json")


def _seed_task_map(state_dir: Path, log: EventLog) -> Path:
    task_map = state_dir / "artifacts" / FEATURE_ID / "task_map.json"
    task_map.parent.mkdir(parents=True)
    task_map.write_text(
        json.dumps(
            {
                "schema_version": "task-map.v1",
                "feature_id": FEATURE_ID,
                "tasks": [
                    {
                        "task_id": OLD_TASK_ID,
                        "title": "repair expiry behavior",
                        "owner_role": "dev",
                        "wave": 0,
                        "allowed_paths": ["src/**", "tests/**"],
                        "allowed_paths_reason": "initial issue slice",
                        "acceptance": ["expiry behavior is preserved"],
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    log.append(
        ZfEvent(
            id="initial-task-map",
            type="task_map.ready",
            actor="zf-cli",
            correlation_id=TRACE_ID,
            payload={
                "pdd_id": FEATURE_ID,
                "feature_id": FEATURE_ID,
                "task_map_ref": str(task_map),
            },
        )
    )
    return task_map


def _failure(round_number: int) -> ZfEvent:
    return ZfEvent(
        id=f"failure-{round_number}",
        type="review.rejected",
        actor="review",
        task_id=OLD_TASK_ID,
        correlation_id=TRACE_ID,
        payload={
            "fanout_id": f"review-fanout-{round_number}",
            "failure_fingerprint": "missing-expiry-contract",
            "reason": "expiry acceptance remains untestable",
            "findings": [f"round {round_number}: missing bounded expiry test"],
        },
    )


def _append_and_run(
    log: EventLog,
    orchestrator: Orchestrator,
    event: ZfEvent,
) -> None:
    log.append(event)
    orchestrator.run_once(events=[event])


def _events_of(log: EventLog, event_type: str) -> list[ZfEvent]:
    return [event for event in log.read_all() if event.type == event_type]


def _first_index(events: list[ZfEvent], event_type: str) -> int:
    return next(index for index, event in enumerate(events) if event.type == event_type)


def test_serial_recovery_chain_replans_restarts_and_verifies(tmp_path: Path) -> None:
    state_dir, log, writer, store = _state(tmp_path)
    store.add(
        Task(
            id=OLD_TASK_ID,
            title="repair expiry behavior",
            status="review",
            assigned_to="dev",
            contract=TaskContract(
                feature_id=FEATURE_ID,
                behavior="expiry behavior is preserved",
                verification="uv run pytest tests/test_core.py",
                plan_ref="docs/plans/serial-recovery.md",
            ),
        )
    )
    task_map = _seed_task_map(state_dir, log)
    config = _config()
    transport = TmuxTransport(TmuxSession(session_name="serial-recovery", dry_run=True))
    orchestrator = Orchestrator(state_dir, config, transport)

    for round_number in (1, 2, 3):
        store.update(OLD_TASK_ID, status="review")
        orchestrator._set_worker_state("dev", "idle", reason="serial e2e next round")
        _append_and_run(log, orchestrator, _failure(round_number))
        assert store.get(OLD_TASK_ID).retry_count == round_number
        if round_number < 3:
            assert len(_events_of(log, "task.rework.requested")) == round_number
            assert not _events_of(log, "task.rework.capped")
            assert not _events_of(log, TRIAGE_REQUESTED)

    capped = _events_of(log, "task.rework.capped")
    assert len(capped) == 1
    assert capped[0].payload["failure_count"] == 3
    assert capped[0].payload["semantic_triage_required"] is True
    assert len(_events_of(log, "task.rework.requested")) == 2
    assert not _events_of(log, "human.escalate")

    supervisor = run_supervisor_inspection(
        state_dir,
        config=config,
        project_root=tmp_path,
        project_id="serial-recovery-e2e",
        emit_attention_events=True,
    )
    assert supervisor["snapshot"]["schema_version"]
    attention = _events_of(log, "runtime.attention.needed")
    assert len(attention) == 1
    assert attention[0].task_id == OLD_TASK_ID
    assert attention[0].payload["suggested_route"] == "run_manager_recovery"
    assert _events_of(log, "supervisor.decision.recorded")
    assert not _events_of(log, "owner.visible_message.requested")
    assert not _events_of(log, "autoresearch.invocation.requested")

    first_tick = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=config,
        event_log=log,
        spawn_repairs=False,
    )
    assert first_tick.actions_applied == 1
    triage_request = _events_of(log, TRIAGE_REQUESTED)
    assert len(triage_request) == 1
    assert not _events_of(log, "run.manager.autoresearch.requested")
    request_id = triage_request[0].payload["request_id"]

    advice = ZfEvent(
        id="orchestrator-advice",
        type=TRIAGE_RECORDED,
        actor="orchestrator",
        task_id=OLD_TASK_ID,
        causation_id=triage_request[0].id,
        correlation_id=request_id,
        payload={
            "request_id": request_id,
            "failure_fingerprint": "missing-expiry-contract",
            "recommended_action": "split_task",
            "guidance": "replace the mixed task with one bounded core slice",
            "apply_policy": "proposal_only",
        },
    )
    log.append(advice)
    second_tick = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=config,
        event_log=log,
        spawn_repairs=False,
    )
    assert second_tick.actions_applied == 1
    discovery_requests = _events_of(log, "flow.discovery.requested")
    assert len(discovery_requests) == 1
    discovery_request = discovery_requests[0]
    assert discovery_request.payload["task_map_ref"] == str(task_map)
    assert discovery_request.payload["supersedes_task_ids"] == [OLD_TASK_ID]
    context_ref = discovery_request.payload["recovery_context_ref"]["ref"]
    assert (state_dir / context_ref).exists()

    # Simulate process restart at the semantic request checkpoint.
    restarted_log = EventLog(state_dir / "events.jsonl")
    restarted_writer = EventWriter(restarted_log)
    restarted_transport = TmuxTransport(
        TmuxSession(session_name="serial-recovery-restarted", dry_run=True)
    )
    restarted_orchestrator = Orchestrator(state_dir, config, restarted_transport)
    restarted_orchestrator.run_once(events=[discovery_request])

    discovery_completed = ZfEvent(
        id="flow-discovery-completed",
        type="flow.discovery.completed",
        actor="flow-discovery",
        causation_id=discovery_request.id,
        correlation_id=request_id,
        payload={
            **discovery_request.payload,
            "goal_kind": "issue",
            "gap_category": "issue_gap",
            "supersedes_task_ids": [OLD_TASK_ID],
            "gap_tasks": [
                {
                    "task_id": NEW_TASK_ID,
                    "parent_task_id": OLD_TASK_ID,
                    "owner_role": "dev",
                    "claim_paths": ["src/core/**", "tests/test_core.py"],
                    "acceptance": ["expiry core behavior works"],
                    "verify_commands": ["uv run pytest tests/test_core.py"],
                    "source_refs": ["docs/issues/serial-recovery.md"],
                }
            ],
        },
    )
    _append_and_run(restarted_log, restarted_orchestrator, discovery_completed)

    restarted_events = restarted_log.read_all()
    assert len(_events_of(restarted_log, "flow.discovery.requested")) == 1
    assert len(_events_of(restarted_log, "task_map.amended")) == 1
    assert len(_events_of(restarted_log, "task.superseded")) == 1
    restarted_store = TaskStore(state_dir / "kanban.json")
    assert restarted_store.get(OLD_TASK_ID).status == "cancelled"
    assert restarted_store.get(NEW_TASK_ID).status == "in_progress"
    impl_dispatch = [
        event
        for event in restarted_events
        if event.type == "fanout.child.dispatched"
        and event.payload.get("stage_id") == "issue-gap-impl"
    ]
    assert len(impl_dispatch) == 1

    impl_done = ZfEvent(
        id="task-core-done",
        type="task.done.accepted",
        actor="zf-cli",
        task_id=NEW_TASK_ID,
        causation_id=impl_dispatch[0].id,
        correlation_id=request_id,
        payload={
            "task_id": NEW_TASK_ID,
            "status": "done",
            "evidence_refs": ["artifacts/serial-recovery/impl.json"],
        },
    )
    restarted_store.update(NEW_TASK_ID, status="done")
    restarted_log.append(impl_done)
    candidate = ZfEvent(
        id="candidate-ready",
        type="candidate.ready",
        actor="dev",
        task_id=NEW_TASK_ID,
        causation_id=impl_done.id,
        correlation_id=request_id,
        payload={
            "pdd_id": FEATURE_ID,
            "feature_id": FEATURE_ID,
            "candidate_ref": f"candidate/{FEATURE_ID}",
            "completed_task_ids": [NEW_TASK_ID],
        },
    )
    _append_and_run(restarted_log, restarted_orchestrator, candidate)
    verify_dispatch = [
        event
        for event in restarted_log.read_all()
        if event.type == "fanout.child.dispatched"
        and event.payload.get("stage_id") == "issue-gap-verify"
    ][-1]
    verify_completed = ZfEvent(
        id="verify-child-completed",
        type="verify.child.completed",
        actor="verify",
        causation_id=verify_dispatch.id,
        correlation_id=request_id,
        payload={
            "fanout_id": verify_dispatch.payload["fanout_id"],
            "child_id": verify_dispatch.payload["child_id"],
            "run_id": verify_dispatch.payload["run_id"],
            "role_instance": "verify",
            "status": "completed",
            "report": {
                "status": "passed",
                "summary": "replacement task verified after restart",
                "findings": [],
                "recommendation": "approve",
                "evidence_refs": ["artifacts/serial-recovery/verify.json"],
            },
        },
    )
    _append_and_run(restarted_log, restarted_orchestrator, verify_completed)

    final_tick = run_manager_tick(
        state_dir=state_dir,
        writer=restarted_writer,
        config=config,
        event_log=restarted_log,
        spawn_repairs=False,
    )
    assert final_tick.actions_applied == 0
    final_events = restarted_log.read_all()
    assert len([event for event in final_events if event.type == "verify.passed"]) == 1
    assert _events_of(restarted_log, "run.manager.action.verify.passed")
    assert not _events_of(restarted_log, "run.manager.autoresearch.requested")
    assert not _events_of(restarted_log, "human.escalate")

    ordered_types = [
        "review.rejected",
        "task.rework.requested",
        "task.rework.capped",
        "supervisor.decision.recorded",
        TRIAGE_REQUESTED,
        TRIAGE_RECORDED,
        "flow.discovery.requested",
        "flow.discovery.completed",
        "task_map.amended",
        "task.superseded",
        "task.done.accepted",
        "candidate.ready",
        "verify.passed",
    ]
    positions = [_first_index(final_events, event_type) for event_type in ordered_types]
    assert positions == sorted(positions)


def test_serial_advisor_timeout_returns_to_run_manager_diagnosis(tmp_path: Path) -> None:
    state_dir, log, writer, store = _state(tmp_path)
    store.add(Task(id="TASK-TIMEOUT", title="timeout task", status="review"))
    log.append(
        ZfEvent(
            id="cap-timeout",
            type="task.rework.capped",
            actor="zf-cli",
            task_id="TASK-TIMEOUT",
            payload={
                "failure_fingerprint": "advisor-timeout",
                "failure_count": 3,
                "failure_event_ids": ["f1", "f2", "f3"],
            },
        )
    )

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(with_orchestrator=False),
        event_log=log,
        spawn_repairs=False,
    )

    assert result.actions_applied == 0
    assert result.autoresearch_requested == 1
    assert len(_events_of(log, "run.manager.autoresearch.requested")) == 1
    assert not _events_of(log, TRIAGE_REQUESTED)
    assert not _events_of(log, "human.escalate")
