"""Already decoded Workflow inputs need no second read-model build."""
from zf.core.events.model import ZfEvent
from zf.runtime.operation_projection import project_workflow_operation
from zf.web.projections import operations


def test_supplied_snapshot_keeps_operation_semantics_without_querying_index(tmp_path, monkeypatch):
    events = [ZfEvent(type="workflow.operation.requested", payload={"operation_id": "op", "request_id": "request"}),
              ZfEvent(type="workflow.operation.started", payload={"operation_id": "op"})]
    def forbidden(*args, **kwargs):
        raise AssertionError("supplied snapshot triggered a second index/hydration")
    monkeypatch.setattr(operations.read_model, "hydrate_events_by_ref", forbidden)
    expected = project_workflow_operation(tmp_path, "op", events=events)
    actual = operations.workflow_operation(tmp_path, "op", events=events)
    assert actual["source"] == "events.jsonl"
    assert actual["projection_state"] == "ready"
    for field in ("operation_id", "status", "timeline", "freshness", "blockers"):
        assert actual.get(field) == expected.get(field)
    empty = operations.workflow_operation(tmp_path, "op", events=[])
    assert empty["freshness"]["event_count"] == 0
