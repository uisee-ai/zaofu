from dataclasses import replace

import pytest

from tests.test_orchestrator_agent_execution_review import _case, _pending
from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.orchestrator_agent_semantic_failure import request_semantic_failure_checkpoint
from zf.runtime.run_manager import _execute_orchestrator_triage_request


def _escalated(runtime, *, target_role="dev-b", decision="escalate"):
    action = _pending(runtime)[0]
    _execute_orchestrator_triage_request(writer=runtime.event_writer, action=action,
        causation_id=action["source_event_id"], state_dir=runtime.state_dir, config=runtime.config)
    original = next(e for e in runtime.event_log.read_all()
                    if e.type == "orchestrator.semantic.failure.requested")
    ref = write_immutable_json_sidecar(runtime.state_dir, {"checkpoint_context": {
        "target_task_id": "TASK-1", "target_attempt_id": "lease-task-1",
        "target_stage_id": "impl", "target_role_instance": target_role,
    }}, root="fixtures/checkpoint", kind="checkpoint", schema_version="checkpoint.v1", created_by="test")
    runtime.event_writer.append(ZfEvent(type="orchestrator.semantic.checkpoint.requested",
        causation_id=original.id, payload={"operation_id": "oa-old", "checkpoint": "semantic_failure",
                                         "source_event_id": original.id, "checkpoint_input_ref": ref}))
    decision_ref = write_immutable_json_sidecar(runtime.state_dir, {"decision": decision},
        root="fixtures/decision", kind="decision", schema_version="decision.v1", created_by="test")
    submitted = runtime.event_writer.append(ZfEvent(type="orchestrator.semantic.decision.submitted",
        payload={"operation_id": "oa-old", "control_result_ref": decision_ref}))
    runtime.event_writer.append(ZfEvent(type="workflow.call.result.admitted",
        payload={"operation_id": "oa-old", "control_result_ref": decision_ref,
                 "source_event_id": submitted.id}))
    escalation = runtime.event_writer.append(ZfEvent(type="human.escalate", causation_id=submitted.id,
        payload={"orchestrator_operation_id": "oa-old", "orchestrator_decision_ref": decision_ref}))
    return escalation


def test_operator_reissues_exact_role_mismatch_once(tmp_path):
    from zf.runtime.orchestrator_agent_operator_review_retry import request_review_retry

    runtime, _ = _case(tmp_path, minimal_admitted=True)
    task = runtime.task_store.get("TASK-1")
    runtime.task_store.update("TASK-1", active_dispatch_id="", contract=replace(
        task.contract, owner_instance="dev-b"))
    escalation = _escalated(runtime)
    kwargs = dict(state_dir=runtime.state_dir, event_log=runtime.event_log,
                  event_writer=runtime.event_writer, config=runtime.config,
                  escalation_id=escalation.id, reason="review after fixing role binding")
    preview = request_review_retry(**kwargs)
    assert preview["status"] == "preview"
    assert preview["target_role_instance"] == "dev-a"
    assert len([e for e in runtime.event_log.read_all() if e.type == "orchestrator.semantic.failure.requested"]) == 1
    applied = request_review_retry(**kwargs, apply=True)
    assert applied["status"] == "requested"
    again = request_review_retry(**kwargs, apply=True)
    assert again["status"] == "already_requested"
    requests = [e for e in runtime.event_log.read_all() if e.type == "orchestrator.semantic.failure.requested"]
    assert len(requests) == 2
    checkpoint = request_semantic_failure_checkpoint(runtime, requests[-1])
    assert checkpoint.context.input_body["checkpoint_context"]["target_role_instance"] == "dev-a"


@pytest.mark.parametrize("role,decision", [("dev-a", "escalate"), ("dev-b", "continue")])
def test_other_escalations_cannot_be_reissued(tmp_path, role, decision):
    from zf.runtime.orchestrator_agent_operator_review_retry import request_review_retry

    runtime, _ = _case(tmp_path)
    escalation = _escalated(runtime, target_role=role, decision=decision)
    with pytest.raises(ValueError):
        request_review_retry(state_dir=runtime.state_dir, event_log=runtime.event_log,
            event_writer=runtime.event_writer, config=runtime.config,
            escalation_id=escalation.id, reason="operator review", apply=True)


def test_new_attempt_fences_old_escalation(tmp_path):
    from zf.runtime.orchestrator_agent_operator_review_retry import request_review_retry

    runtime, identity = _case(tmp_path)
    escalation = _escalated(runtime)
    runtime.event_writer.append(ZfEvent(type="workflow.operation.requested", task_id="TASK-1",
        payload={**identity, "operation_id": "impl-2", "operation_generation": 2}))
    with pytest.raises(ValueError, match="no longer eligible"):
        request_review_retry(state_dir=runtime.state_dir, event_log=runtime.event_log,
            event_writer=runtime.event_writer, config=runtime.config,
            escalation_id=escalation.id, reason="operator review", apply=True)


def test_unadmitted_decision_cannot_be_reissued(tmp_path):
    from zf.runtime.orchestrator_agent_operator_review_retry import request_review_retry

    runtime, _ = _case(tmp_path)
    escalation = _escalated(runtime)
    events = runtime.event_log.read_all()
    admitted = next(e for e in events if e.type == "workflow.call.result.admitted"
                    and e.payload.get("operation_id") == "oa-old")
    class WithoutAdmission:
        def read_all(self):
            return [e for e in events if e.id != admitted.id]
    with pytest.raises(ValueError, match="admitted decision"):
        request_review_retry(state_dir=runtime.state_dir, event_log=WithoutAdmission(),
            event_writer=runtime.event_writer, config=runtime.config,
            escalation_id=escalation.id, reason="operator review", apply=True)
