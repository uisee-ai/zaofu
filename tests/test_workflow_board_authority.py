from types import SimpleNamespace

import pytest

from zf.core.config.workflow_profiles import WorkflowProfileError, expand_prd_flow
from zf.core.events import ZfEvent
from zf.runtime import workflow_board_authority as authority


@pytest.mark.parametrize("value", [
    {"enabled": 1}, {}, {"enabled": True, "silenceSeconds": False},
    {"enabled": True, "silenceSeconds": 0}, {"enabled": True, "members": ["reader"]},
    {"enabled": True, "run_id": "preselected"}, True,
])
def test_public_board_rejects_invalid_policy_and_preselected_members(value):
    with pytest.raises(WorkflowProfileError, match="runBoard"):
        expand_prd_flow({"runBoard": value})


def test_explicit_public_board_disabled_and_omitted():
    assert "run_board_policy" not in expand_prd_flow({})["metadata"]
    assert expand_prd_flow({"runBoard": {"enabled": False}})["metadata"]["run_board_policy"] == {
        "enabled": False, "silence_seconds": 300,
    }


def event(kind, *, origin="kernel", **fields):
    return ZfEvent(type=kind, origin=origin, correlation_id="run", payload={"workflow_run_id": "run", **fields})


def test_fixed_reader_needs_current_approved_run_and_real_kernel_dispatch(tmp_path, monkeypatch):
    policy = {"enabled": True, "silence_seconds": 120}
    config = SimpleNamespace(workflow=SimpleNamespace(flow_metadata={"run_board_policy": policy}))
    node = {"node_id": "stage:prd-scan", "stage_id": "prd-scan", "roles": ["reader"]}
    graph = {"flow_kind": "prd", "run_board_policy": policy, "nodes": [node], "roles": [{"instance_id": "reader"}]}
    scope = {"revision": 2, "request_id": "req", "proposal_digest": "approved", "effective_config_digest": "cfg"}
    monkeypatch.setattr(authority, "approved_stage_graph", lambda *_: (graph, scope, None))
    monkeypatch.setattr(authority, "approved_stage_roster", lambda *_: [])
    admit = event("run.admission.admitted", request_id="req", effective_config_digest="cfg")
    invoke = event("workflow.invoke.accepted", request_id="req", request_revision=2,
                   workflow_generation="approved", effective_config_digest="cfg")
    binding = {"role_instance": "reader", "attempt_id": "one", "request_hash": "hash", "operation_id": "first"}
    receipt = event("fanout.child.dispatched", **binding)
    operation = {"role_instance": "reader", "active_attempt_id": "one", "parent_stage_id": "prd-scan",
                 "workflow_run_id": "run", "operation_id": "first", "request_hash": "hash", "status": "running"}
    other = {**operation, "operation_id": "foreign", "workflow_run_id": "another"}
    ops = {"first": operation, "foreign": other}
    def board(*events):
        return authority.board_for_run(tmp_path, config, events, ops, "run")

    assert authority.permits(board(admit, invoke, receipt), operation)
    assert not authority.permits(board(admit, invoke, receipt), other)
    with pytest.raises(ValueError, match="admitted Run"):
        board(invoke, receipt)
    with pytest.raises(ValueError, match="admitted workflow invocation"):
        board(admit, receipt)
    with pytest.raises(ValueError, match="invoked proposal"):
        board(admit, event("workflow.invoke.accepted", **{**invoke.payload, "request_revision": 1}), receipt)
    for invalid in (
        event("fanout.child.dispatched", origin="worker", **binding),
        event("fanout.child.dispatched", **binding, workflow_generation="old"),
        event("fanout.child.dispatched", **binding, workflow_run_id="another"),
        event("fanout.child.dispatched", **{**binding, "attempt_id": "reused-role"}),
    ):
        assert not authority.permits(board(admit, invoke, invalid), operation)
    earlier = board(admit, invoke, receipt)
    new_map = event("task_map.ready", task_map_generation="map-two")
    advanced = board(admit, invoke, receipt, new_map)
    assert advanced["generation"] != earlier["generation"]
    assert not authority.permits(advanced, operation)
    # Adding an unrelated role to the card/agent palette never makes it a fixed stage member.
    node["roles"] = ["someone-else"]
    assert not authority.permits(board(admit, invoke, receipt), operation)
