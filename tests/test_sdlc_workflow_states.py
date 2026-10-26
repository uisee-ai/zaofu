"""Compiled role and aggregate nodes follow execution evidence."""

import pytest

from tests.test_sdlc_graph import sources
from zf.core.events.model import ZfEvent
from zf.runtime.sdlc_graph import build_sdlc_graph


def graph(events):
    compiled = {"nodes": [
        {"node_id": "stage:verify", "stage_id": "verify"},
        {"node_id": "aggregate:verify", "stage_id": "verify:aggregate"},
        {"node_id": "role:reader", "stage_id": "reader"},
        {"node_id": "event:discovery.requested", "type": "external_event"},
    ], "edges": []}
    return build_sdlc_graph(sources(compiled=compiled, events=list(enumerate(events, 1))))


def event(kind, **payload):
    return ZfEvent(type=kind, origin="kernel", payload=payload)


def state(result, identity):
    return next(n for n in result["nodes"] if n["id"] == "workflow:" + identity)


@pytest.mark.parametrize("verdict", ["completed", "failed", "cancelled"])
def test_aggregate_uses_kernel_verdict_not_completed_child_count(verdict):
    rows = [event("fanout.started", fanout_id="F", stage_id="verify"),
            event("fanout.child.completed", fanout_id="F", child_id="child", run_id="attempt")]
    assert state(graph(rows), "aggregate:verify")["status"] == "pending"
    terminal = event("fanout.aggregate.completed", fanout_id="F", status=verdict)
    result = graph([*rows, terminal])
    assert state(result, "aggregate:verify")["status"] == verdict
    assert state(result, "aggregate:verify")["data"]["status_source_event_id"] == terminal.id


def test_latest_fanout_resets_aggregate_and_old_completion_cannot_overwrite_it():
    rows = [event("fanout.started", fanout_id="old", stage_id="verify"),
            event("fanout.aggregate.completed", fanout_id="old", status="completed"),
            event("fanout.started", fanout_id="new", stage_id="verify"),
            event("fanout.synth.dispatched", fanout_id="new"),
            event("fanout.aggregate.completed", fanout_id="old", status="completed")]
    assert state(graph(rows), "aggregate:verify")["status"] == "running"
    rows.append(ZfEvent(type="fanout.aggregate.completed", origin="worker",
                       payload={"fanout_id": "new", "status": "completed"}))
    assert state(graph(rows), "aggregate:verify")["status"] == "running"


def test_stage_owned_operation_also_updates_role_and_links_execution():
    rows = [event("workflow.operation.requested", workflow_run_id="run-a", operation_id="O1",
                  parent_stage_id="verify", role_instance="reader"),
            event("workflow.operation.started", workflow_run_id="run-a", operation_id="O1",
                  role_instance="reader")]
    result = graph(rows)
    assert state(result, "role:reader")["status"] == "running"
    assert state(result, "stage:verify")["status"] == "running"
    assert any(e["source"] == "workflow:role:reader" and e["target"] == "operation:O1"
               for e in result["edges"])
    rows.append(event("workflow.operation.requested", workflow_run_id="run-a", operation_id="O2",
                      parent_stage_id="verify", role_instance="reader"))
    assert state(graph(rows), "role:reader")["status"] == "running"
    rows.extend([event("workflow.operation.failed", workflow_run_id="run-a", operation_id="O1"),
                 event("workflow.operation.settled", workflow_run_id="run-a", operation_id="O2")])
    assert state(graph(rows), "role:reader")["status"] == "settled"


def test_trigger_node_records_occurrence_without_claiming_completion():
    trigger = event("discovery.requested")
    result = graph([trigger])
    node = state(result, "event:discovery.requested")
    assert node["status"] == "reported"
    assert node["data"]["source_event_ids"] == [trigger.id]
    assert state(result, "aggregate:verify")["status"] == "pending"
    assert state(graph([]), "event:discovery.requested")["status"] == "pending"
