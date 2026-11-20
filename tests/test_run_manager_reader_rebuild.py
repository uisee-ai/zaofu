"""A resident recommendation reaches one failed Reader through kernel actions."""
import pytest

from tests.test_reader_undelivered_retry import _waiting
from zf.core.events import ZfEvent
from zf.runtime.run_manager import (
    _action_from_agent_recommendation, _consume_agent_recommendations, _settle_pending_action_effects,
)
from zf.runtime.workflow_operation import load_workflow_operation


def recommendation(runtime, context, operation_id, failed):
    op = load_workflow_operation(runtime.event_log, operation_id)
    return runtime.event_writer.append(ZfEvent(type="run.manager.agent.recommendation",
        actor="run-manager", payload={"recommended_route": "controlled_action", "action": "child-rebuild",
            "summary": "retry one exhausted environment wait after release",
            "action_payload": {"fanout_id": context.fanout_id, "fanout_child_id": "verify",
                "workflow_run_id": context.trace_id, "target_operation_id": operation_id,
                "active_attempt_id": op["active_attempt_id"], "source_event_id": failed.id}}))


def consume(runtime):
    return _consume_agent_recommendations(state_dir=runtime.state_dir, writer=runtime.event_writer,
        config=runtime.config, project_root=runtime.project_root, events=runtime.event_log.read_all(),
        causation_id="mock-rm-tick", reflect_fn=None)


def test_resident_recommendation_recovers_exact_reader_once(tmp_path):
    runtime, context, old, failed = _waiting(tmp_path)
    event = recommendation(runtime, context, old, failed)
    action = _action_from_agent_recommendation(event, event.payload)
    assert action["action"] == "child-rebuild"
    assert action["policy_decision"]["decision"] == "auto_decide"
    consume(runtime)
    intents = [e for e in runtime.event_log.read_all() if e.type == "repair.action.requested"]
    assert len(intents) == 1
    assert intents[0].payload["fanout_child_id"] == "verify"
    assert not runtime.transport.sent
    assert not any(e.type == "run.manager.action.effect.passed" for e in runtime.event_log.read_all())
    runtime.run_once(events=intents)
    consume(runtime)
    runtime.run_once(events=intents)
    assert len(runtime.transport.sent) == 1
    assert load_workflow_operation(runtime.event_log, old)["status"] == "failed"
    events = runtime.event_log.read_all()
    assert any(e.type == "repair.action.applied" for e in events)
    assert len([e for e in events if e.type == "repair.action.requested"]) == 1
    pending = [e for e in events if e.type == "run.manager.action.effect.pending"]
    assert len(pending) == 1
    assert pending[0].payload["fanout_child_id"] == "verify"
    assert _settle_pending_action_effects(events, runtime.event_writer) == 0
    dispatched = next(e for e in events if e.type == "fanout.child.dispatched")
    # A sibling or old attempt terminal cannot settle the selected retry.
    for changed in ({"child_id": "healthy-sibling"}, {"run_id": "old-attempt"}):
        runtime.event_writer.append(ZfEvent(type="fanout.child.completed", origin="kernel",
            correlation_id=context.trace_id, payload={**dispatched.payload, **changed}))
        assert _settle_pending_action_effects(runtime.event_log.read_all(), runtime.event_writer) == 0
    runtime.event_writer.append(ZfEvent(type="fanout.child.completed", origin="kernel",
        correlation_id=context.trace_id, payload=dispatched.payload))
    assert _settle_pending_action_effects(runtime.event_log.read_all(), runtime.event_writer) == 1
    assert _settle_pending_action_effects(runtime.event_log.read_all(), runtime.event_writer) == 0


@pytest.mark.parametrize("fault", ["run.paused", "run.cancelled", "dispatch.paused", "stale_attempt",
                                  "wrong_child", "old_failure", "new_dispatch"])
