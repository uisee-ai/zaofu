"""Operation graph behavior across ordering, causality and bounded previews."""

from zf.core.events.model import ZfEvent
from zf.runtime.sdlc_graph import build_sdlc_graph
from tests.test_sdlc_graph import sources


def _operation(graph, identity):
    return next(node["data"] for node in graph["nodes"] if node["id"] == f"operation:{identity}")


def test_operation_activity_keeps_causality_order_history_and_latest_usage():
    first = "2026-01-01T00:00:01Z"
    replay = "2026-01-01T00:00:02Z"
    payload_a = {"workflow_run_id": "run-a", "operation_id": "A", "parent_stage_id": "build"}
    payload_b = {"workflow_run_id": "run-a", "operation_id": "B", "parent_stage_id": "build"}
    # The causation chain is deliberately in reverse ledger order. Resolving it
    # requires multiple passes, and the visible output must retain ledger order.
    events = [
        ZfEvent(id="deep", type="agent.output", causation_id="middle"),
        ZfEvent(id="middle", type="codex.hook.pre_tool_use", causation_id="root"),
        ZfEvent(id="root", type="provider.message", causation_id="request-a"),
        ZfEvent(id="request-a", type="workflow.operation.requested", ts=first, payload=payload_a),
        ZfEvent(id="replay-a", type="workflow.operation.requested", ts=replay, payload=payload_a),
        ZfEvent(id="request-b", type="workflow.operation.requested", ts=replay, payload=payload_b),
        ZfEvent(id="usage-a", type="agent.usage", causation_id="root", payload={"model": "model-a"}),
        ZfEvent(id="usage-b", type="agent.usage", causation_id="request-b", payload={"model": "model-b"}),
        # A nonempty unknown operation ID blocks fallback to the causal parent.
        ZfEvent(id="foreign", type="agent.output", causation_id="request-a", payload={"operation_id": "unknown"}),
    ]
    historical_source = sources(events=list(enumerate(events, 1)))
    historical_source["scope"]["historical"] = True
    history = build_sdlc_graph(historical_source)
    assert history["scope"]["historical"]
    a = _operation(history, "A")
    b = _operation(history, "B")
    assert a["requested_at"] == first
    assert [event["id"] for event in a["recent_activity"]] == ["deep", "middle", "root", "usage-a"]
    assert [event["id"] for event in b["recent_activity"]] == ["usage-b"]
    assert a["agent_binding"]["model"] == "model-a"
    assert b["agent_binding"]["model"] == "model-b"

    events.extend(ZfEvent(id=f"a-{i}", type="agent.output", causation_id="request-a") for i in range(12))
    latest = build_sdlc_graph(sources(events=list(enumerate(events, 1))))
    a = _operation(latest, "A")
    assert [event["id"] for event in a["recent_activity"]] == [f"a-{i}" for i in range(2, 12)]
    # The most recent usage is older than the last 10 activities and still binds.
    assert a["agent_binding"]["source_event_id"] == "usage-a"
    assert a["agent_binding"]["model"] == "model-a"


def test_duplicate_event_ids_keep_first_matching_request_for_each_operation():
    first = "2026-01-01T00:00:01Z"
    second = "2026-01-01T00:00:02Z"
    events = [
        ZfEvent(id="same", type="workflow.operation.requested", ts=first,
                payload={"workflow_run_id": "run-a", "operation_id": "A"}),
        ZfEvent(id="same", type="workflow.operation.requested", ts=second,
                payload={"workflow_run_id": "run-a", "operation_id": "B"}),
        ZfEvent(id="activity", type="agent.output", causation_id="same"),
    ]
    graph = build_sdlc_graph(sources(events=list(enumerate(events, 1))))
    assert _operation(graph, "A")["requested_at"] == first
    assert _operation(graph, "B")["requested_at"] == first
    assert _operation(graph, "A")["recent_activity"] == []
    assert [event["id"] for event in _operation(graph, "B")["recent_activity"]] == ["activity"]
