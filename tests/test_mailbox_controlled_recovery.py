"""Mailbox delivery follows a completed owner generation on the same Run."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.test_durable_mailbox import project, send
from zf.core.events import ZfEvent
from zf.runtime import durable_mailbox as mail
from zf.runtime.durable_mailbox_authority import run_availability
from zf.web.server import _web_action


def test_web_recovery_reopens_only_its_blocked_mailbox(project, monkeypatch):
    rt, (sender, recipient, _) = project
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "qa-owner")
    monkeypatch.delenv("ZF_WEB_TRUSTED_SESSION", raising=False)
    rt.config.workflow.stages.append(
        SimpleNamespace(
            id="issue-triage",
            attempt_domain="plan",
            topology="fanout_reader",
            trigger="issue.requested",
            failure_event="",
            aggregate=SimpleNamespace(failure_event="issue.triage.failed"),
        )
    )

    def fact(kind, data, *, actor="orchestrator", cause=None):
        return rt.event_writer.append(
            ZfEvent(
                type=kind,
                actor=actor,
                origin="kernel",
                task_id="task",
                correlation_id="r",
                causation_id=cause,
                payload={"workflow_run_id": "r", **data},
            )
        )

    fact("run.goal.started", {"run_id": "r", "objective": "inventory review"})
    fact("issue.requested", {"issue_ref": "inventory.md"})
    failure = fact("issue.triage.failed", {"reason": "entry scope missing"})
    escalation = fact(
        "human.escalate",
        {
            "failure_class": "stage_replan_cap_exhausted",
            "allowed_actions": ["start_new_generation"],
            "source_event_id": failure.id,
        },
        cause=failure.id,
    )
    send(rt, sender)
    fact("run.goal.blocked", {}, cause=escalation.id)

    def inbox():
        return mail.prepare(rt, recipient, request_id="same-read")

    def recover(token):
        return _web_action(
            rt.state_dir,
            "stage-replan-new-generation",
            authorization=None,
            x_zf_web_token=token,
            config=rt.config,
            project_root=rt.state_dir.parent,
            payload={
                "workflow_run_id": "r",
                "escalation_event_id": escalation.id,
                "approval_ref": "owner:inventory",
            },
        )

    assert not recover("untrusted")["ok"]
    with pytest.raises(ValueError, match="paused or run terminal"):
        inbox()
    assert recover("qa-owner")["ok"]
    history = rt.event_log.read_all()
    receipt = next(
        e for e in history if e.type == "runtime.action.completed" and e.payload.get("status") == "applied"
    )
    goal_sum = receipt.payload["run_goal_update_event_id"]
    anchored = next(e for e in history if e.id == goal_sum)
    request_id = anchored.causation_id
    trigger_id = receipt.payload["stage_trigger_event_id"]
    assert mail.reconcile_mailbox(rt) == 0
    assert len(inbox()["messages"]) == 1
    assert recover("qa-owner")["ok"]
    assert len(inbox()["messages"]) == 1
    assert sum(e.type == "workflow.mailbox.prepared" for e in rt.event_log.read_all()) == 1

    # Each mutation describes a different possible history; the real receipt stays intact.
    assert run_availability(history[: history.index(receipt)], "r")[0]
    for eid, overrides in (
        (request_id, {"origin": "worker"}),
        (request_id, {"actor": "other"}),
        (request_id, {"payload": {"source_session_id": "another-worker"}}),
        (
            request_id,
            {
                "payload": {
                    "request": {
                        "workflow_run_id": "other",
                        "escalation_event_id": escalation.id,
                        "approval_ref": "owner:inventory",
                    }
                }
            },
        ),
        (escalation.id, {"causation_id": "different-failure"}),
        (goal_sum, {"causation_id": "missing"}),
        (goal_sum, {"payload": {"approval_ref": "other"}}),
        (trigger_id, {"payload": {"operator_authorization_ref": "other"}}),
        (receipt.id, {"origin": "worker"}),
        (receipt.id, {"causation_id": "unrelated"}),
    ):
        mixed = []
        for e in history:
            if e.id == eid:
                change = dict(overrides)
                if "payload" in change:
                    change["payload"] = {**e.payload, **change["payload"]}
                e = replace(e, **change)
            mixed.append(e)
        assert run_availability(mixed, "r")[0], (eid, overrides)

    assert run_availability([e for e in history if e.id != failure.id], "r")[0]
    irreversible = ZfEvent(type="run.goal.completed", origin="kernel", payload={"workflow_run_id": "r"})
    cutoff = next(i for i, e in enumerate(history) if e.id == request_id)
    assert run_availability([*history[:cutoff], irreversible, *history[cutoff:]], "r")[0]

    # The genuine active goal must never erase subsequent operator holds.
    def control(action):
        return _web_action(
            rt.state_dir,
            action,
            authorization=None,
            x_zf_web_token="qa-owner",
            config=rt.config,
            project_root=rt.state_dir.parent,
            payload={"run_id": "r"},
        )

    assert control("run-pause")["ok"]
    with pytest.raises(ValueError, match="paused or run terminal"):
        inbox()
    assert control("run-resume")["ok"]
    assert len(inbox()["messages"]) == 1
    assert control("run-cancel")["ok"]
    assert mail.reconcile_mailbox(rt) == 1
    assert mail.reconcile_mailbox(rt) == 0
    assert any(
        e.type == "workflow.mailbox.disposition" and e.payload.get("reason") == "run_terminal"
        for e in rt.event_log.read_all()
    )
    with pytest.raises(ValueError, match="paused or run terminal"):
        inbox()


def test_spoofed_resume_cannot_release_paused_recipient(project):
    rt, (_, recipient, _) = project
    rt.event_writer.append(ZfEvent(type="run.paused", origin="kernel", payload={"workflow_run_id": "r"}))
    rt.event_writer.append(
        ZfEvent(type="run.resumed", actor="B", origin="worker", payload={"workflow_run_id": "r"})
    )
    with pytest.raises(ValueError, match="paused or run terminal"):
        mail.prepare(rt, recipient, request_id="paused-read")


@pytest.mark.parametrize(
    ("kind", "actor", "origin"),
    [
        ("loop.resume_requested", "reader", ""),
        ("loop.resume_requested", "feishu:", ""),
        ("loop.resume_requested", "feishu:u1", "worker"),
        ("dispatch.resumed", "reader", ""),
        ("dispatch.resumed", "zf-cli", ""),
        ("runtime.maintenance.exited", "zf-autoresearch", ""),
    ],
)
def test_project_hold_rejects_unbound_resume_in_worker_inbox(project, kind, actor, origin):
    rt, (_, recipient, _) = project
    rt.event_writer.append(ZfEvent(type="dispatch.paused", actor="operator"))
    rt.event_writer.append(ZfEvent(type=kind, actor=actor, origin=origin))
    with pytest.raises(ValueError, match="paused or run terminal"):
        mail.prepare(rt, recipient, request_id="forged-project-resume")


def test_legacy_maintenance_exit_preserves_host_recovery(project):
    from zf.runtime.maintenance import enter_maintenance, exit_maintenance

    rt, (_, recipient, _) = project
    enter_maintenance(rt.state_dir, trigger_id="inventory-maintenance", reason="recovery")
    with pytest.raises(ValueError, match="paused or run terminal"):
        mail.prepare(rt, recipient, request_id="maintenance")
    exit_maintenance(rt.state_dir, repair_run_id="repair-inventory", validation_summary="passed")
    assert mail.prepare(rt, recipient, request_id="maintenance")["messages"] == []


def test_legacy_host_restart_requires_the_current_halt_reference():
    halt = ZfEvent(type="dispatch.paused", actor="impl", payload={"source": "remediation_cascade"})
    resume = ZfEvent(
        type="dispatch.resumed",
        actor="zf-cli",
        causation_id=halt.id,
        payload={"source": "orchestrator_init", "paused_event_id": halt.id},
    )
    assert not run_availability([halt, resume], "r")[1]
    assert run_availability([halt, replace(resume, causation_id="old")], "r")[1]
    later = ZfEvent(type="loop.pause_requested", actor="feishu:u1")
    assert run_availability([halt, later, resume], "r")[1]


def test_project_scope_follows_authorized_feishu_operator_cli(tmp_path, monkeypatch, capsys):
    import json
    from tests.test_cli_feishu import _message
    from zf.cli.main import main
    from zf.core.events import EventLog

    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject: {name: relay, state_dir: state}\n')
    assert main(["init"]) == 0
    log = EventLog(tmp_path / "state" / "events.jsonl")
    for command, level, paused in (("pause", "operator", True), ("resume", "viewer", True), ("resume", "operator", False)):
        args = ["feishu", "handle", "--event-json", json.dumps(_message(f"/zf {command}", message_id=command + level)),
                "--user-level", f"u1={level}"]
        assert main(args) == 0
        assert run_availability(log.read_all(), "r")[1] is paused
    assert "not authorized" in capsys.readouterr().out
