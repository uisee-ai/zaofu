"""A finished task cannot retain old replan ownership across run boundaries."""
import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.run_manager_rework_triage import active_immediate_replan_task_ids


def event(kind, *, run="run-a", task="A", origin="kernel", **payload):
    return ZfEvent(type=kind, task_id=task, origin=origin, correlation_id=run,
                   payload={"workflow_run_id": run, **payload})


def failure(run="run-a"):
    blocked = event("dev.blocked", run=run)
    triage = event("task.rework.triage.completed", run=run,
                   failed_event_id=blocked.id, recommended_action="request_replan",
                   retryable=False)
    return [blocked, triage]


def test_kernel_task_completion_retires_old_failure_and_new_failure_reopens():
    events = failure()
    assert active_immediate_replan_task_ids(events) == {"A"}
    events.append(event("task.done"))
    assert active_immediate_replan_task_ids(events) == set()
    events.extend(failure())
    assert active_immediate_replan_task_ids(events) == {"A"}


@pytest.mark.parametrize("kind", ["run.goal.completed", "run.goal.blocked"])
def test_only_same_run_terminal_releases_recovery(kind):
    events = failure() + [event(kind, run="run-b", task=None)]
    assert active_immediate_replan_task_ids(events) == {"A"}
    events.append(event(kind, task=None))
    assert active_immediate_replan_task_ids(events) == set()


@pytest.mark.parametrize("kind,kwargs", [
    ("task.done", {"run": "run-b"}),
    ("task.done", {"task": "B"}),
    ("task.done", {"origin": "external"}),
    ("task.done", {"origin": "worker"}),
    ("task.dispatched", {}),
    ("orchestrator.replan_requested", {}),
    ("task_map.ready", {}),
])
def test_unproven_progress_keeps_original_contract_suppressed(kind, kwargs):
    assert active_immediate_replan_task_ids(failure() + [event(kind, **kwargs)]) == {"A"}


def test_completion_uses_existing_workflow_run_aliases():
    events = [event("run.started", run="run-a", task=None, run_id="run-a")]
    events.append(event("workflow.invoke.requested", run="workflow-a", task=None,
                        run_id="run-a"))
    events.extend(failure("workflow-a"))
    events.append(event("task.done", run="run-a"))
    assert active_immediate_replan_task_ids(events) == set()


def test_ambiguous_unscoped_completion_does_not_release_recovery():
    events = failure() + [event("run.started", run="run-b", task=None)]
    events.append(ZfEvent(type="task.done", task_id="A", origin="kernel"))
    assert active_immediate_replan_task_ids(events) == {"A"}
