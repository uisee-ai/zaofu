"""Result repair owns the previous turn's close on a rebuilt Reader."""
import pytest

from tests.test_reader_fanout_runtime import _durable_reader_state
from tests.test_run_manager_reader_rebuild import consume
from zf.core.events import ZfEvent
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.result_submit import SemanticResultSubmitService, ResultSubmitError, provision_role_submit_credential
from zf.runtime.workflow_operation import reduce_workflow_operations


@pytest.mark.parametrize("split_stop", [False, True])
def test_rebuilt_reader_receives_protocol_correction_before_previous_turn_loss(tmp_path, split_stop):
    state, log, transport, runtime, config, child = _durable_reader_state(tmp_path, semantic_submit=True)
    writer = runtime.event_writer
    config.workflow.task_attempt.mode = "enforce"
    writer.emit("run.started", payload={"workflow_run_id": "run-durable"})
    for turn in ("original", "read-continuation"):
        runtime.run_once(events=[writer.emit("provider.turn.closed", actor="verify-1",
            payload={"backend": "codex", "turn_id": turn, "provider_status": "completed"})])
    earlier = log.query(type="fanout.child.failed")[-1]
    op = reduce_workflow_operations(log.read_all())[child["payload"]["operation_id"]]
    writer.emit("run.manager.agent.recommendation", actor="run-manager",
        payload={"recommended_route": "controlled_action", "action": "child-rebuild",
                 "summary": "Rebuild the failed Reader only.", "action_payload": {
                    "workflow_run_id": op["workflow_run_id"], "fanout_id": earlier.payload["fanout_id"],
                    "fanout_child_id": earlier.payload["child_id"], "target_operation_id": op["operation_id"],
                    "source_event_id": earlier.id, "active_attempt_id": op["active_attempt_id"]}})
    consume(runtime)
    runtime.run_once(events=log.query(type="repair.action.requested"))
    dispatch = log.query(type="fanout.child.dispatched")[-1]
    new_id = dispatch.payload["operation_id"]
    assert dispatch.payload["payload"]["retry_of_operation_id"] == op["operation_id"]
    assert not dispatch.payload.get("request_hash") and dispatch.payload["payload"]["request_hash"]
    writer.append(ZfEvent(type="codex.hook.user_prompt_submit", actor="verify-1",
        payload={"session_id": "session", "turn_id": "rebuilt"}))
    service = SemanticResultSubmitService(state_dir=state, event_log=log, event_writer=writer)
    credential = provision_role_submit_credential(state, "verify-1", rotate=False).read_text().strip()
    with pytest.raises(ResultSubmitError, match="result was not admitted"):
        service.submit(operation_id=new_id, role_instance="verify-1", credential=credential,
                       semantic_result={"verdict": "passed", "summary": "Incomplete receipt"})
    correction, = log.query(type="workflow.call.result.repair.requested")
    runtime.run_once(events=[correction])
    assert not log.query(type="workflow.call.result.repair.dispatched")
    before = len(transport.sent)
    stop = writer.append(ZfEvent(type="codex.hook.stop", actor="verify-1",
        payload={"session_id": "session", "turn_id": "rebuilt"}))
    if split_stop:
        runtime.run_once(events=[stop])
        assert len(log.query(type="workflow.call.result.repair.dispatched")) == 1
    closed = writer.emit("provider.turn.closed", actor="verify-1",
                         payload={"backend": "codex", "turn_id": "rebuilt", "provider_status": "completed"})
    runtime.run_once(events=[closed])
    resumed = Orchestrator(state, config, transport)
    resumed.run_once(events=[closed])
    resumed.run_once(events=[])
    receipts = log.query(type="workflow.call.result.repair.dispatched")
    assert len(receipts) == 1 and receipts[0].payload["operation_id"] == new_id
    assert len(transport.sent) == before + 1
    assert not [event for event in log.query(type="fanout.child.dispatch_lost") if event.causation_id == closed.id]
    assert reduce_workflow_operations(log.read_all())[new_id]["status"] == "running"
    # The correction's own turn can still fail if it produces no result.
    resumed.event_writer.emit("codex.hook.user_prompt_submit", actor="verify-1",
                              payload={"session_id": "session", "turn_id": "correction"})
    later = resumed.event_writer.emit("provider.turn.closed", actor="verify-1",
        payload={"backend": "codex", "turn_id": "correction", "provider_status": "completed"})
    resumed.run_once(events=[later])
    losses = [e for e in log.query(type="fanout.child.dispatch_lost") if e.causation_id == later.id]
    assert len(losses) == 1 and losses[0].payload["run_id"] == dispatch.payload["run_id"]
    assert len(log.query(type="workflow.call.result.repair.dispatched")) == 1


@pytest.mark.parametrize("guard", ["paused", "cancelled", "shutdown", "dead", "exhausted", "admitted"])
def test_result_repair_precedence_respects_current_delivery_authority(tmp_path, guard):
    from tests.test_result_repair_delivery import rejected_reader

    state, log, transport, runtime, _, _ = rejected_reader(tmp_path)
    writer = runtime.event_writer
    request, = log.query(type="workflow.call.result.repair.requested")
    run_id = request.payload["workflow_run_id"]
    if guard in {"cancelled", "paused"}:
        writer.emit(f"run.{guard}", actor="kernel", correlation_id=run_id,
                    payload={"workflow_run_id": run_id})
    elif guard == "shutdown":
        (state / "shutdown-requested").touch()
    elif guard == "dead":
        transport.is_alive = lambda _: False
    elif guard == "exhausted":
        writer.emit(request.type, actor=request.actor, correlation_id=run_id, payload={
            **request.payload, "repair_round": request.payload["repair_cap"] + 1,
        })
    elif guard == "admitted":
        writer.emit("workflow.call.result.admitted", actor="zf-cli", correlation_id=run_id,
                    payload={"operation_id": request.payload["operation_id"],
                             "request_hash": request.payload["request_hash"]})
    terminal = writer.emit("provider.turn.closed", actor="verify-1",
                           payload={"backend": "codex", "turn_id": "source", "provider_status": "completed"})
    runtime.run_once(events=[terminal])
    assert not log.query(type="workflow.call.result.repair.dispatched")
    assert not [event for event in log.query(type="worker.continuation.reconciled")
                if event.payload.get("recovery_action") == "result_protocol_correction"
                and event.payload.get("delivery_status") == "sent"]
