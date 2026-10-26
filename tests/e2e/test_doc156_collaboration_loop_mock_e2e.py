from __future__ import annotations

from tests.channel_wait import wait_for_channel

import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

from zf.core.config.loader import load_config
from zf.core.config.schema import RoleConfig, WorkflowStageConfig
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.channel_discussion import advance_discussion
from zf.runtime.channel_discussion import discussion_state
from zf.runtime.channel_discussion_contribution import valid_phase1_members
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_question_dedup import (
    apply_question_dedup_reply,
)
from zf.runtime.channel_synthesis_reactor import (
    react_channel_consensus_proposed,
    react_channel_cross_review_requested,
)
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_reactor import EventReactorMixin
from zf.runtime.supervisor_plan_integrity import build_plan_integrity_projection
from zf.runtime.workflow_anchor import workflow_task_request_binding
from zf.runtime.workflow_requests import load_workflow_request
from tests.e2e.scripts import (
    doc156_fake_kanban_agent,
    doc156_research_finisher,
)


RESEARCH_CONFIG = (
    Path(__file__).parents[1] / "fixtures" / "research_fanout.yaml"
)


class _RecordingTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Path, str, object]] = []

    def send_task(
        self,
        role_name,
        briefing_path,
        prompt,
        *,
        context=None,
    ):  # noqa: ANN001
        self.sent.append((role_name, briefing_path, prompt, context))

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