def test_rebuild_guards_are_rechecked_when_intent_is_executed(tmp_path, fault):
    runtime, context, old, failed = _waiting(tmp_path)
    recommendation(runtime, context, old, failed)
    consume(runtime)
    intent = next(e for e in runtime.event_log.read_all() if e.type == "repair.action.requested")
    if fault in {"run.paused", "run.cancelled", "dispatch.paused"}:
        runtime.event_writer.emit(fault, payload={"workflow_run_id": context.trace_id})
    elif fault == "stale_attempt":
        runtime.event_writer.emit("workflow.operation.started", payload={
            "operation_id": old, "active_attempt_id": "new-attempt"})
    elif fault == "new_dispatch":
        runtime.event_writer.emit("fanout.child.dispatched", payload={
            "fanout_id": context.fanout_id, "child_id": "verify", "run_id": "new"})
    else:
        # New admitted intent with a mismatched explicit target must fail closed.
        intent = runtime.event_writer.append(ZfEvent(type="repair.action.requested",
            origin="kernel", payload={**intent.payload, "action_id": "bad-target",
                "idempotency_key": "bad-target",
                ("fanout_child_id" if fault == "wrong_child" else "source_event_id"): "foreign"}))
    runtime.run_once(events=[intent])
    assert not runtime.transport.sent
    assert not any(e.type == "repair.action.applied" for e in runtime.event_log.read_all())


def test_nested_recommendation_cannot_override_bound_target(tmp_path):
    runtime, context, old, failed = _waiting(tmp_path)
    event = recommendation(runtime, context, old, failed)
    runtime.event_writer.append(ZfEvent(type=event.type, actor="run-manager",
        payload={**event.payload, "target_operation_id": "foreign"}))
    consume(runtime)
    consumed = [e for e in runtime.event_log.read_all() if e.type == "run.manager.agent.recommendation.consumed"]
    assert any(e.payload.get("status") == "blocked" and "identity conflict" in e.payload.get("reason", "")
               for e in consumed)


@pytest.mark.parametrize("verb", ["task-requeue", "stage-retrigger", "rescan-grant"])
def test_broader_recovery_routes_to_existing_owner_approval(verb):
    event = ZfEvent(type="run.manager.agent.recommendation", payload={
        "recommended_route": "controlled_action", "action": verb,
        "action_payload": {"task_id": "T1", "source_event_id": "failure"},
    })
    action = _action_from_agent_recommendation(event, event.payload)
    assert action["action"] == verb
    assert action["policy_decision"]["decision"] == "needs_approval"
    assert action["source_event_id"] == "failure"


def test_reader_retry_preserves_healthy_siblings_and_shared_anchor(tmp_path):
    from dataclasses import replace
    from zf.runtime.fanout import FanoutChild
    runtime, context, old, failed = _waiting(tmp_path)
    runtime.task_store.update("FLOW-ANCHOR", status="done")
    siblings = [FanoutChild(child_id=f"healthy-{i}", role_instance=f"reader-{i}",
                           target_ref=context.target_ref) for i in range(7)]
    context = replace(context, expected_children=[*context.expected_children, *siblings])
    runtime.event_writer.append(context.started_event())
    original = []
    for sibling in siblings:
        original.append(runtime.event_writer.append(ZfEvent(
            type="fanout.child.completed", origin="kernel", correlation_id=context.trace_id,
            payload={"fanout_id": context.fanout_id, "child_id": sibling.child_id,
                     "workflow_run_id": context.trace_id, "run_id": sibling.child_id,
                     "role_instance": sibling.role_instance})))
    # Restore this child's failed frontier after refreshing the manifest.
    failed = runtime.event_writer.append(ZfEvent(type=failed.type, origin="kernel",
                                                payload=failed.payload))
    recommendation(runtime, context, old, failed)
    consume(runtime)
    intents = [e for e in runtime.event_log.read_all() if e.type == "repair.action.requested"]
    runtime.run_once(events=intents)
    assert len(runtime.transport.sent) == 1
    assert runtime.task_store.get("FLOW-ANCHOR").status == "done"
    events = runtime.event_log.read_all()
    assert [e for e in events if e.type == "fanout.child.dispatched"][0].payload["child_id"] == "verify"
    for original_event in original:
        assert next(e for e in events if e.id == original_event.id).payload == original_event.payload
    assert len([e for e in events if e.type == "fanout.child.completed"]) == 7
