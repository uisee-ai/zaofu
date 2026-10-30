import pytest

from tests.test_read_only_dynamic_continuation import _config, _seed, _state
from zf.core.config.schema import RoleConfig
from zf.runtime.read_only_dynamic_continuation import execute_read_only_continuation
from zf.runtime.workflow_operation import WorkflowOperationService


def test_concurrent_sibling_fragments_share_parent_capacity(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from zf.runtime.dynamic_fragment_policy import action_from_fragment_proposal, canonical_fragment_digest

    state, log, writer = _state(tmp_path)
    config = _config()
    first = _seed(state, log, writer, config=config)
    proposal = next(e for e in log.read_all() if e.type == "workflow.fragment.proposed")
    body = {**proposal.payload, "fragment_id": "FRAG-2"}
    body["fragment_digest"] = canonical_fragment_digest(body)
    event = writer.emit("workflow.fragment.proposed", actor="research-agent", payload=body)
    second = action_from_fragment_proposal(event, body)

    def execute(action):
        return execute_read_only_continuation(state, config=config, event_log=log, writer=writer, action=action)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, [first, second]))
    assert sum(result.status == "dispatched" for result in results) == 1
    assert any(result.reason == "parent_child_capacity_exceeded" for result in results)
    assert sum(e.type == "workflow.invoke.requested" for e in log.read_all()) == 1


@pytest.mark.parametrize("fault,reason", [
    ("width", "max_children_exceeded"),
    ("unbounded", "bounded_max_children_required"),
    ("join", "required_children_must_join_all"),
    ("depth", "dynamic_depth_exceeded"),
    ("cost", "budget_usage_unknown"),
])
def test_bounded_continuation_fails_before_dispatch(tmp_path, monkeypatch, fault, reason):
    state, log, writer = _state(tmp_path)
    config = _config(budget=10)
    action = _seed(state, log, writer, config=config)
    if fault == "width":
        config.roles.append(RoleConfig(name="extra", role_kind="reader"))
        config.workflow.stages[0].roles.append("extra")
    elif fault == "unbounded":
        action["budgets"] = {}
    elif fault == "join":
        config.workflow.stages[0].aggregate.mode = "first_success"
    elif fault == "depth":
        WorkflowOperationService(state_dir=state, event_log=log, event_writer=writer).ensure_operation(
            operation_id="nested-parent", workflow_run_id=action["workflow_run_id"],
            parent_operation_id=action["parent_operation_id"], operation_type="dynamic_read_only_workflow",
            request={"reason": "already expanded"},
        )
        action["parent_operation_id"] = "nested-parent"
    else:
        def unavailable(*args, **kwargs):
            raise OSError("meter unavailable")
        monkeypatch.setattr("zf.core.cost.tracker.CostTracker.usage_totals", unavailable)
    result = execute_read_only_continuation(state, config=config, event_log=log, writer=writer, action=action)
    assert result.reason == reason
    assert not any(e.type == "workflow.invoke.requested" for e in log.read_all())


@pytest.mark.parametrize("status,receipt,allowed", [
    ("settled", {"ref": "admitted-result"}, True),
    ("settled", {}, False),
    ("cancelled", {}, False),
    ("failed", {}, False),
])
def test_capacity_release_requires_admitted_result(tmp_path, status, receipt, allowed):
    from zf.runtime.dynamic_continuation_lifecycle import capacity_error
    from zf.runtime.workflow_operation import reduce_workflow_operations

    state, log, writer = _state(tmp_path)
    config = _config()
    action = _seed(state, log, writer, config=config)
    dispatched = execute_read_only_continuation(state, config=config, event_log=log, writer=writer, action=action)
    rows = reduce_workflow_operations(log.read_all())
    child = rows[dispatched.operation_id]
    child.update(status=status, admitted_call_result_ref=receipt)
    assert (capacity_error(state, rows, action, "new-sibling") == "") is allowed


@pytest.mark.parametrize("fault", ["none", "missing", "old_attempt", "open_fanout"])
def test_cancelled_fragment_releases_only_after_all_dispatch_receipts(tmp_path, fault):
    from zf.runtime.dynamic_continuation_lifecycle import capacity_error
    from zf.runtime.workflow_operation import reduce_workflow_operations
    state, log, writer = _state(tmp_path)
    config = _config()
    action = _seed(state, log, writer, config=config)
    result = execute_read_only_continuation(state, config=config, event_log=log, writer=writer, action=action)
    service = WorkflowOperationService(state_dir=state, event_log=log, event_writer=writer)
    service.cancel(operation_id=result.operation_id, request_hash=result.request_hash,
                   workflow_run_id=action["workflow_run_id"], reason="test cancellation")
    writer.emit("fanout.started", actor="zf-cli", payload={"fanout_id": "wave", "trigger_payload": {"workflow_operation_id": result.operation_id}})
    writer.emit("fanout.child.dispatched", actor="zf-cli", payload={"fanout_id": "wave", "role_instance": "reader", "run_id": "current"})
    if fault != "open_fanout":
        writer.emit("fanout.cancelled", actor="zf-cli", payload={"fanout_id": "wave"})
    if fault != "missing":
        writer.emit("worker.state.changed", actor="reader", payload={"to": "idle", "source": "run_cancel_reconciliation",
            "dispatch_id": "old" if fault == "old_attempt" else "current", "provider_stop_confirmed": True})
    events = log.read_all()
    assert (capacity_error(state, reduce_workflow_operations(events), action, "new", events=events) == "") is (fault == "none")


def test_cancelled_tree_keeps_admitted_sibling_completion(tmp_path):
    from zf.core.events import ZfEvent
    from zf.runtime.dynamic_cancellation_receipts import cancelled_tree_released
    root = {"operation_id": "root", "status": "cancelled"}
    rows = {"root": root, "completed": {"parent_operation_id": "root", "status": "settled",
            "admitted_call_result_ref": {"sha256": "a" * 64}, "role_instance": "reader", "dispatch_id": "finished"}}
    events = [ZfEvent(type=kind, actor="zf-cli", payload=payload) for kind, payload in [
        ("fanout.started", {"fanout_id": "wave", "trigger_payload": {"workflow_operation_id": "root"}}),
        ("fanout.child.dispatched", {"fanout_id": "wave", "role_instance": "reader", "run_id": "finished"}),
        ("fanout.cancelled", {"fanout_id": "wave"})]]
    assert cancelled_tree_released(rows, root, events)
    events.insert(1, ZfEvent(type="fanout.child.dispatched", actor="zf-cli", payload={
        "fanout_id": "wave", "role_instance": "reader", "run_id": "old-unconfirmed"}))
    assert not cancelled_tree_released(rows, root, events)