def _approved_action(
    service: ControlledActionService,
    writer: EventWriter,
    action: str,
    payload: dict,
) -> dict:
    proposal = writer.emit(
        "operator.action.proposed",
        actor="web",
        task_id=str(payload.get("task_id") or "") or None,
        payload={
            "source": "web",
            "proposal": {
                "proposal_id": f"proposal-{action}-{time.time_ns()}",
                "proposal_digest": hashlib.sha256(
                    json.dumps(
                        {"action": action, "payload": payload},
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest(),
                "revision": 1,
                "action": action,
                "requested_action": action,
                "reason": "doc156 deterministic mock",
                "valid": True,
                "validation_error": "",
                "payload": payload,
            },
        },
    )
    requested = writer.emit(
        "web.action.requested",
        actor="operator",
        task_id=str(payload.get("task_id") or "") or None,
        causation_id=proposal.id,
        payload={
            "action": action,
            "request": payload,
            "proposal_event_id": proposal.id,
        },
    )
    return service.execute(
        action=action,
        requested_action=action,
        payload={**payload, "proposal_event_id": proposal.id},
        requested=requested,
    )


def test_doc156_browser_helpers_follow_request_first_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (tmp_path / "zf.yaml").write_text(
        RESEARCH_CONFIG.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    TaskStore(state_dir / "kanban.json").add(
        Task(
            id="TASK-DOC156-HELPER",
            title="Doc 156 helper contract",
        )
    )
    args = SimpleNamespace(
        project_root=tmp_path,
        state_dir=state_dir,
        task_id="TASK-DOC156-HELPER",
        channel_id="ch-doc156-helper",
        request_id="REQ-DOC156-HELPER",
    )

    assert doc156_research_finisher._prepare_request(args) == 0
    request = load_workflow_request(
        state_dir,
        "REQ-DOC156-HELPER",
    )
    task = TaskStore(state_dir / "kanban.json").get(
        "TASK-DOC156-HELPER"
    )
    assert task is not None
    assert task.contract.source_ref == (
        "docs/intake/REQ-DOC156-HELPER.md"
    )
    source_path = tmp_path / task.contract.source_ref
    assert source_path.is_file()
    assert "Use Doc 156 research evidence before delivery starts." in (
        source_path.read_text(encoding="utf-8")
    )
    contract_update = [
        event
        for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == "task.contract.update"
        and event.task_id == task.id
    ][-1]
    assert contract_update.payload["source_ref"] == task.contract.source_ref
    assert (
        contract_update.payload["contract"]["source_ref"]
        == task.contract.source_ref
    )
    integrity = build_plan_integrity_projection(
        state_dir,
        project_root=tmp_path,
    )
    assert integrity["summary"]["missing_plan_refs"] == 0
    assert workflow_task_request_binding(task) == {
        "request_id": "REQ-DOC156-HELPER",
        "request_revision": int(request["revision"]),
        "origin_binding_digest": task.contract.evidence_contract[
            "workflow_origin_binding_digest"
        ],
    }

    monkeypatch.setenv("ZF_DOC156_TASK_ID", task.id)
    monkeypatch.setenv("ZF_DOC156_CHANNEL_ID", "ch-doc156-helper")
    monkeypatch.setenv("ZF_DOC156_REQUEST_ID", "REQ-DOC156-HELPER")
    workflow = doc156_fake_kanban_agent._proposal(
        "DOC156_WORKFLOW_PLAN"
    )
    assert workflow["payload"]["request_revision"] == 1
    adoption = doc156_fake_kanban_agent._proposal(
        'DOC156_ADOPT {"result_event_id":"evt-result","request_id":'
        '"REQ-DOC156-HELPER","request_revision":1}'
    )
    assert adoption["payload"]["result_event_id"] == "evt-result"


def test_doc156_channel_research_adoption_and_workflow_start(
    tmp_path: Path,
) -> None:
    config = load_config(RESEARCH_CONFIG)
    config.roles.append(
        RoleConfig(
            name="delivery_worker",
            backend="mock",
            role_kind="writer",
        )
    )
    config.workflow.stages.append(
        WorkflowStageConfig(
            id="delivery-smoke",
            trigger="workflow.invoke.requested",
            topology="fanout_reader",
            roles=["delivery_worker"],
        )
    )
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (tmp_path / "zf.yaml").write_text(
        RESEARCH_CONFIG.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    service = ControlledActionService(
        state_dir,
        writer,
        config=config,
        project_root=tmp_path,
        actor="operator",
        source="kanban-agent",
        surface="web",
    )

    channel = _approved_action(
        service,
        writer,
        "channel-create-from-template",
        {
            "template_id": "quick-change",
            "channel_id": "doc156-review",
            "overrides": {"backend": "fake"},
        },
    )
    assert channel["status"] == "created"
    channel_id = str(channel["channel_id"])
    discussion = _approved_action(
        service,
        writer,
        "channel-discussion-start",
        {
            "channel_id": channel_id,
            "thread_id": "main",
            "objective": "Assess delivery constraints before research.",
        },
    )
    assert discussion["status"] == "started"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        replies = [
            event
            for event in log.read_all()
            if event.type == "channel.agent.reply.completed"
            and event.payload.get("thread_id") == "main"
        ]
        if len(replies) >= 1:
            break
        time.sleep(0.02)
    assert len(replies) == 1

    requested_synthesis = _approved_action(
        service,
        writer,
        "channel-synthesis-request",
        {
            "channel_id": channel_id,
            "thread_id": "main",
            "target_member_id": "tech_leader",
            "reason": "Finalize the explicit discussion into a PRD draft.",
        },
    )
    assert requested_synthesis["status"] == "requested"

    synthesis_request = None
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        synthesis_request = next(
            (
                event
                for event in reversed(log.read_all())
                if event.type == "channel.synthesis.requested"
                and event.payload.get("channel_id") == channel_id
            ),
            None,
        )
        if synthesis_request is not None:
            break
        time.sleep(0.02)
    assert synthesis_request is not None
    deadline = time.monotonic() + 15
    synthesis_proposed = False
    while time.monotonic() < deadline:
        synthesis_proposed = any(
            event.type == "channel.synthesis.proposed"
            and event.payload.get("request_id")
            == synthesis_request.payload["request_id"]
            for event in log.read_all()
        )
        if synthesis_proposed:
            break
        time.sleep(0.02)
    assert synthesis_proposed

    request = _approved_action(
        service,
        writer,
        "workflow-request",
        {
            "request_id": "REQ-DOC156",
            "kind": "workflow",
            "objective": "Collect evidence before the delivery decision.",
            "backend": "mock",
            "pattern_id": "research-fanout",
            "allow_missing_env": True,
            "channel_id": channel_id,
            "thread_id": "main",
        },
    )
    assert request["ok"] is True
    request_revision = int(request["request_revision"])
    origin_binding = dict(request["origin_binding"])
    created = _approved_action(
        service,
        writer,
        "create-task",
        {
            "task_id": "TASK-DOC156",
            "title": "Doc 156 collaboration loop",
            "execution_mode": "workflow",
            "request_id": "REQ-DOC156",
            "request_revision": request_revision,
        },
    )
    assert created["ok"] is True
    task = TaskStore(state_dir / "kanban.json").get("TASK-DOC156")
    assert task is not None
    assert (
        task.contract.evidence_contract["execution_owner"]
        == "workflow"
    )
    research = _approved_action(
        service,
        writer,
        "research-start",
        {
            "task_id": "TASK-DOC156",
            "topic": "Collect evidence for the delivery decision.",
            "request_id": "REQ-DOC156",
            "request_revision": request_revision,
            "channel_id": channel_id,
            "thread_id": "main",
            "dispatch_id": "dispatch-doc156",
        },
    )
    assert research["status"] == "requested"
    invoke = next(
        event
        for event in reversed(log.read_all())
        if event.id == research["event_id"]
        and event.type == "workflow.invoke.requested"
    )

    transport = _RecordingTransport()
    orchestrator = Orchestrator(
        state_dir,
        config,
        transport,  # type: ignore[arg-type]
    )
    orchestrator.run_once(events=[invoke])
    fanout = next(
        event
        for event in log.read_all()
        if event.type == "fanout.started"
        and event.payload.get("stage_id") == "research-fanout"
    )
    child_dispatches = [
        event
        for event in log.read_all()
        if event.type == "fanout.child.dispatched"
        and event.payload.get("fanout_id") == fanout.payload["fanout_id"]
    ]
    assert len(child_dispatches) == 4
    orchestrator.run_once(events=[
        ZfEvent(
            type="research.child.completed",
            actor=str(event.payload["role_instance"]),
            task_id="TASK-DOC156",
            correlation_id=channel_id,
            payload={
                "fanout_id": fanout.payload["fanout_id"],
                "stage_id": "research-fanout",
                "child_id": event.payload["child_id"],
                "run_id": event.payload["run_id"],
                "role_instance": event.payload["role_instance"],
                "status": "completed",
                "report": {
                    "summary": f"{event.payload['child_id']} evidence",
                    "evidence_refs": ["source:mock"],
                },
            },
        )
        for event in child_dispatches
    ])
    synth_dispatch = next(
        event
        for event in reversed(log.read_all())
        if event.type == "fanout.synth.dispatched"
        and event.payload.get("fanout_id") == fanout.payload["fanout_id"]
    )
    orchestrator.run_once(events=[ZfEvent(
        type="fanout.synth.completed",
        actor="synthesizer",
        task_id="TASK-DOC156",
        correlation_id=channel_id,
        payload={
            "fanout_id": fanout.payload["fanout_id"],
            "stage_id": "research-fanout",
            "run_id": synth_dispatch.payload["run_id"],
            "role_instance": "synthesizer",
            "status": "completed",
            "research_summary": "Evidence-backed mock synthesis.",
            "evidence_refs": ["source:mock"],
            "open_questions": [],
            "report": {
                "summary": "Evidence-backed mock synthesis.",
                "recommendation": "approve",
            },
        },
    )])
    aggregate = next(
        event
        for event in reversed(log.read_all())
        if event.type == "fanout.aggregate.completed"
        and event.payload.get("fanout_id") == fanout.payload["fanout_id"]
    )
    artifact_ref = str(aggregate.payload["research_artifact_ref"])
    artifact_digest = str(
        aggregate.payload["research_artifact_digest"]
    )
    assert (state_dir / artifact_ref).is_file()
    result_event = next(
        event
        for event in reversed(log.read_all())
        if event.type == "workflow.result.available"
        and event.payload.get("request_id") == "REQ-DOC156"
    )
    assert result_event.payload["request_revision"] == request_revision
    assert result_event.payload["origin_binding"] == origin_binding
    result_updates = [
        event
        for event in log.read_all()
        if event.type == "channel.state_update.posted"
        and event.payload.get("status") == "research_result_available"
    ]
    assert len(result_updates) == 1
    assert result_updates[0].payload["channel_id"] == channel_id
    assert result_updates[0].payload["thread_id"] == "main"
    writer.append(ZfEvent(
        type="run.completed",
        actor="orchestrator",
        task_id="TASK-DOC156",
        correlation_id=str(invoke.payload["workflow_run_id"]),
        payload={
            "workflow_run_id": str(invoke.payload["workflow_run_id"]),
            "status": "completed",
            "source_event_id": aggregate.id,
        },
        causation_id=aggregate.id,
    ))

    adoption = _approved_action(
        service,
        writer,
        "research-adopt",
        {
            "task_id": "TASK-DOC156",
            "request_id": "REQ-DOC156",
            "request_revision": request_revision,
            "result_event_id": result_event.id,
            "artifact_ref": artifact_ref,
            "artifact_digest": artifact_digest,
            "summary": "Adopt the mock evidence into the workflow request.",
            "channel_id": channel_id,
            "thread_id": "main",
        },
    )
    assert adoption["status"] == "adopted"

    delivery = _approved_action(
        service,
        writer,
        "workflow-invoke",
        {
            "task_id": "TASK-DOC156",
            "pattern_id": "delivery-smoke",
            "request_id": "REQ-DOC156",
            "request_revision": request_revision,
            "dispatch_id": "dispatch-doc156",
            "channel_id": channel_id,
            "thread_id": "main",
            "reason": "approved post-research delivery start",
        },
    )
    delivery_invoke = next(
        event
        for event in reversed(log.read_all())
        if event.id == delivery["event_id"]
    )
    orchestrator.run_once(events=[delivery_invoke])
    events = log.read_all()
    assert any(
        event.type == "workflow.invoke.accepted"
        and event.payload.get("pattern_id") == "delivery-smoke"
        for event in events
    )
    assert any(
        event.type == "fanout.child.dispatched"
        and event.payload.get("stage_id") == "delivery-smoke"
        and event.payload.get("role_instance") == "delivery_worker"
        for event in events
    )
    assert sum(
        event.type == "operator.action.resolved"
        for event in events
    ) == 7


def test_four_lens_discussion_cross_review_and_signoff_closes(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    service = ControlledActionService(
        state_dir,
        writer,
        project_root=tmp_path,
        actor="operator",
        source="kanban-agent",
        surface="web",
    )
    channel_id = "ch-four-lens-review"
    thread_id = "main"

    created = _approved_action(
        service,
        writer,
        "channel-create-from-template",
        {
            "template_id": "architecture-review",
            "channel_id": channel_id,
            "overrides": {"backend": "fake"},
        },
    )
    assert created["status"] == "created"
    for question_id, target, question in (
        (
            "q-security",
            "security_reviewer",
            "Which boundary prevents untrusted writes?",
        ),
        (
            "q-implementation",
            "dev_reviewer",
            "Which implementation path preserves replay?",
        ),
    ):
        writer.emit(
            "channel.question.opened",
            actor="arch",
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "question_id": question_id,
                "question": question,
                "kind": "clarification",
                "priority": "p0",
                "target_member_id": target,
                "asked_by": "arch",
                "source": "test",
            },
        )
    started = _approved_action(
        service,
        writer,
        "channel-discussion-start",
        {
            "channel_id": channel_id,
            "thread_id": thread_id,
            "message": "Review the delivery boundary from every role lens.",
        },
    )
    assert started["participants"] == [
        "arch",
        "security_reviewer",
        "dev_reviewer",
        "critic",
    ]

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        detail = project_channel(state_dir, channel_id)
        session = discussion_state(detail, thread_id)
        blind_replies = [
            event
            for event in log.read_all()
            if event.type == "channel.agent.reply.completed"
            and event.payload.get("thread_id") == thread_id
        ]
        phase1_members = valid_phase1_members(detail, session, thread_id)
        if len(blind_replies) == 4 and phase1_members == set(
            session.get("roster") or []
        ):
            break
        time.sleep(0.02)
    assert len(blind_replies) == 4
    assert phase1_members == set(session.get("roster") or [])

    advance_discussion(
        state_dir,
        writer,
        channel_id=channel_id,
        thread_id=thread_id,
        project_root=tmp_path,
    )
    dedup_request = next(
        event
        for event in reversed(log.read_all())
        if event.type == "channel.question.dedup.requested"
    )
    applied, reason = apply_question_dedup_reply(
        state_dir=state_dir,
        writer=writer,
        channel_id=channel_id,
        thread_id=thread_id,
        request_id=str(dedup_request.payload["request_id"]),
        payload={
            "ledger_digest": str(dedup_request.payload["ledger_digest"]),
            "groups": [],
            "question_updates": [
                {
                    "question_id": "q-security",
                    "kind": "clarification",
                    "priority": "p0",
                    "target_member_id": "security_reviewer",
                },
                {
                    "question_id": "q-implementation",
                    "kind": "clarification",
                    "priority": "p0",
                    "target_member_id": "dev_reviewer",
                },
            ],
            "cross_review_requests": [
                {
                    "question_id": "q-security",
                    "target_member_ids": ["security_reviewer"],
                    "prompt": "Challenge the proposed security boundary.",
                    "reason": "The blind findings need a security counterexample.",
                    "source_refs": ["event:blind-security"],
                },
                {
                    "question_id": "q-implementation",
                    "target_member_ids": ["dev_reviewer"],
                    "prompt": "Challenge the replay-safe implementation path.",
                    "reason": "The blind findings need an implementation check.",
                    "source_refs": ["event:blind-implementation"],
                },
            ],
        },
        actor="arch",
        source="test",
        causation_id="reply-dedup",
    )
    assert (applied, reason) == (True, "applied")

    host = SimpleNamespace(
        state_dir=state_dir,
        event_log=log,
        event_writer=writer,
        project_root=tmp_path,
        config=None,
        openclaw_client=None,
    )
    review_requests = [
        event
        for event in log.read_all()
        if event.type == "channel.cross_review.requested"
    ]
    assert len(review_requests) == 2
    for request in review_requests:
        react_channel_cross_review_requested(host, request)
        react_channel_cross_review_requested(host, request)
    wait_for_channel(lambda: all(r["status"] == "completed" for r in project_channel(state_dir, channel_id)["cross_reviews"]))
    detail = project_channel(state_dir, channel_id)
    assert {
        (review["target_member_id"], review["status"])
        for review in detail["cross_reviews"]
    } == {
        ("security_reviewer", "completed"),
        ("dev_reviewer", "completed"),
    }

    for question_id in ("q-security", "q-implementation"):
        resolved = _approved_action(
            service,
            writer,
            "channel-question-resolve",
            {
                "channel_id": channel_id,
                "thread_id": thread_id,
                "question_id": question_id,
                "resolution": "answered",
                "answer": f"Owner accepted the review for {question_id}.",
            },
        )
        assert resolved["status"] == "resolved"

    advance_discussion(
        state_dir,
        writer,
        channel_id=channel_id,
        thread_id=thread_id,
        project_root=tmp_path,
    )
    synthesis_request = next(
        event
        for event in reversed(log.read_all())
        if event.type == "channel.synthesis.requested"
    )
    EventReactorMixin._on_channel_synthesis_requested(
        host,
        synthesis_request,
    )
    wait_for_channel(lambda: any(e.type == "channel.consensus.proposed" for e in log.read_all()))
    consensus = next(
        event
        for event in reversed(log.read_all())
        if event.type == "channel.consensus.proposed"
    )
    assert consensus.payload["required_signers"] == [
        "arch",
        "security_reviewer",
        "dev_reviewer",
        "critic",
    ]
    react_channel_consensus_proposed(host, consensus)
    react_channel_consensus_proposed(host, consensus)
    wait_for_channel(lambda: len([e for e in log.read_all() if e.type == "channel.consensus.signed"]) == 4)
    signed = {
        event.payload.get("member_id")
        for event in log.read_all()
        if event.type == "channel.consensus.signed"
        and event.payload.get("artifact_digest")
        == consensus.payload["artifact_digest"]
    }
    assert signed == {
        "arch",
        "security_reviewer",
        "dev_reviewer",
        "critic",
    }

    confirmed = _approved_action(
        service,
        writer,
        "channel-consensus-confirm",
        {
            "channel_id": channel_id,
            "thread_id": thread_id,
            "artifact_ref": consensus.payload["artifact_ref"],
            "artifact_digest": consensus.payload["artifact_digest"],
            "prd_revision": consensus.payload["prd_revision"],
        },
    )
    assert confirmed["status"] == "confirmed"
    advance_discussion(
        state_dir,
        writer,
        channel_id=channel_id,
        thread_id=thread_id,
        project_root=tmp_path,
    )
    assert any(
        event.type == "channel.consensus.reached"
        and event.payload.get("channel_id") == channel_id
        for event in log.read_all()
    )
