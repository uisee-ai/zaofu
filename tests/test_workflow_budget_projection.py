from zf.core.events.model import ZfEvent
from zf.runtime.sdlc_graph import build_sdlc_graph
from zf.runtime.workflow_budget_projection import operation_budget_observations
from tests.test_sdlc_graph import sources


def test_graph_explains_budget_without_double_counting_or_inventing_money():
    requested = ZfEvent(id="request", type="workflow.operation.requested", origin="kernel", payload={
        "workflow_run_id": "run-a", "operation_id": "op", "role_instance": "verify-1",
        "operation_type": "fanout_reader_child", "request_hash": "hash",
    })
    event = ZfEvent(id="budget", type="workflow.budget.exceeded", origin="kernel", payload={
        "workflow_run_id": "run-a", "scope": "operation", "scope_id": "op",
        "limits": {"token_budget": 1500000},
        "measurement": {"total_tokens": 1510948, "unpriced_entries": 21, "total_usd": 0,
                        "baseline": {"input_tokens": 10, "output_tokens": 0, "cache_read_tokens": 100},
                        "current": {"input_tokens": 51780, "output_tokens": 2922, "cache_read_tokens": 1456356}},
    })
    observation = operation_budget_observations([event], run_id="run-a")["op"]
    assert observation["total_usd"] is None
    assert sum(observation[k] for k in ("input_tokens", "output_tokens", "cache_read_tokens")) == 1510948
    assert operation_budget_observations([event], run_id="other") == {}
    graph = build_sdlc_graph(sources(events=[(1, requested), (2, event)]))
    op = next(n for n in graph["nodes"] if n["id"] == "operation:op")
    assert op["data"]["budget_observation"] == observation


def test_partially_priced_usage_does_not_present_a_complete_total():
    event = ZfEvent(type="workflow.budget.exceeded", origin="kernel", payload={
        "workflow_run_id": "run", "scope": "operation", "scope_id": "op",
        "measurement": {"total_usd": 0.5, "unpriced_entries": 0,
                        "baseline": {"partial_entries": 2},
                        "current": {"partial_entries": 3}},
    })
    observation = operation_budget_observations([event], run_id="run")["op"]
    assert observation["partial_entries"] == 1
    assert observation["total_usd"] is None
