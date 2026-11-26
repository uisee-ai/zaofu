"""A stopped peer claimant is a diagnostic obligation, with no product verdict."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tests.test_durable_mailbox import project, send
from zf.core.events import ZfEvent
from zf.runtime import durable_mailbox as mail
from zf.runtime.failure_diagnosis_routing import pending_failure_diagnoses
from zf.runtime.stream_turn_lifecycle import run_stream_turn
from zf.runtime.run_manager import build_run_manager_projection
from zf.runtime.stage_team_mailbox import mailbox_index, member_mailbox


@pytest.mark.parametrize(
    "mode",
    [
        "ended",
        "wait",
        "answered",
        "paused",
        "cross-session",
        "other-attempt",
        "worker-fact",
    ],
)
def test_peer_claim_has_one_bound_diagnosis_after_real_stream_exit(project, mode):
    rt, (sender, receiver, other) = project
    message = send(rt, sender, kind="question", visibility="operator_metadata")
    key = message["message_key"]
    raw = {
        "operation_id": "op-B",
        "attempt_id": "B",
        "instance_id": "B",
        "task_id": "task",
        "run_id": "r",
        "dispatch_id": "d",
        "lease_id": "l",
        "task_pipeline_stage": "verify",
    }
    ctx = SimpleNamespace(to_payload=lambda: raw)
    claim = None

    def receive():
        nonlocal claim
        batch = mail.prepare(rt, receiver, request_id="pull")
        did = batch["delivery_id"]
        mail.acknowledge(rt, receiver, delivery_id=did)
        claim = mail.question_action(
            rt,
            receiver,
            message_id=key,
            action="claim",
            request_id="take",
            delivery_id=did,
        )
        if mode in {"wait", "answered"}:
            mail.question_action(
                rt,
                receiver,
                message_id=key,
                action="wait" if mode == "wait" else "answer",
                request_id="next",
                text="Bound decision",
                delivery_id=did,
                deadline=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
                if mode == "wait"
                else "",
            )
        if mode == "paused":
            rt.event_writer.append(
                ZfEvent(
                    type="run.paused",
                    origin="kernel",
                    correlation_id="r",
                    payload={"workflow_run_id": "r", "run_id": "r"},
                )
            )
        assert mail.reconcile_mailbox(rt) == 0
        return [
            SimpleNamespace(
                subtype="success", is_error=False, num_turns=1, session_id="s"
            )
        ], "ok"

    if mode in {"cross-session", "other-attempt", "worker-fact"}:
        fields = {
            **raw,
            "workflow_run_id": "r",
            "session_id": "s",
            "turn_id": "turn-1",
            "transport": "stream-json",
            "backend": "claude-code",
        }
        rt.event_writer.append(
            ZfEvent(
                type="provider.turn.opened", origin="kernel", actor="B", payload=fields
            )
        )
        receive()
        patch = (
            {"session_id": "other"}
            if mode == "cross-session"
            else {"attempt_id": "C"}
            if mode == "other-attempt"
            else {}
        )
        rt.event_writer.append(
            ZfEvent(
                type="provider.turn.closed",
                origin="worker" if mode == "worker-fact" else "kernel",
                actor="B",
                payload={**fields, **patch, "provider_status": "completed"},
            )
        )
    else:
        run_stream_turn(rt.state_dir, ctx, "s", receive)
    assert mail.reconcile_mailbox(rt) == (1 if mode == "ended" else 0)
    assert mail.reconcile_mailbox(rt) == 0
    es = rt.event_log.read_all()
    actions = pending_failure_diagnoses(rt.state_dir, es, rt.config)
    if mode != "ended":
        assert not actions
        return
    (disposition,) = [e for e in es if e.type == "workflow.mailbox.disposition"]
    (close,) = [e for e in es if e.type == "provider.turn.closed"]
    assert disposition.causation_id == claim["event_id"]
    assert {message["event_id"], claim["event_id"], close.id, disposition.id} <= set(
        actions[0]["source_event_ids"]
    )
    assert (
        actions[0]["owner_route"] == "run_manager"
        and actions[0]["action"] == "diagnose-attention"
    )
    projection = build_run_manager_projection(rt.state_dir, events=es, config=rt.config)
    (action,) = [
        a
        for a in projection["pending_actions"]
        if disposition.id in a.get("source_event_ids", [])
    ]
    assert action["action"] == "diagnose-attention"
    (row,) = mail.project_mailbox(es, run_id="r")["items"]
    assert (row["question_status"], row["status"]) == ("claimed", "diagnosis_required")
    member = {
        "stage": "verify",
        "obligation_id": "review",
        "role": "verify",
        "history": [{"operation_id": "op-B", "attempt_id": "B"}],
    }
    state = member_mailbox(member, mailbox_index(es, "r"), "", "")
    assert state["recovery_owner"] == "run_manager" and state["unknown"] == 0
    assert not any(
        e.type
        in {
            "task.done",
            "dev.failed",
            "workflow.operation.failed",
            "workflow.operation.settled",
            "human.escalate",
        }
        for e in es
    )


def test_settled_claimant_retains_diagnosis_without_new_business_failure(project):
    rt, (sender, receiver, other) = project
    original = send(rt, sender, kind="question")
    did = mail.prepare(rt, receiver, request_id="pull")["delivery_id"]
    mail.acknowledge(rt, receiver, delivery_id=did)
    claim = mail.question_action(
        rt,
        receiver,
        message_id=original["message_key"],
        action="claim",
        request_id="claim",
        delivery_id=did,
    )
    from zf.runtime.workflow_operation import WorkflowOperationService

    service = WorkflowOperationService(
        state_dir=rt.state_dir, event_log=rt.event_log, event_writer=rt.event_writer
    )
    service.fail(
        workflow_run_id="r",
        operation_id="op-B",
        request_hash="hash",
        task_id="task",
        reason="closed by transport",
    )
    assert mail.reconcile_mailbox(rt) == 1 and mail.reconcile_mailbox(rt) == 0
    es = rt.event_log.read_all()
    (fact,) = [e for e in es if e.type == "workflow.mailbox.disposition"]
    assert (
        fact.payload["status"] == "diagnosis_required"
        and fact.causation_id == claim["event_id"]
    )
    assert len(pending_failure_diagnoses(rt.state_dir, es, rt.config)) == 1
    mail.question_action(
        rt,
        sender,
        message_id=original["message_key"],
        action="cancel",
        request_id="withdraw",
    )
    assert not pending_failure_diagnoses(
        rt.state_dir, rt.event_log.read_all(), rt.config
    )


@pytest.mark.parametrize("second_session,expected", [("", True), ("second", False)])
def test_scoped_codex_claim_requires_one_native_turn(project, second_session, expected):
    rt, (sender, receiver, _) = project
    question = send(rt, sender, kind="question")
    scope = {
        "workflow_run_id": "r",
        "operation_id": "op-B",
        "attempt_id": "B",
        "session_id": "first",
        "turn_id": "turn-1",
        "dispatch_id": "d",
        "lease_id": "l",
    }

    def hook(name, **fields):
        return rt.event_writer.append(
            ZfEvent(
                type="codex.hook." + name,
                actor="B",
                origin="kernel",
                payload={**scope, **fields},
            )
        )

    hook("user_prompt_submit")
    if second_session:
        hook("user_prompt_submit", session_id=second_session, turn_id="turn-2")
    batch = mail.prepare(rt, receiver, request_id="get")["delivery_id"]
    mail.acknowledge(rt, receiver, delivery_id=batch)
    claim = mail.question_action(
        rt,
        receiver,
        message_id=question["message_key"],
        action="claim",
        request_id="take",
        delivery_id=batch,
    )
    hook("stop", attempt_id="different")
    assert mail.reconcile_mailbox(rt) == 0
    hook("stop")
    assert mail.reconcile_mailbox(rt) == int(expected)
    receipt = next(e for e in rt.event_log.read_all() if e.id == claim["event_id"])
    assert bool(receipt.payload.get("provider_turn")) == expected
