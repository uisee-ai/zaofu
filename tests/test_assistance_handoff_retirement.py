"""An admitted worker handoff must not wait on its expired advisory notice."""

from copy import deepcopy

import pytest

from tests.test_run_continuation import _actions, _events, _goal
from tests.test_run_manager_rework_triage import _config, _state
from zf.core.events.model import ZfEvent
from zf.runtime.run_continuation import build_run_continuation_projection
from zf.runtime.run_manager import _pending_human_decisions, build_run_manager_projection


def scenario():
    identity = dict(workflow_run_id="RUN-1", task_id="TASK-A", operation_id="op",
                    active_attempt_id="attempt", lease_id="lease")
    request = ZfEvent(id="request", type="worker.assistance.requested", actor="impl-1",
        task_id="TASK-A", correlation_id="RUN-1", payload={**identity,
            "request_id": "assistance-1", "owner": "orchestrator",
            "owner_instance": "orchestrator", "apply_policy": "proposal_only"})
    reason = "assistance response deadline expired"
    closed = ZfEvent(id="closed", type="worker.assistance.closed", actor="run-manager",
        origin="kernel", task_id="TASK-A", correlation_id="RUN-1", causation_id=request.id,
        payload={**request.payload, "status": "waiting", "reason": reason})
    escalation = ZfEvent(id="escalation", type="human.escalate", actor="run-manager",
        origin="kernel", task_id="TASK-A", correlation_id="RUN-1", causation_id=request.id,
        payload={"request_id": "assistance-1", "reason": reason, "owner_route": "orchestrator",
                 "blocking_scope": "task", "task_id": "TASK-A",
                 "resume_condition": "current advisor response or authorized handoff"})
    envelope = {"ref": "artifacts/envelope.json", "sha256": "a" * 64,
                "source_event_id": "failure"}
    failure = ZfEvent(id="failure", type="dev.blocked", actor="impl-1", task_id="TASK-A",
        correlation_id="RUN-1", payload={**identity, "attempt_id": "attempt",
            "request_hash": "request-hash", "semantic_submit_admission_event_id": "admission",
            "call_result_envelope_ref": envelope})
    admission = ZfEvent(id="admission", type="workflow.call.result.admitted", actor="zf-cli",
        task_id="TASK-A", correlation_id="RUN-1", payload={
            "schema_version": "call-result-admission.v1", "admission_status": "admitted",
            "workflow_run_id": "RUN-1", "operation_id": "op", "request_hash": "request-hash",
            "source_event_id": failure.id, "semantic_verdict": "blocked", "envelope_ref": envelope})
    settled = ZfEvent(id="settled", type="workflow.operation.settled", actor="zf-cli",
        origin="kernel", task_id="TASK-A", correlation_id="RUN-1", payload={
            "workflow_run_id": "RUN-1", "operation_id": "op", "request_hash": "request-hash",
            "reason": "admitted_call_result", "admitted_call_result_ref": envelope})
    triage = ZfEvent(id="triage", type="task.rework.triage.completed", actor="zf-cli",
        origin="kernel", task_id="TASK-A", correlation_id="RUN-1", causation_id=failure.id,
        payload={"failed_event_id": failure.id, "recommended_action": "request_replan",
                 "retryable": False, "is_terminal": False})
    # Admission is appended before its source event in the actual CLI path.
    return [request, closed, escalation, admission, settled, failure, triage]


def test_admitted_handoff_retires_notice_and_restores_continuation():
    events = scenario()
    before = deepcopy(events)
    assert _pending_human_decisions(events[:3])
    assert _pending_human_decisions(events) == []
    projection = build_run_continuation_projection(
        _events() + events, goal=_goal(), pending_actions=[{**_actions()[0], "task_id": "TASK-A"}],
        completion_profile={"pending_human_decisions": _pending_human_decisions(events)})
    assert projection["next_operation"]["task_id"] == "TASK-A"
    assert _pending_human_decisions(list(events)) == []
    assert events == before  # No invented human acknowledgement or task completion.


def test_actual_rm_projection_preserves_owner_gate_but_retires_notice(tmp_path):
    state, log, writer = _state(tmp_path)
    for event in [*_events(), *scenario(), ZfEvent(
            id="owner", type="human.escalate", task_id="TASK-A", correlation_id="RUN-1",
            origin="kernel", payload={"decision_token": "owner-token", "checkpoint_id": "Q13",
                "blocking_scope": "task", "task_id": "TASK-A", "reason": "Owner decision"})]:
        writer.append(event)
    result = build_run_manager_projection(state, config=_config())
    pending = result["completion_profile"]["pending_human_decisions"]
    assert [row["event_id"] for row in pending] == ["owner"]
    assert not any(e.type == "human.escalation.acknowledged" for e in log.read_all())


@pytest.mark.parametrize("defect", [
    "no_closed", "no_failure", "no_admission", "no_settlement", "no_triage",
    "late_answer_only", "external_escalation", "external_closed", "external_settlement",
    "external_triage", "other_request", "other_run", "other_task", "other_operation",
    "other_attempt", "other_lease", "other_envelope", "other_source", "other_hash",
    "explicit_decision", "owner_approval", "other_reason", "terminal_triage", "human_triage",
])
def test_unproven_or_owner_handoff_keeps_gate(defect):
    events = scenario()
    request, closed, escalation, admission, settled, failure, triage = events
    if defect.startswith("no_"):
        names = {"closed": closed, "failure": failure, "admission": admission,
                 "settlement": settled, "triage": triage}
        events.remove(names[defect[3:]])
    elif defect == "late_answer_only":
        events = events[:3] + [ZfEvent(type="orchestrator.rework.triage.recorded",
            actor="orchestrator", task_id="TASK-A", payload={"request_id": "assistance-1",
                "recommended_action": "replan", "guidance": "new answer", "apply_policy": "proposal_only"})]
    elif defect.startswith("external_"):
        {"escalation": escalation, "closed": closed, "settlement": settled,
         "triage": triage}[defect[9:]].origin = "external"
    elif defect == "other_request":
        closed.payload["request_id"] = "other"
    elif defect == "other_run":
        triage.correlation_id = "OTHER"
    elif defect == "other_task":
        triage.task_id = "OTHER"
    elif defect == "other_operation":
        failure.payload["operation_id"] = "other"
    elif defect == "other_attempt":
        failure.payload["attempt_id"] = "other"
    elif defect == "other_lease":
        failure.payload["lease_id"] = "other"
    elif defect == "other_envelope":
        settled.payload["admitted_call_result_ref"] = {"ref": "other", "sha256": "b" * 64}
    elif defect == "other_source":
        admission.payload["source_event_id"] = "another-failure"
    elif defect == "other_hash":
        settled.payload["request_hash"] = "other"
    elif defect == "explicit_decision":
        escalation.payload["decision_token"] = "owner-token"
    elif defect == "owner_approval":
        escalation.payload["approval_command"] = "owner approve"
    elif defect == "other_reason":
        escalation.payload["reason"] = "owner decision required"
    elif defect == "terminal_triage":
        triage.payload["is_terminal"] = True
    elif defect == "human_triage":
        triage.payload["recommended_action"] = "human_escalate"
    assert [row["event_id"] for row in _pending_human_decisions(events)] == [escalation.id]
