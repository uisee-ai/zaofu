"""Deterministic product E2E for the three Channel Group discussion modes."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from zf.core.config.loader import load_config
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.channel_adapter import dispatch_reply_request
from zf.runtime.channel_discussion import advance_discussion
from zf.runtime.channel_prd_evaluation_reactor import (
    react_channel_prd_evaluation_requested,
)
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_result_receipts import reconcile_channel_result_receipts
from zf.runtime.channel_synthesis_reactor import (
    react_channel_consensus_proposed,
    react_channel_synthesis_requested,
)
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.workflow_start import WorkflowStartService
from zf.web.plan_extraction import extract_plan_request


ROOT = Path(__file__).resolve().parents[2]
PLAN_REQUESTED_EVENT = "kanban.agent.plan.requested"

pytestmark = pytest.mark.mock_e2e


def _runtime(tmp_path: Path) -> tuple[Path, EventLog, EventWriter, ControlledActionService]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    for name in ("examples", "skills"):
        link = tmp_path / name
        if not link.exists():
            link.symlink_to(ROOT / name, target_is_directory=True)
    config_path = tmp_path / "zf.yaml"
    config_path.write_text(
        (ROOT / "zf.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    # Delivery workflow admission requires a committed project baseline.  The
    # isolated mock product project is otherwise an empty temporary directory.
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "channel-group@test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Channel Group E2E"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "test: initialize channel group fixture"], cwd=tmp_path, check=True)
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    service = ControlledActionService(
        state_dir,
        writer,
        config=load_config(config_path),
        actor="web",
        source="channel-group-mock-e2e",
        surface="web",
        project_root=tmp_path,
    )
    return state_dir, log, writer, service


def _execute(
    service: ControlledActionService,
    writer: EventWriter,
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    requested = writer.emit(
        "web.action.requested",
        actor=service.actor,
        payload={"action": action, "request": payload},
    )
    result = service.execute(
        action=action,
        requested_action=action,
        payload=payload,
        requested=requested,
    )
    assert result.get("ok"), result
    return result


def _create_group(
    service: ControlledActionService,
    writer: EventWriter,
    *,
    mode: str,
    members: tuple[tuple[str, str], ...],
    participants: list[str] | None = None,
    synthesizer: str = "",
) -> str:
    created = _execute(
        service,
        writer,
        "channel-create",
        {
            "channel_id": "channel-group-e2e",
            "name": "Channel Group E2E",
            "owner_actor_ref": "web",
        },
    )
    channel_id = str(created["channel_id"])
    for member_id, role in members:
        _execute(
            service,
            writer,
            "channel-invite-member",
            {
                "channel_id": channel_id,
                "member_id": member_id,
                "provider": "fake",
                "backend": "fake",
                "channel_role": role,
                "permission_profile": "read_only",
                "permissions": [
                    "read",
                    "message",
                    "summarize",
                    "propose_workflow",
                ],
            },
        )
    leader = members[0][0]
    _execute(
        service,
        writer,
        "channel-set-leader",
        {
            "channel_id": channel_id,
            "leader_member_id": leader,
            "expected_revision": 0,
            "idempotency_key": "channel-group-e2e-leader-r1",
        },
    )
    mode_payload: dict[str, Any] = {
        "channel_id": channel_id,
        "thread_id": "main",
        "mode": mode,
    }
    if participants is not None:
        mode_payload["participants"] = participants
    if synthesizer:
        mode_payload["synthesizer"] = synthesizer
    _execute(service, writer, "channel-discussion-mode", mode_payload)
    return channel_id


def _post(
    service: ControlledActionService,
    writer: EventWriter,
    channel_id: str,
    *,
    client_message_id: str,
    member_id: str,
    role: str,
    text: str,
    refs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _execute(
        service,
        writer,
        "channel-post-message",
        {
            "channel_id": channel_id,
            "thread_id": "main",
            "client_message_id": client_message_id,
            "member_id": member_id,
            "role": role,
            "text": text,
            "refs": {"dispatch_policy": "trigger_only", **(refs or {})},
        },
    )


def _drain_requests(
    state_dir: Path,
    writer: EventWriter,
    channel_id: str,
    *,
    request_ids: list[str] | None = None,
) -> None:
    channel = project_channel(state_dir, channel_id) or {}
    pending = [
        item
        for item in channel.get("reply_requests") or []
        if item.get("status") in {"pending", "queued"}
        and (request_ids is None or item.get("request_id") in request_ids)
    ]
    for item in pending:
        result = dispatch_reply_request(
            state_dir=state_dir,
            writer=writer,
            channel_id=channel_id,
            request_id=str(item["request_id"]),
            actor="mock-provider",
            source="channel-group-mock-e2e",
            project_root=state_dir.parent,
        )
        assert result.completed == [str(item["request_id"])], result


def _run_multilens_to_consensus(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, EventLog, EventWriter, ControlledActionService, str]:
    state_dir, log, writer, service = _runtime(tmp_path)
    channel_id = _create_group(
        service,
        writer,
        mode="multi_lens",
        members=(
            ("product-pm", "product_pm"),
            ("architect", "architect"),
            ("critic", "critic"),
        ),
        participants=["product-pm", "architect", "critic"],
        synthesizer="product-pm",
    )
    posted = _post(
        service,
        writer,
        channel_id,
        client_message_id="requirement",
        member_id="operator",
        role="user",
        text="我们需要一个可审计的 Channel Group 产品流程。@all",
    )
    assert posted["route"]["targets"] == [
        "product-pm",
        "architect",
        "critic",
    ]
    _drain_requests(state_dir, writer, channel_id)
    assert advance_discussion(
        state_dir, writer, channel_id=channel_id, thread_id="main", source="test"
    ) == ["channel.discussion.phase.changed"]
    assert advance_discussion(
        state_dir, writer, channel_id=channel_id, thread_id="main", source="test"
    ) == [
        "channel.synthesis.requested",
        "channel.discussion.phase.changed",
    ]

    # The reactor creates the runtime synthesis message and reply request. The
    # test drains it synchronously so no background provider thread races the
    # assertions below.
    monkeypatch.setattr(
        "zf.runtime.channel_message_ingress.dispatch_channel_replies_background",
        lambda **_: None,
    )
    host = SimpleNamespace(
        state_dir=state_dir,
        event_log=log,
        event_writer=writer,
        project_root=state_dir.parent,
        config=service.config,
        openclaw_client=None,
    )
    synthesis_event = next(
        event for event in log.read_all() if event.type == "channel.synthesis.requested"
    )
    react_channel_synthesis_requested(host, synthesis_event)
    channel = project_channel(state_dir, channel_id) or {}
    synthesis_request = next(
        item
        for item in channel.get("reply_requests") or []
        if item.get("message_id") == f"msg-{synthesis_event.payload['request_id']}"
    )
    _drain_requests(state_dir, writer, channel_id, request_ids=[synthesis_request["request_id"]])

    proposed = next(
        event for event in log.read_all() if event.type == "channel.consensus.proposed"
    )
    react_channel_consensus_proposed(host, proposed)
    channel = project_channel(state_dir, channel_id) or {}
    review_requests = [
        item
        for item in channel.get("reply_requests") or []
        if str(item.get("message_id") or "").startswith("msg-creview-")
    ]
    assert {item["target_member_id"] for item in review_requests} == {
        "architect",
        "critic",
    }
    _drain_requests(
        state_dir,
        writer,
        channel_id,
        request_ids=[str(item["request_id"]) for item in review_requests],
    )
    channel = project_channel(state_dir, channel_id) or {}
    consensus = channel["consensus"]["main"]
    confirmed = _execute(
        service,
        writer,
        "channel-consensus-confirm",
        {
            "channel_id": channel_id,
            "thread_id": "main",
            "artifact_ref": consensus["artifact_ref"],
            "artifact_digest": consensus["artifact_digest"],
            "prd_revision": consensus["prd_revision"],
            "idempotency_key": "channel-group-e2e-confirm-r1",
        },
    )
    assert confirmed["status"] == "confirmed"
    assert advance_discussion(
        state_dir, writer, channel_id=channel_id, thread_id="main", source="test"
    ) == ["channel.consensus.reached", "channel.discussion.closed"]
    return state_dir, log, writer, service, channel_id


def test_conversation_mode_uses_default_responder_without_fanout(tmp_path: Path) -> None:
    state_dir, log, writer, service = _runtime(tmp_path)
    channel_id = _create_group(
        service,
        writer,
        mode="conversation",
        members=(("product-pm", "product_pm"),),
    )
    _execute(
        service,
        writer,
        "channel-discussion-mode",
        {
            "channel_id": channel_id,
            "thread_id": "main",
            "mode": "conversation",
            "default_responder_id": "product-pm",
        },
    )
    posted = _post(
        service,
        writer,
        channel_id,
        client_message_id="conversation-requirement",
        member_id="operator",
        role="user",
        text="先给我一个产品需求澄清答复。",
    )
    assert posted["route"]["targets"] == ["product-pm"]
    assert not any(event.type == "channel.discussion.started" for event in log.read_all())
    route = next(event for event in log.read_all() if event.type == "channel.route.defaulted")
    assert route.payload["routing_reason"] == "default_responder"
    assert (project_channel(state_dir, channel_id) or {})["discussion"]["mode"] == "conversation"


def test_clarification_mode_relays_mentions_and_suppresses_bare_ack(tmp_path: Path) -> None:
    state_dir, log, writer, service = _runtime(tmp_path)
    channel_id = _create_group(
        service,
        writer,
        mode="clarification",
        members=(("pm", "product_pm"), ("architect", "architect")),
    )
    first = _post(
        service,
        writer,
        channel_id,
        client_message_id="clarification-root",
        member_id="operator",
        role="user",
        text="@pm 请先澄清需求边界。",
    )
    root_request = (project_channel(state_dir, channel_id) or {})["reply_requests"][0]
    assert first["route"]["targets"] == ["pm"]
    relay = _post(
        service,
        writer,
        channel_id,
        client_message_id="clarification-relay-1",
        member_id="pm",
        role="assistant",
        text="@architect 请核实这个边界。",
        refs={"request_id": root_request["request_id"]},
    )
    assert relay["route"]["targets"] == ["architect"]
    relay_request = next(
        item
        for item in (project_channel(state_dir, channel_id) or {})["reply_requests"]
        if item["message_id"] == "msg-clarification-relay-1"
    )
    suppressed = _post(
        service,
        writer,
        channel_id,
        client_message_id="clarification-relay-2",
        member_id="pm",
        role="assistant",
        text="@architect 好的。",
        refs={"request_id": relay_request["request_id"]},
    )
    assert suppressed["route"]["targets"] == []
    assert suppressed["route"]["skipped"] == [{
        "reason": "bare_ack",
    }]
    event = next(
        event
        for event in reversed(log.read_all())
        if event.type == "channel.relay.suppressed"
    )
    assert event.payload["reason"] == "bare_ack"


def test_multi_lens_runs_blind_relay_synthesis_and_consensus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_dir, log, _writer, _service, channel_id = _run_multilens_to_consensus(
        tmp_path, monkeypatch
    )
    channel = project_channel(state_dir, channel_id) or {}
    discussion = channel["discussions"]["main"]
    assert discussion["product_mode"] == "multi_lens"
    assert discussion["state"] == "idle"
    assert channel["syntheses"][-1]["artifact_ref"].endswith("/prd/r1.json")
    assert channel["consensus"]["main"]["human_confirmed"] is True
    event_types = [event.type for event in log.read_all()]
    assert "channel.discussion.started" in event_types
    assert "channel.synthesis.requested" in event_types
    assert "channel.synthesis.proposed" in event_types
    assert "channel.consensus.reached" in event_types
    assert "channel.discussion.closed" in event_types


def _task_plan_request(
    authority: dict[str, Any],
    *,
    workflow_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task_payload: dict[str, Any] = {
        "title": "Deliver the confirmed Channel PRD",
        "objective": "Implement the exact confirmed Channel PRD.",
        "acceptance": "All canonical PRD checks pass.",
        "priority": 2,
    }
    if workflow_plan is not None:
        task_payload["workflow_plan"] = workflow_plan
    request = extract_plan_request(
        json.dumps(
            {
                "plan_request": {
                    "subject_type": "task_create",
                    "header": "Create Channel PRD Task",
                    "question": "How should the confirmed PRD become work?",
                    "options": [
                        {
                            "id": "create",
                            "label": "Create the PRD Task (Recommended)",
                            "recommended": True,
                            "description": "Create one exact PRD-bound Task.",
                            "effect": {
                                "mode": "propose",
                                "action": "create-task",
                                "payload": task_payload,
                            },
                        },
                        {
                            "id": "continue",
                            "label": "Continue discussion",
                            "description": "Keep the result in the Channel.",
                            "effect": {"mode": "continue"},
                        },
                    ],
                    "allow_other": False,
                }
            }
        ),
        plan_context={"workflow_parameters": authority},
    )
    assert request is not None and request["valid"] is True, request
    return request


def _mock_evaluation_output(
    *,
    state_dir: Path,
    writer: EventWriter,
    item: Any,
    request_id: str,
    causation_id: str,
    **_: Any,
) -> dict[str, Any]:
    manifest = item.manifest()
    base = f"artifacts/output-evaluations/{request_id}"
    manifest_ref = write_sidecar_json(
        state_dir,
        f"{base}/input-manifest.json",
        manifest,
        kind="evaluation_input_manifest",
        schema_version="output-evaluation.v1",
        created_by="mock-evaluator",
        source_event_id=causation_id,
        required=True,
    )
    report_ref = write_sidecar_json(
        state_dir,
        f"{base}/report.json",
        {
            "schema_version": "output-evaluation.v1",
            "request_id": request_id,
            "manifest_ref": manifest_ref["ref"],
            "manifest_digest": manifest_ref["sha256"],
            "verdict": "PASS",
            "findings": [],
            "matrix": [],
            "coverage": {"checked": ["canonical PRD"], "unread": []},
            "product_verification": "not_executed",
        },
        kind="evaluation_report",
        schema_version="output-evaluation.v1",
        created_by="mock-evaluator",
        source_event_id=causation_id,
        required=True,
    )
    started = writer.emit(
        "output.evaluation.started",
        actor="mock-evaluator",
        causation_id=causation_id,
        correlation_id=str((item.context or {}).get("channel_id") or request_id),
        payload={"request_id": request_id, "manifest": manifest},
    )
    completed = writer.emit(
        "output.evaluation.completed",
        actor="mock-evaluator",
        causation_id=started.id,
        correlation_id=str((item.context or {}).get("channel_id") or request_id),
        payload={
            "request_id": request_id,
            "report_ref": report_ref["ref"],
            "report_digest": report_ref["sha256"],
            "verdict": "PASS",
        },
    )
    return {
        "ok": True,
        "request_id": request_id,
        "report_ref": report_ref["ref"],
        "report_digest": report_ref["sha256"],
        "event_id": completed.id,
    }


def test_channel_prd_evaluation_task_workflow_run_and_receipt_continuation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This product-flow fixture simulates the workflow workers. Its generated
    # config may still name real providers in auxiliary roles; do not make the
    # simulated flow depend on the host user's CLI login state.
    monkeypatch.setattr(
        "zf.runtime.preflight._probe_provider_auth",
        lambda backend: (True, f"simulated authentication for {backend}"),
    )
    state_dir, log, writer, service, channel_id = _run_multilens_to_consensus(
        tmp_path, monkeypatch
    )
    consensus = (project_channel(state_dir, channel_id) or {})["consensus"]["main"]
    evaluation = _execute(
        service,
        writer,
        "channel-prd-evaluate",
        {
            "channel_id": channel_id,
            "thread_id": "main",
            "artifact_ref": consensus["artifact_ref"],
            "artifact_digest": consensus["artifact_digest"],
            "prd_revision": consensus["prd_revision"],
            "role": "full",
        },
    )
    monkeypatch.setattr(
        "zf.runtime.channel_prd_evaluation_reactor.evaluate_output",
        _mock_evaluation_output,
    )
    host = SimpleNamespace(
        state_dir=state_dir,
        event_log=log,
        event_writer=writer,
        project_root=state_dir.parent,
        config=service.config,
    )
    evaluation_event = next(
        event
        for event in log.read_all()
        if event.type == "channel.prd_evaluation.requested"
        and event.payload.get("request_id") == evaluation["request_id"]
    )
    react_channel_prd_evaluation_requested(host, evaluation_event)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if any(
            event.type == "output.evaluation.completed"
            and event.payload.get("request_id") == evaluation["request_id"]
            for event in log.read_all()
        ):
            break
        time.sleep(0.02)
    assert any(
        event.type == "output.evaluation.completed"
        and event.payload.get("request_id") == evaluation["request_id"]
        for event in log.read_all()
    )
    feedback_deadline = time.monotonic() + 5
    while time.monotonic() < feedback_deadline:
        if any(
            event.type == "channel.prd_evaluation.feedback.skipped"
            and event.payload.get("reason") == "owner_confirmed_or_closed"
            for event in log.read_all()
        ):
            break
        time.sleep(0.02)
    assert any(
        event.type == "channel.prd_evaluation.feedback.skipped"
        and event.payload.get("reason") == "owner_confirmed_or_closed"
        for event in log.read_all()
    )

    channel = project_channel(state_dir, channel_id) or {}
    authority = {
        "channel_id": channel_id,
        "thread_id": "main",
        "channel_member_id": "product-pm",
        "leader_revision": int(channel["leader_revision"]),
        "prd_revision": int(consensus["prd_revision"]),
        "source_ref": str(consensus["prd_ref"]),
        "source_digest": str(consensus["prd_digest"]),
    }
    workflow_plan = {
        "header": "Choose delivery workflow",
        "question_id": "channel-group-delivery",
        "question": "Which approved workflow should run?",
        "options": [
            {
                "id": "delivery",
                "label": "PRD delivery (Recommended)",
                "recommended": True,
                "route_id": "delivery:prd:standard",
                "objective": "Deliver the confirmed Channel PRD.",
                "parameters": {"target_root": "."},
            },
            {
                "id": "defer",
                "label": "Do not start yet",
                "mode": "defer",
            },
        ],
        "allow_other": False,
    }
    plan = _task_plan_request(authority, workflow_plan=workflow_plan)
    requested_plan = ZfEvent(type=PLAN_REQUESTED_EVENT, actor="kanban-agent")
    plan["request_event_id"] = requested_plan.id
    requested_plan.payload = {"request": plan, "plan_request": plan}
    writer.append(requested_plan)
    proposed = _execute(
        service,
        writer,
        "kanban-plan-apply",
        {
            "plan_response": {
                "request_event_id": requested_plan.id,
                "request_id": plan["request_id"],
                "revision": plan["revision"],
                "question_id": plan["question_id"],
                "option_id": "create",
                "answer": "Create the PRD Task (Recommended)",
            }
        },
    )
    assert proposed["status"] == "proposal_ready"
    create_proposal_event = next(
        event
        for event in writer.event_log.read_all()
        if event.type == "operator.action.proposed"
        and event.payload["proposal"]["action"] == "create-task"
    )
    created = _execute(
        service,
        writer,
        "create-task",
        {
            **create_proposal_event.payload["proposal"]["payload"],
            "proposal_event_id": create_proposal_event.id,
        },
    )
    assert created["ok"] and created["task_id"]
    task_event = next(
        event
        for event in writer.event_log.read_all()
        if event.type == "task.created" and event.task_id == created["task_id"]
    )
    assert task_event.payload["request"]["channel_authority"]["channel_id"] == channel_id
    workflow_plan_event = next(
        event
        for event in writer.event_log.read_all()
        if event.type == PLAN_REQUESTED_EVENT and event.causation_id == task_event.id
    )
    workflow_request = workflow_plan_event.payload["request"]
    workflow_proposed = _execute(
        service,
        writer,
        "kanban-plan-apply",
        {
            "plan_response": {
                "request_event_id": workflow_plan_event.id,
                "request_id": workflow_request["request_id"],
                "revision": workflow_request["revision"],
                "question_id": workflow_request["question_id"],
                "option_id": "delivery",
                "answer": "PRD delivery (Recommended)",
            }
        },
    )
    assert workflow_proposed["status"] == "proposal_ready"
    task = TaskStore(state_dir / "kanban.json").get(created["task_id"])
    assert task is not None
    direct_workflow_proposal = WorkflowStartService(
        state_dir,
        service.config,
        project_root=state_dir.parent,
    ).propose(
        writer,
        {
            "task_id": task.id,
            "route_id": "delivery:prd:standard",
            "objective": task.contract.behavior,
            "parameters": {
                "backend": "mock",
                "target_root": str(state_dir.parent),
                "authorize_channel_execution": True,
            },
        },
        actor="web",
        origin="web",
    )
    assert direct_workflow_proposal["ok"], direct_workflow_proposal
    workflow_proposal_event = next(
        event
        for event in reversed(writer.event_log.read_all())
        if event.id == direct_workflow_proposal["proposal_event_id"]
    )
    workflow_payload = {
        **workflow_proposal_event.payload["proposal"]["payload"],
        "proposal_event_id": workflow_proposal_event.id,
    }
    started = _execute(service, writer, "workflow-start", workflow_payload)
    assert started["ok"]
    invoke = next(
        event for event in writer.event_log.read_all() if event.type == "workflow.invoke.requested"
    )
    run_id = str(invoke.payload.get("workflow_run_id") or invoke.payload.get("run_id") or "run-channel-group")
    writer.emit(
        "workflow.invoke.accepted",
        actor="mock-orchestrator",
        task_id=created["task_id"],
        correlation_id=run_id,
        payload={"request_id": invoke.payload.get("request_id"), "workflow_run_id": run_id, "task_id": created["task_id"]},
    )
    writer.emit(
        "run.started",
        actor="mock-orchestrator",
        task_id=created["task_id"],
        correlation_id=run_id,
        payload={"run_id": run_id, "workflow_run_id": run_id, "task_id": created["task_id"]},
    )
    terminal = writer.emit(
        "run.goal.completed",
        actor="mock-orchestrator",
        task_id=created["task_id"],
        correlation_id=run_id,
        payload={
            "run_id": run_id,
            "workflow_run_id": run_id,
            "task_id": created["task_id"],
            "summary": "Deterministic Channel Group workflow completed.",
            "artifact_ref": "artifacts/channel-group/run-result.json",
            "artifact_digest": hashlib.sha256(b"channel-group-result").hexdigest(),
        },
    )
    receipt_result = reconcile_channel_result_receipts(
        state_dir=state_dir,
        event_log=log,
        writer=writer,
    )
    assert receipt_result.recorded >= 2
    receipts = (project_channel(state_dir, channel_id) or {}).get("result_receipts") or []
    assert {item["receipt_kind"] for item in receipts} >= {
        "prd_confirmed",
        "task_created",
        "workflow_terminal",
    }
    assert any(
        event.type == "channel.result.receipt.recorded"
        and event.payload.get("source_event_id") == terminal.id
        for event in writer.event_log.read_all()
    )
