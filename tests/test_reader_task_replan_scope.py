"""Task recovery readers share a Run, not one retry counter or terminal."""
from types import SimpleNamespace

import pytest
from pathlib import Path

from zf.core.events import ZfEvent
from zf.runtime.stage_failure_replan import STAGE_REPLAN_CAP, plan_reader_stage_replan
from zf.runtime.reader_task_recovery_scope import ReaderTaskRecoveryScopes


def config():
    return SimpleNamespace(workflow=SimpleNamespace(stages=[SimpleNamespace(
        id="prd-post-verify-discovery", attempt_domain="task", topology="fanout_reader",
        trigger="flow.discovery.requested", failure_event="",
        aggregate=SimpleNamespace(success_event="flow.discovery.completed", failure_event="flow.discovery.failed"),
    )]))


def chain(task="A", operation="impl-A", run="run", *, admitted=True):
    source = ZfEvent(type="dev.blocked", task_id=task, payload={
        "workflow_run_id": run, "operation_id": operation, "task_pipeline_stage": "impl",
    })
    admission = ZfEvent(type="workflow.call.result.admitted", payload={
        "workflow_run_id": run, "operation_id": operation, "source_event_id": source.id,
        "admission_status": "admitted", "semantic_verdict": "blocked",
    })
    trigger = ZfEvent(type="flow.discovery.requested", task_id=task, payload={
        "workflow_run_id": run, "task_id": task, "pdd_id": "PDD", "rework_of": source.id,
        "target_ref": "partial-" + task,
    })
    failure = ZfEvent(type="flow.discovery.failed", payload={
        "workflow_run_id": run, "pdd_id": "PDD", "rework_of": source.id,
        "affected_task_ids": [task], "reason": "bounded task issue",
    })
    return [source, *([admission] if admitted else []), trigger, failure]


@pytest.mark.parametrize("task,operation", [("B", "impl-B"), ("A", "impl-A-next")])
def test_other_task_or_failed_operation_does_not_consume_cap(task, operation):
    other = chain(task, operation)
    historical = other + [ZfEvent(type="flow.discovery.failed", payload=dict(other[-1].payload))
                          for _ in range(STAGE_REPLAN_CAP)]
    current = chain()
    retry, reason = plan_reader_stage_replan(config(), historical + current, current[-1])
    assert retry is not None, reason
    assert retry.payload["rework_attempt"] == 1
    assert retry.payload["task_id"] == "A"


def test_same_causal_chain_keeps_cap_after_new_reader_attempts():
    events = chain()
    for _ in range(STAGE_REPLAN_CAP):
        prior = events[-1]
        retry, _ = plan_reader_stage_replan(config(), events, prior)
        assert retry is not None
        events += [retry, ZfEvent(type="flow.discovery.failed", payload={
            "workflow_run_id": "run", "pdd_id": "PDD", "rework_of": prior.id,
        })]
    retry, reason = plan_reader_stage_replan(config(), events, events[-1])
    assert retry is None and reason == "cap_exhausted"


def test_other_task_success_cannot_reset_failed_chain():
    current = chain()
    events = current + [ZfEvent(type="flow.discovery.failed", payload=dict(current[-1].payload))]
    other = chain("B", "impl-B")
    events += other[:-1] + [ZfEvent(type="flow.discovery.completed", payload=dict(other[-1].payload))]
    failure = ZfEvent(type="flow.discovery.failed", payload=dict(current[-1].payload))
    retry, reason = plan_reader_stage_replan(config(), events + [failure], failure)
    assert retry is None and reason == "cap_exhausted"


def test_affected_task_claim_alone_does_not_create_local_authority():
    events = chain(admitted=False)
    events += [ZfEvent(type="flow.discovery.failed", payload={**events[-1].payload,
               "affected_task_ids": ["invented-" + str(n)]}) for n in range(STAGE_REPLAN_CAP)]
    retry, reason = plan_reader_stage_replan(config(), events, events[-1])
    assert retry is None and reason == "cap_exhausted"


@pytest.mark.parametrize("field,value", [
    ("operation_id", "unrelated-operation"),
    ("workflow_run_id", "unrelated-run"),
    ("admission_status", "rejected"),
    ("semantic_verdict", "passed"),
])
def test_mismatched_or_unadmitted_root_does_not_create_scope(field, value):
    events = chain()
    events[1].payload[field] = value
    assert ReaderTaskRecoveryScopes(events).get(events[-1]) is None


def test_cross_run_child_cannot_poison_valid_ancestor_scope():
    events = chain()
    child = ZfEvent(type="flow.discovery.failed", payload={
        "workflow_run_id": "another-run", "rework_of": events[-1].id,
    })
    scopes = ReaderTaskRecoveryScopes(events + [child])
    assert scopes.get(child) is None
    assert scopes.get(events[-1]) == ("run", "A", "impl-A")


