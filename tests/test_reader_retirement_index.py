"""Recovery proofs stay causal without rescanning unrelated provider traffic."""
from types import SimpleNamespace

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.reader_failure_supersession import reader_failure_was_replaced
from zf.runtime.reader_replan_index import ReaderReplanIndex
from zf.runtime.reader_replan_ownership import active_candidate_replan_owns_failure


class CountedEvents(list):
    visits = 0

    def __iter__(self):
        for event in super().__iter__():
            self.visits += 1
            yield event


def event(kind, **payload):
    return ZfEvent(type=kind, origin="kernel", correlation_id="run", payload={
        "workflow_run_id": "run", "pdd_id": "goal", "task_map_generation": "generation", **payload})


def history():
    source = event("dev.blocked", task_id="old")
    trigger = event("flow.discovery.trigger", rework_of=source.id, task_id="old")
    start = event("fanout.started", fanout_id="reader", trigger_event_id=trigger.id)
    ready = event("task_map.ready", rework_of=source.id, rework_summary={"gap_tasks": [
        {"task_id": "new", "supersedes_task_ids": ["old"]}]})
    admitted = event("task.pipeline.generation.admitted", schema_version="task-pipeline-generation.v1",
                     trigger_event_id=ready.id, task_ids=["new"])
    failure = event("flow.discovery.failed", fanout_id="reader", task_id="old")
    return CountedEvents([source, trigger, start, ready, admitted, failure]), failure


def test_replacement_lookup_cost_does_not_grow_with_provider_history():
    events, failure = history()
    events[1:1] = [event("codex.hook.post_tool_use") for _ in range(2000)]
    index = ReaderReplanIndex(events)
    events.visits = 0
    assert reader_failure_was_replaced(events, failure, event_index=index)
    assert events.visits < 100


def test_candidate_ownership_uses_only_history_before_failure():
    config = SimpleNamespace(workflow=SimpleNamespace(stages=[SimpleNamespace(
        id="plan", attempt_domain="plan", topology="fanout_reader", trigger="plan.requested")]))
    repair = event("orchestrator.replan_requested", rework_of="cause", rework_source="integration.failed")
    trigger = event("plan.requested", rework_of="cause")
    started = event("fanout.started", fanout_id="plan", stage_id="plan", trigger_event_id=trigger.id)
    failure = event("flow.discovery.failed", stage_id="scan", task_id="old")
    ended = event("fanout.cancelled", fanout_id="plan")
    events = CountedEvents([repair, trigger, started,
                           *[event("codex.hook.post_tool_use") for _ in range(2000)], failure, ended])
    index = ReaderReplanIndex(events)
    events.visits = 0
    assert active_candidate_replan_owns_failure(config, events, failure, event_index=index)
    assert events.visits < 100
    events.remove(ended)
    events.insert(-1, ended)
    assert not active_candidate_replan_owns_failure(
        config, events, failure, event_index=ReaderReplanIndex(events))
    events.remove(ended)
    events.remove(trigger)
    events.append(trigger)
    assert not active_candidate_replan_owns_failure(
        config, events, failure, event_index=ReaderReplanIndex(events))


@pytest.mark.parametrize("change", ["append", "copy"])
def test_retirement_cannot_reuse_another_snapshot(change):
    events, failure = history()
    index = ReaderReplanIndex(events)
    if change == "append":
        events.append(event("task_map.ready"))
    else:
        events = list(events)
    with pytest.raises(ValueError, match="snapshot"):
        reader_failure_was_replaced(events, failure, event_index=index)


def test_preceding_admission_cannot_retire_a_later_occurrence():
    events, failure = history()
    source = events.pop(0)
    events.insert(-1, source)
    assert not reader_failure_was_replaced(events, failure, event_index=ReaderReplanIndex(events))


def test_ambiguous_reader_start_does_not_retire_failure():
    events, failure = history()
    events.append(event("fanout.started", fanout_id="reader", trigger_event_id=events[1].id))
    assert not reader_failure_was_replaced(events, failure, event_index=ReaderReplanIndex(events))
