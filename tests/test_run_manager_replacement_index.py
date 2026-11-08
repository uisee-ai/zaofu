"""Recovery retirement stays causal without repeated linear source lookup."""
import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.run_manager_rework_triage import (
    active_immediate_replan_task_ids,
    pending_immediate_replan_actions,
)


def event(kind, identity, **payload):
    return ZfEvent(type=kind, id=identity, origin="kernel", correlation_id="run",
                   payload={"workflow_run_id": "run", **payload})


def failure(task):
    source = event("dev.blocked", "source-" + task, task_id=task)
    triage = event("task.rework.triage.completed", "triage-" + task,
                   task_id=task, failed_event_id=source.id,
                   recommended_action="request_replan", retryable=False)
    return [source, triage]


def replacement(task):
    ready = event("task_map.ready", "ready-" + task, rework_of="source-" + task,
                  rework_summary={"gap_tasks": [
                      {"task_id": "new-" + task, "supersedes_task_ids": [task]}]})
    admitted = event("task.pipeline.generation.admitted", "admitted-" + task,
                     trigger_event_id=ready.id, schema_version="task-pipeline-generation.v1",
                     task_ids=["new-" + task])
    return [ready, admitted]


def selected(events, projection):
    if projection == "pending":
        return {row["task_id"] for row in pending_immediate_replan_actions(events)}
    return active_immediate_replan_task_ids(events)


@pytest.mark.parametrize("projection", ["pending", "active"])
def test_many_failures_do_not_linearly_search_the_full_ledger(projection):
    class IndexedEvents(list):
        def index(self, *args):
            raise AssertionError("full-ledger equality search repeated for a failed task")

    events = IndexedEvents([event("codex.hook.post_tool_use", "noise-" + str(i))
                            for i in range(100)])
    for task in ("A", "B", "C"):
        events.extend(failure(task))
    events.extend(replacement("A"))
    assert selected(events, projection) == {"B", "C"}
    events.extend(replacement("B"))
    assert selected(events, projection) == {"C"}


@pytest.mark.parametrize("projection", ["pending", "active"])
@pytest.mark.parametrize("invalid", ["unadmitted", "other-run", "other-cause", "partial", "external"])
def test_unproven_replacement_does_not_retire_recovery(projection, invalid):
    events = failure("A") + failure("B")
    ready, admitted = replacement("A")
    if invalid == "other-run":
        admitted.payload["workflow_run_id"] = "other"
    elif invalid == "other-cause":
        ready.payload["rework_of"] = "different-failure"
    elif invalid == "partial":
        ready.payload["rework_summary"]["gap_tasks"].append(
            {"task_id": "missing-successor", "supersedes_task_ids": ["A"]})
    elif invalid == "external":
        admitted.origin = "external"
    events.extend([ready] if invalid == "unadmitted" else [ready, admitted])
    assert selected(events, projection) == {"A", "B"}


@pytest.mark.parametrize("projection", ["pending", "active"])
def test_new_failure_of_same_task_is_not_retired_by_old_admission(projection):
    events = failure("A") + replacement("A")
    assert selected(events, projection) == set()
    source, triage = failure("A")
    source.id = "new-failure-A"
    triage.id = "new-triage-A"
    triage.payload["failed_event_id"] = source.id
    events.extend([source, triage])
    assert selected(events, projection) == {"A"}