def test_cycle_has_no_local_authority():
    events = chain()
    a = ZfEvent(type="flow.discovery.failed", payload={})
    b = ZfEvent(type="flow.discovery.requested", payload={"rework_of": a.id})
    a.payload["rework_of"] = b.id
    scopes = ReaderTaskRecoveryScopes(events + [a, b])
    assert scopes.get(a) is None
    assert scopes.get(b) is None


def test_aggregate_uses_unique_fanout_origin_without_freeform_task_claim():
    events = chain()
    started = ZfEvent(type="fanout.started", payload={
        "fanout_id": "reader-1", "trigger_event_id": events[-2].id,
    })
    aggregate = ZfEvent(type="flow.discovery.failed", payload={
        "fanout_id": "reader-1", "workflow_run_id": "run",
    })
    assert ReaderTaskRecoveryScopes(events + [started, aggregate]).get(aggregate) == (
        "run", "A", "impl-A",
    )
    ambiguous = ZfEvent(type="fanout.started", payload={
        "fanout_id": "reader-1", "trigger_event_id": "different-trigger",
    })
    assert ReaderTaskRecoveryScopes(events + [started, ambiguous, aggregate]).get(aggregate) is None


def test_task_reader_cap_through_runtime_keeps_run_and_other_tasks_live(tmp_path: Path):
    from zf.core.config.schema import (
        FanoutAggregateConfig, ProjectConfig, RoleConfig, SessionConfig,
        WorkflowConfig, WorkflowStageConfig, ZfConfig,
    )
    from zf.core.events.log import EventLog
    from zf.core.state.session import SessionStore
    from zf.core.task.schema import Task
    from zf.core.task.store import TaskStore
    from zf.runtime.orchestrator import Orchestrator
    from zf.runtime.tmux import TmuxSession
    from zf.runtime.transport import TmuxTransport
    from zf.runtime.workflow_anchor import mark_workflow_managed_task
    from zf.runtime.escalation_terminal import converge_unrecoverable_escalations
    from zf.runtime.run_manager import _pending_human_decisions, _blocking_human_decisions

    state_dir = tmp_path / ".zf"
    (state_dir / "memory").mkdir(parents=True)
    SessionStore(state_dir / "session.yaml").create(project_root=str(tmp_path))
    store = TaskStore(state_dir / "kanban.json")
    for task_id in ("PDD", "A", "B"):
        store.add(mark_workflow_managed_task(Task(
            id=task_id, title=task_id, status="in_progress",
        )))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="run.goal.started", task_id="PDD", correlation_id="run",
                       payload={"run_id": "run", "objective": "deliver"}))
    events = chain()
    for _ in range(STAGE_REPLAN_CAP):
        events.append(ZfEvent(type="flow.discovery.failed", payload=dict(events[-1].payload)))
    for event in events:
        log.append(event)
    cfg = ZfConfig(
        project=ProjectConfig(name="reader-cap"),
        session=SessionConfig(tmux_session="reader-cap"),
        roles=[RoleConfig(name="planner", backend="mock")],
        workflow=WorkflowConfig(stages=[WorkflowStageConfig(
            id="prd-post-verify-discovery", trigger="flow.discovery.requested",
            topology="fanout_reader", attempt_domain="task",
            aggregate=FanoutAggregateConfig(success_event="flow.discovery.completed",
                                            failure_event="flow.discovery.failed"),
        )]),
    )
    orch = Orchestrator(state_dir, cfg, TmuxTransport(
        TmuxSession(session_name="reader-cap", dry_run=True),
    ))
    orch.run_once(events=[events[-1]])
    # The periodic convergence path must also respect the task-local escalation.
    converge_unrecoverable_escalations(log.read_all(), writer=orch.event_writer,
                                       task_store=store, request_autoresearch=True)
    output = log.read_all()
    escalations = [event for event in output if event.type == "human.escalate"
                   and event.payload.get("source_event_id") == events[-1].id]
    assert len(escalations) == 1
    escalation = escalations[0]
    assert escalation.task_id == "A"
    assert escalation.payload["source_event_id"] == events[-1].id
    assert escalation.payload["failed_operation_id"] == "impl-A"
    assert escalation.payload["failure_scope"] == "task"
    assert escalation.payload["operator_required"] is True
    assert escalation.payload["max_auto_attempts"] == 0
    pending = _pending_human_decisions([escalation])
    assert len(pending) == 1 and pending[0]["task_id"] == "A"
    assert _blocking_human_decisions([escalation]) == []
    assert not any(event.type in {"run.goal.blocked", "run.manager.autoresearch.requested"}
                   for event in output)
    assert all(store.get(task_id).status == "in_progress" for task_id in ("PDD", "B"))
