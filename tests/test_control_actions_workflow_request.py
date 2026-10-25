from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from zf.core.config.loader import load_config
from zf.core.events import ZfEvent
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.workflow_anchor import (
    WORKFLOW_TASK_REQUEST_ROTATION_SOURCE,
    workflow_task_request_binding,
)
from zf.runtime.workflow_intake import build_flow_intake
from zf.runtime.workflow_origin import workflow_origin_digest
from zf.runtime.workflow_requests import (
    WorkflowRequestError,
    load_workflow_request,
)
from zf.runtime.task_workflow_plans import task_workflow_binding_digest
from zf.runtime.workflow_start import WorkflowStartService
from zf.runtime.workflow_synthesis import (
    WORKFLOW_SYNTHESIS_RESULT_SCHEMA,
    run_workflow_synthesis as _run_workflow_synthesis,
)


def _service(tmp_path: Path) -> tuple[ControlledActionService, EventLog]:
    config_ref = tmp_path / "zf.yaml"
    config_ref.write_text("""\
apiVersion: zaofu.dev/v1
kind: IssueFlow
metadata: {name: issue-demo}
spec:
  lanes: 1
  backend: mock
  issueRef: docs/intake/bug.md
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  version: "1.0"
  project: {name: demo, state_dir: .zf}
""", encoding="utf-8")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    service = ControlledActionService(
        state_dir,
        EventWriter(log),
        config=load_config(config_ref),
        project_root=tmp_path,
        actor="operator",
        source="kanban-agent",
        surface="web",
    )
    return service, log


def _execute(service: ControlledActionService, action: str, payload: dict) -> dict:
    return service._execute_action(
        action=action,
        requested_action=action,
        payload=payload,
        requested=ZfEvent(type="control.action.requested", actor="test", payload=payload),
    )


def test_request_is_proposed_before_explicit_submit(tmp_path: Path) -> None:
    service, log = _service(tmp_path)

    proposed = _execute(service, "workflow-request", {
        "kind": "issue",
        "objective": "Fix session expiry and add a regression test",
        "backend": "mock",
        "allow_missing_env": True,
    })

    assert proposed["ok"] is True
    assert proposed["status"] == "proposal_ready"
    assert Path(proposed["intake_ref"]).exists()
    assert "workflow.invoke.requested" not in [event.type for event in log.read_all()]

    submitted = _execute(service, "workflow-submit", {
        "intake_ref": proposed["intake_ref"],
        "request_id": proposed["request_id"],
        "proposal_ref": proposed["proposal_ref"],
        "proposal_digest": proposed["proposal_digest"],
        "kind": "issue",
        "allow_missing_env": True,
    })

    assert submitted["ok"] is True
    types = [event.type for event in log.read_all()]
    assert "workflow.request.proposed" in types
    assert "workflow.request.approved" in types
    assert "workflow.submit.accepted" in types
    assert "workflow.invoke.requested" in types

    replay = _execute(service, "workflow-submit", {
        "intake_ref": proposed["intake_ref"],
        "request_id": proposed["request_id"],
        "proposal_ref": proposed["proposal_ref"],
        "proposal_digest": proposed["proposal_digest"],
        "kind": "issue",
        "allow_missing_env": True,
    })
    assert replay["ok"] is True
    assert len([
        event for event in log.read_all()
        if event.type == "workflow.invoke.requested"
    ]) == 1


def test_terminal_workflow_request_replay_fails_closed(tmp_path: Path) -> None:
    service, log = _service(tmp_path)
    proposed = _execute(service, "workflow-request", {
        "kind": "issue",
        "objective": "Fix session expiry and add a regression test",
        "backend": "mock",
        "allow_missing_env": True,
    })
    submit_payload = {
        "intake_ref": proposed["intake_ref"],
        "request_id": proposed["request_id"],
        "proposal_ref": proposed["proposal_ref"],
        "proposal_digest": proposed["proposal_digest"],
        "kind": "issue",
        "allow_missing_env": True,
    }
    assert _execute(service, "workflow-submit", submit_payload)["ok"] is True
    invoke = next(
        event for event in log.read_all()
        if event.type == "workflow.invoke.requested"
    )
    EventWriter(log).append(ZfEvent(
        type="run.goal.blocked",
        actor="orchestrator",
        task_id=invoke.task_id,
        correlation_id=proposed["request_id"],
        payload={
            "run_id": proposed["request_id"],
            "workflow_run_id": proposed["request_id"],
            "request_id": proposed["request_id"],
            "reason": "stage replan cap exhausted",
        },
    ))

    replay = _execute(service, "workflow-submit", submit_payload)

    assert replay["ok"] is False
    assert replay["status"] == "STOP"
    assert replay["workflow_invoke_status"] == "terminal"
    assert replay["blockers"][0]["kind"] == "workflow_request_run_terminal"
    assert sum(
        event.type == "workflow.invoke.requested"
        for event in log.read_all()
    ) == 1


def test_channel_workflow_request_pins_canonical_origin(
    tmp_path: Path,
) -> None:
    service, log = _service(tmp_path)

    proposed = _execute(service, "workflow-request", {
        "kind": "issue",
        "objective": "Research session expiry evidence",
        "backend": "mock",
        "allow_missing_env": True,
        "project_id": "demo",
        "channel_id": "ch-product",
        "thread_id": "session-expiry",
        "conversation_id": "ignored-when-channel-is-primary",
    })

    assert proposed["ok"] is True
    assert proposed["request_revision"] >= 1
    origin = proposed["origin_binding"]
    assert origin["schema_version"] == "workflow-origin-binding.v1"
    assert origin["surface"] == "channel"
    assert origin["channel_id"] == "ch-product"
    assert origin["thread_id"] == "session-expiry"
    projection = load_workflow_request(
        service.state_dir,
        proposed["request_id"],
    )
    assert projection["origin_binding"] == origin
    manifest = Path(
        projection["workflow_input_manifest_ref"]
    )
    assert manifest.exists()
    assert json.loads(
        manifest.read_text(encoding="utf-8")
    )["origin_binding"] == origin
    intake_event = next(
        event
        for event in log.read_all()
        if event.type == "workflow.intake.created"
    )
    assert intake_event.payload["origin_binding"] == origin
    service.source = "feishu-agent"
    continued = _execute(service, "workflow-request", {
        "request_id": proposed["request_id"],
        "kind": "issue",
        "objective": "Continue the same request from another adapter",
        "backend": "mock",
        "allow_missing_env": True,
    })
    assert continued["ok"] is True
    assert continued["origin_binding"] == origin
    service.source = "kanban-agent"
    manifest_before = manifest.read_text(encoding="utf-8")

    mismatch = _execute(service, "workflow-request", {
        "request_id": proposed["request_id"],
        "kind": "issue",
        "objective": "Attempt to redirect the same request",
        "backend": "mock",
        "allow_missing_env": True,
        "project_id": "demo",
        "channel_id": "ch-other",
        "thread_id": "main",
    })

    assert mismatch["status"] == "origin_binding_mismatch"
    assert load_workflow_request(
        service.state_dir,
        proposed["request_id"],
    )["origin_binding"] == origin
    assert manifest.read_text(encoding="utf-8") == manifest_before


def test_existing_workflow_request_creates_confirmed_requirement_revision(
    tmp_path: Path,
) -> None:
    service, _log = _service(tmp_path)
    initial = _execute(service, "workflow-request", {
        "request_id": "REQ-REVISION",
        "kind": "issue",
        "objective": "Fix the original session expiry behavior",
        "backend": "mock",
        "allow_missing_env": True,
    })
    initial_projection = load_workflow_request(
        service.state_dir,
        initial["request_id"],
    )

    revised = _execute(service, "workflow-request", {
        "request_id": initial["request_id"],
        "kind": "issue",
        "objective": "Fix the revised session expiry behavior",
        "backend": "mock",
        "allow_missing_env": True,
    })

    projection = load_workflow_request(
        service.state_dir,
        initial["request_id"],
    )
    requirement = json.loads(
        Path(projection["requirement_spec_ref"]).read_text(encoding="utf-8")
    )
    assert revised["ok"] is True
    assert projection["revision"] == initial_projection["revision"] + 1
    assert projection["confirmed"] is True
    assert requirement["objective"] == (
        "Fix the revised session expiry behavior"
    )


def test_workflow_start_active_request_is_immutable_and_exact_replay_is_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, log = _service(tmp_path)
    task_store = TaskStore(service.state_dir / "kanban.json")
    task_store.add(Task(
        id="TASK-ACTIVE-REQUEST",
        title="Deliver the approved issue",
        contract=TaskContract(
            behavior="Deliver the approved issue behavior.",
            verification="Run the focused issue regression.",
            verification_tiers=["runtime"],
            owner_instance="fix-lane-0",
            spec_skip_reason="isolated workflow-start regression fixture",
        ),
    ))
    objective = "Fix session expiry and add a regression test"
    proposed = _execute(service, "workflow-request", {
        "kind": "issue",
        "objective": objective,
        "backend": "mock",
        "task_id": "TASK-ACTIVE-REQUEST",
        "allow_missing_env": True,
    })
    submitted = _execute(service, "workflow-submit", {
        "intake_ref": proposed["intake_ref"],
        "request_id": proposed["request_id"],
        "proposal_ref": proposed["proposal_ref"],
        "proposal_digest": proposed["proposal_digest"],
        "kind": "issue",
        "task_id": "TASK-ACTIVE-REQUEST",
        "allow_missing_env": True,
    })
    assert submitted["ok"] is True
    before = load_workflow_request(service.state_dir, proposed["request_id"])
    direct_event_count = len(log.read_all())
    direct_revision = _execute(service, "workflow-request", {
        "request_id": proposed["request_id"],
        "kind": "issue",
        "objective": "Expand the active request with a new dashboard",
        "backend": "mock",
        "task_id": "TASK-ACTIVE-REQUEST",
        "allow_missing_env": True,
    })
    assert direct_revision["ok"] is False
    assert direct_revision["status"] == "workflow_request_active"
    assert not any(
        event.type in {"workflow.request.updated", "workflow.request.proposed"}
        for event in log.read_all()[direct_event_count:]
    )
    start = WorkflowStartService(
        service.state_dir,
        service.config,
        project_root=tmp_path,
    )
    route = {
        "route_id": "delivery:issue:standard",
        "family": "delivery",
        "kind": "issue",
        "tier": "standard",
        "start_adapter": "delivery_request_submit",
        "available": True,
    }
    catalog = {
        "schema_version": "workflow-route-catalog.v1",
        "config_digest": "sha256:test-active-request",
        "routes": [route],
    }
    monkeypatch.setattr(
        "zf.runtime.workflow_start.workflow_route_catalog",
        lambda _config: catalog,
    )
    monkeypatch.setattr(
        "zf.runtime.workflow_start.resolve_workflow_route",
        lambda _config, route_id, **_kwargs: (
            route if route_id == route["route_id"] else None
        ),
    )
    routes = start.routes(task_id="TASK-ACTIVE-REQUEST")
    base_payload = {
        "task_id": "TASK-ACTIVE-REQUEST",
        "route_id": route["route_id"],
        "objective": objective,
        "task_contract_digest": routes["task_contract_digest"],
        "config_digest": routes["config_digest"],
    }
    event_count = len(log.read_all())

    changed = _execute(service, "workflow-start", {
        **base_payload,
        "objective": "Expand the active request with a new dashboard",
    })

    assert changed["ok"] is False
    assert changed["status"] == "workflow_request_active"
    assert load_workflow_request(
        service.state_dir, proposed["request_id"]
    )["revision"] == before["revision"]
    assert not any(
        event.type in {"workflow.request.updated", "workflow.request.proposed"}
        for event in log.read_all()[event_count:]
    )

    replayed = _execute(service, "workflow-start", base_payload)

    assert replayed["ok"] is True, replayed.get("reason")
    assert replayed["idempotent_replay"] is True
    assert replayed["workflow_invoke_status"] == "already_requested"
    assert load_workflow_request(
        service.state_dir, proposed["request_id"]
    )["revision"] == before["revision"]
    assert sum(
        event.type == "workflow.invoke.requested"
        for event in log.read_all()
    ) == 1


def test_channel_plan_only_prd_blocks_actual_workflow_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, log = _service(tmp_path)
    task = Task(
        id="TASK-CHANNEL-PLAN-ONLY",
        title="Plan the confirmed public map",
        contract=TaskContract(
            behavior="Produce the approved public-map planning artifacts.",
            verification="Review the generated planning artifacts.",
            verification_tiers=["runtime"],
            source_mode="channel_prd",
            source_ref="channels/ch-plan-only/prd/r1.json",
            source_revision="1",
            evidence_contract={
                "execution_owner": "workflow",
                "channel_id": "ch-plan-only",
                "thread_id": "main",
                "channel_member_id": "product_pm",
                "leader_revision": 1,
                "prd_revision": 1,
                "source_digest": "canonical-plan-only",
                "readiness_ref": "channels/ch-plan-only/prd/r1-readiness.json",
                "readiness_digest": "readiness-plan-only",
                "readiness_verdict": "ready",
                "implementation_start": False,
                "readiness_risk_accepted": False,
            },
        ),
    )
    TaskStore(service.state_dir / "kanban.json").add(task)
    route = {
        "route_id": "delivery:issue:default",
        "family": "delivery",
        "kind": "issue",
        "tier": "default",
        "start_adapter": "delivery_request_submit",
        "available": True,
    }
    catalog = {
        "schema_version": "workflow-route-catalog.v1",
        "config_digest": "sha256:plan-only",
        "routes": [route],
    }
    monkeypatch.setattr(
        "zf.runtime.workflow_start.workflow_route_catalog",
        lambda _config: catalog,
    )
    monkeypatch.setattr(
        "zf.runtime.workflow_start.resolve_workflow_route",
        lambda _config, route_id, **_kwargs: (
            route if route_id == route["route_id"] else None
        ),
    )
    digest = task_workflow_binding_digest(task)

    result = _execute(service, "workflow-start", {
        "task_id": task.id,
        "route_id": route["route_id"],
        "objective": "Start delivery for the public map.",
        "task_contract_digest": digest,
        "config_digest": catalog["config_digest"],
    })

    assert result["ok"] is False
    assert result["status"] == "channel_prd_execution_blocked"
    assert "permits Task and Workflow planning" in result["reason"]
    assert not any(
        event.type == "workflow.invoke.requested"
        for event in log.read_all()
    )


def test_intake_rejects_origin_change_before_overwriting_sidecars(
    tmp_path: Path,
) -> None:
    service, _log = _service(tmp_path)
    proposed = _execute(service, "workflow-request", {
        "request_id": "REQ-IMMUTABLE",
        "kind": "issue",
        "objective": "Preserve the canonical result destination",
        "backend": "mock",
        "allow_missing_env": True,
        "project_id": "demo",
        "channel_id": "ch-original",
        "thread_id": "main",
    })
    source_manifest = (
        tmp_path
        / "artifacts"
        / "workflow"
        / "REQ-IMMUTABLE"
        / "workflow-input-manifest.json"
    )
    before = source_manifest.read_text(encoding="utf-8")

    with pytest.raises(WorkflowRequestError, match="canonical request origin"):
        build_flow_intake(
            kind="issue",
            objective="Attempt to redirect the same request",
            backend="mock",
            project_id="demo",
            request_id="REQ-IMMUTABLE",
            source="channel",
            channel_id="ch-other",
            thread_id="main",
            output=tmp_path / "docs" / "intake" / "REQ-IMMUTABLE.md",
        )

    assert source_manifest.read_text(encoding="utf-8") == before


def test_workflow_submit_binds_existing_task_before_invoke(
    tmp_path: Path,
) -> None:
    service, log = _service(tmp_path)
    TaskStore(service.state_dir / "kanban.json").add(
        Task(id="TASK-SUBMIT", title="Submit request")
    )
    proposed = _execute(service, "workflow-request", {
        "kind": "issue",
        "objective": "Fix session expiry and add a regression test",
        "backend": "mock",
        "task_id": "TASK-SUBMIT",
        "allow_missing_env": True,
    })

    submitted = _execute(service, "workflow-submit", {
        "intake_ref": proposed["intake_ref"],
        "request_id": proposed["request_id"],
        "proposal_ref": proposed["proposal_ref"],
        "proposal_digest": proposed["proposal_digest"],
        "kind": "issue",
        "task_id": "TASK-SUBMIT",
        "allow_missing_env": True,
    })

    assert submitted["ok"] is True
    task = TaskStore(service.state_dir / "kanban.json").get("TASK-SUBMIT")
    assert task is not None
    assert workflow_task_request_binding(task) == {
        "request_id": proposed["request_id"],
        "request_revision": proposed["request_revision"],
        "origin_binding_digest": workflow_origin_digest(
            proposed["origin_binding"]
        ),
    }
    event_types = [event.type for event in log.read_all()]
    assert event_types.index("task.contract.update") < event_types.index(
        "workflow.invoke.requested"
    )
    binding_event = next(
        event
        for event in log.read_all()
        if event.type == "task.contract.update"
        and event.payload.get("source") == "workflow_submit"
    )
    assert binding_event.payload["contract_digest"] == (
        task_workflow_binding_digest(task)
    )


def test_workflow_start_rotates_terminal_request_for_blocked_task(
    tmp_path: Path,
) -> None:
    service, log = _service(tmp_path)
    task_store = TaskStore(service.state_dir / "kanban.json")
    task_store.add(Task(
        id="TASK-ROTATE",
        title="Retry the blocked delivery",
        contract=TaskContract(
            behavior="Deliver the requested issue fix.",
            verification="Run the focused runtime regression.",
            verification_tiers=["runtime"],
        ),
    ))
    proposed = _execute(service, "workflow-request", {
        "kind": "issue",
        "objective": "Fix session expiry and add a regression test",
        "backend": "mock",
        "task_id": "TASK-ROTATE",
        "allow_missing_env": True,
        "project_id": "demo",
        "conversation_id": "kanban:demo",
        "thread_key": "session-expiry",
    })
    submitted = _execute(service, "workflow-submit", {
        "intake_ref": proposed["intake_ref"],
        "request_id": proposed["request_id"],
        "proposal_ref": proposed["proposal_ref"],
        "proposal_digest": proposed["proposal_digest"],
        "kind": "issue",
        "task_id": "TASK-ROTATE",
        "allow_missing_env": True,
    })
    assert submitted["ok"] is True, submitted
    old_request = load_workflow_request(
        service.state_dir,
        proposed["request_id"],
    )
    terminal = EventWriter(log).emit(
        "run.goal.blocked",
        actor="orchestrator",
        task_id="TASK-ROTATE",
        correlation_id=proposed["request_id"],
        payload={
            "run_id": proposed["request_id"],
            "workflow_run_id": proposed["request_id"],
            "request_id": proposed["request_id"],
            "reason": "stage budget exhausted",
        },
    )
    task_store.update(
        "TASK-ROTATE",
        status="blocked",
        blocked_reason="stage budget exhausted",
    )

    preview_config = load_config(
        Path(__file__).resolve().parents[1] / "zf.yaml"
    )
    start = WorkflowStartService(
        service.state_dir,
        preview_config,
        project_root=tmp_path,
    )
    routes = start.routes(task_id="TASK-ROTATE")
    preview = start.preview(
        {
            "task_id": "TASK-ROTATE",
            "route_id": "research:fixed",
            "objective": "Retry the blocked delivery with the same contract.",
            "task_contract_digest": routes["task_contract_digest"],
            "config_digest": routes["config_digest"],
            "project_id": "demo",
            "conversation_id": "kanban:demo",
            "thread_id": "session-expiry",
        },
        require_bindings=True,
        origin="web",
    )

    assert preview["ok"] is True, preview
    assert preview["payload"]["fresh_request"] is True
    assert "request_id" not in preview["payload"]
    assert preview["payload"]["prior_request_id"] == proposed["request_id"]
    assert preview["payload"]["prior_terminal_event_id"] == terminal.id
    assert preview["payload"]["origin_binding"] == proposed["origin_binding"]
    assert preview["payload"]["origin_binding"]["surface"] == (
        "kanban_agent"
    )

    next_request = _execute(service, "workflow-request", {
        "request_id": "REQ-ROTATED",
        "kind": "issue",
        "objective": "Retry the blocked delivery with the same contract.",
        "backend": "mock",
        "task_id": "TASK-ROTATE",
        "allow_missing_env": True,
        "fresh_request": True,
        "origin_binding": preview["payload"]["origin_binding"],
        "prior_request_id": preview["payload"]["prior_request_id"],
        "prior_request_revision": preview["payload"][
            "prior_request_revision"
        ],
        "prior_terminal_event_id": preview["payload"][
            "prior_terminal_event_id"
        ],
    })
    assert next_request["ok"] is True, next_request
    restarted = _execute(service, "workflow-submit", {
        "intake_ref": next_request["intake_ref"],
        "request_id": next_request["request_id"],
        "proposal_ref": next_request["proposal_ref"],
        "proposal_digest": next_request["proposal_digest"],
        "kind": "issue",
        "task_id": "TASK-ROTATE",
        "allow_missing_env": True,
    })

    assert restarted["ok"] is True
    new_request_id = restarted["request_id"]
    assert new_request_id != proposed["request_id"]
    assert load_workflow_request(
        service.state_dir,
        proposed["request_id"],
    )["revision"] == old_request["revision"]
    assert load_workflow_request(
        service.state_dir,
        new_request_id,
    )["origin_binding"] == proposed["origin_binding"]
    invokes = [
        event
        for event in log.read_all()
        if event.type == "workflow.invoke.requested"
    ]
    assert len(invokes) == 2
    assert len({event.correlation_id for event in invokes}) == 2
    task = task_store.get("TASK-ROTATE")
    assert task is not None
    binding = workflow_task_request_binding(task)
    assert binding["request_id"] == new_request_id
    rotation = next(
        event
        for event in reversed(log.read_all())
        if event.type == "task.contract.update"
        and event.payload.get("source")
        == WORKFLOW_TASK_REQUEST_ROTATION_SOURCE
    )
    assert rotation.payload["prior_request_id"] == proposed["request_id"]
    assert rotation.payload["prior_terminal_event_id"] == terminal.id
    assert rotation.payload["contract_digest"] == (
        task_workflow_binding_digest(task)
    )


def test_submit_and_reject_bind_exact_current_proposal(tmp_path: Path) -> None:
    service, log = _service(tmp_path)
    proposed = _execute(service, "workflow-request", {
        "kind": "issue",
        "objective": "Fix session expiry and add a regression test",
        "backend": "mock",
        "allow_missing_env": True,
    })

    stale = _execute(service, "workflow-submit", {
        "intake_ref": proposed["intake_ref"],
        "request_id": proposed["request_id"],
        "proposal_ref": proposed["proposal_ref"],
        "proposal_digest": "f" * 64,
        "kind": "issue",
        "allow_missing_env": True,
    })
    assert stale["ok"] is False
    assert stale["status"] == "stale_proposal"
    assert "workflow.invoke.requested" not in [
        event.type for event in log.read_all()
    ]

    rejected = _execute(service, "workflow-reject", {
        "request_id": proposed["request_id"],
        "proposal_ref": proposed["proposal_ref"],
        "proposal_digest": proposed["proposal_digest"],
        "reason": "scope needs revision",
    })
    assert rejected["ok"] is True
    assert rejected["status"] == "rejected"
    assert "workflow.request.rejected" in [
        event.type for event in log.read_all()
    ]


def test_vague_request_requires_clarification_and_never_invokes(tmp_path: Path) -> None:
    service, log = _service(tmp_path)

    result = _execute(service, "workflow-request", {
        "kind": "issue",
        "objective": "",
        "open_questions": ["Which checkout path is affected?"],
        "backend": "mock",
        "allow_missing_env": True,
    })

    assert result["ok"] is False
    assert result["status"] == "clarification_required"
    blocker_kinds = {item["kind"] for item in result["blockers"]}
    assert "workflow_request_required_fields_missing" in blocker_kinds
    assert "workflow_request_open_questions" in blocker_kinds
    assert "workflow.invoke.requested" not in [event.type for event in log.read_all()]


def test_initialized_idea_to_product_prepares_before_explicit_submit(tmp_path: Path) -> None:
    service, log = _service(tmp_path)

    result = _execute(service, "idea-to-product", {
        "objective": "Fix session expiry and add a regression test",
        "kind": "issue",
        "backend": "mock",
        "allow_missing_env": True,
    })

    assert result["ok"] is True
    assert result["status"] == "proposal_ready"
    assert load_workflow_request(service.state_dir, result["request_id"])["status"] == "proposed"
    assert not any(event.type == "workflow.invoke.requested" for event in log.read_all())
    assert not any(event.type == "operator.action.proposed" for event in log.read_all())


def test_idea_to_product_explicit_proposal_only_keeps_nested_plan(tmp_path: Path) -> None:
    service, log = _service(tmp_path)
    result = _execute(service, "idea-to-product", {
        "objective": "Preview a product delivery plan",
        "proposal_only": True,
    })
    assert result["status"] == "proposed"
    assert not any(event.type == "workflow.request.created" for event in log.read_all())
    proposal = [
        event for event in log.read_all() if event.type == "operator.action.proposed"
    ][-1]
    assert [item["action"] for item in proposal.payload["proposals"]] == [
        "workflow-request",
        "workflow-submit",
    ]


@pytest.mark.parametrize("lanes", [1, 2])
def test_workflow_request_passes_admitted_synthesis_to_proposal_builder(
    tmp_path: Path,
    monkeypatch,
    lanes: int,
) -> None:
    service, _log = _service(tmp_path)
    base = tmp_path / "zf.yaml"
    if lanes == 2:
        body = base.read_text().replace("  lanes: 1", "  topology: fanout\n  lanes: 1")
        body += "  workflow:\n    candidate_quality_source: task_contract_required\n"
        base.write_text(body)
        service.config = load_config(base)
    original = base.read_bytes()

    def _synthesize(**kwargs):
        projection = load_workflow_request(
            kwargs["state_dir"],
            kwargs["request_id"],
        )
        candidate = {
            "schema_version": WORKFLOW_SYNTHESIS_RESULT_SCHEMA,
            "request_id": projection["request_id"],
            "request_revision": projection["revision"],
            "requirement_ref": projection["requirement_spec_ref"],
            "requirement_digest": projection["requirement_spec_digest"],
            "selected_flow_family": "IssueFlow",
            "short_flow_spec": {
                "flow_family": "IssueFlow",
                "purpose": "Repair the confirmed issue",
                "parameters": {
                    "lanes": lanes,
                    "strictness": "standard",
                    "pattern_id": "issue-triage",
                },
            },
            "decision_rationale": "The request is a bounded issue repair.",
            "assumptions": [],
            "open_questions": [],
            "requested_roles": [],
            "requested_skills": [],
            "requested_profiles": ["direct-v1"],
            "completion_profile": {
                "delivery_policy": "report_only",
                "completion_threshold": "",
                "required_artifacts": [],
            },
            "risk_hints": [],
        }
        return _run_workflow_synthesis(
            **{
                **kwargs,
                "candidate_result": candidate,
            }
        )

    monkeypatch.setattr(
        "zf.runtime.workflow_synthesis.run_workflow_synthesis",
        _synthesize,
    )
    proposed = _execute(service, "workflow-request", {
        "kind": "issue",
        "objective": "Fix session expiry and add a regression test",
        "backend": "mock",
        "synthesis_backend": "mock",
        "allow_missing_env": True,
    })

    assert proposed["ok"] is True
    assert proposed["status"] == "synthesis_queued"
    assert proposed["synthesis_operation_id"]
    assert not proposed.get("proposal")

    from zf.runtime.workflow_synthesis import consume_workflow_synthesis_operations

    consumed = consume_workflow_synthesis_operations(
        state_dir=service.state_dir,
        project_root=service.project_root,
        config=service.config,
        writer=service.writer,
        agent=None,
        limit=1,
    )
    assert consumed == 1
    request = load_workflow_request(service.state_dir, proposed["request_id"])
    assert request["status"] == "proposed"
    from zf.runtime.workflow_proposal import load_workflow_proposal

    proposal = load_workflow_proposal(
        service.state_dir,
        request["proposal_ref"],
    )
    assert proposal["synthesis_result_ref"] == request["synthesis_ref"]
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    import yaml
    snapshot = hydrate_sidecar_ref(service.state_dir, proposal["effective_config_ref"]).payload
    flow = next(doc for doc in yaml.safe_load_all(snapshot["yaml"]) if doc.get("kind") == "IssueFlow")
    assert flow["spec"]["lanes"] == lanes

    assert proposal["run_parameters"] == {
        "lanes": lanes,
        "strictness": "standard",
        "pattern_id": "issue-triage",
    }
    assert proposal["approval_status"] == "approvable"
    assert base.read_bytes() == original
    if lanes == 2:
        assert proposal["change_mode"] == "config_change"
        payload = {
            "request_id": request["request_id"],
            "intake_ref": proposed["intake_ref"],
            "proposal_ref": request["proposal_ref"],
            "proposal_digest": proposal["proposal_digest"],
            "kind": "issue", "allow_missing_env": True,
        }
        blocked = _execute(service, "workflow-submit", payload)
        assert blocked["status"] == "config_apply_required"
        applied = _execute(service, "workflow-config-apply", {
            "proposal_id": proposal["proposal_id"],
            "proposal_ref": request["proposal_ref"],
            "proposal_digest": proposal["proposal_digest"],
            "validation_result_ref": proposal["validation_result_ref"],
            "approval_ref": "owner:approved",
            "idempotency_key": "apply-synthesized-lanes",
        })
        assert applied["ok"] is True, applied
        effective = load_config(base)
        assert len([r for r in effective.roles if r.role_kind == "writer"]) == 2
        submitted = _execute(service, "workflow-submit", payload)
        assert submitted["ok"] is True, submitted
        assert any(e.type == "workflow.submit.requested" for e in _log.read_all())


def test_workflow_request_enqueue_returns_without_provider_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service, _log = _service(tmp_path)

    def blocking_provider(**kwargs):
        time.sleep(2)
        raise AssertionError("HTTP producer must not call synthesis provider")

    monkeypatch.setattr(
        "zf.runtime.workflow_synthesis.run_workflow_synthesis",
        blocking_provider,
    )
    started = time.monotonic()
    queued = _execute(service, "workflow-request", {
        "request_id": "req-fast-accept",
        "kind": "issue",
        "objective": "Fix session expiry and add a regression test",
        "backend": "mock",
        "synthesis_backend": "mock",
        "allow_missing_env": True,
    })

    assert time.monotonic() - started < 1
    assert queued["status"] == "synthesis_queued"
    assert queued["operation_status"] == "queued"


def test_workflow_synthesis_cancel_is_exact_controlled_and_replayable(
    tmp_path: Path,
) -> None:
    from zf.runtime.workflow_operation import load_workflow_operation

    service, _log = _service(tmp_path)
    queued = _execute(service, "workflow-request", {
        "request_id": "req-cancel",
        "kind": "issue",
        "objective": "Fix session expiry and add a regression test",
        "backend": "mock",
        "synthesis_backend": "mock",
        "allow_missing_env": True,
    })
    payload = {
        "request_id": queued["request_id"],
        "operation_id": queued["synthesis_operation_id"],
        "request_hash": queued["synthesis_request_hash"],
        "reason": "operator changed scope",
    }

    stale = _execute(
        service,
        "workflow-cancel",
        {**payload, "request_hash": "f" * 64},
    )
    assert stale["ok"] is False
    assert stale["status"] == "stale_operation"

    cancelled = _execute(service, "workflow-cancel", payload)
    replayed = _execute(service, "workflow-cancel", payload)

    assert cancelled["ok"] is True
    assert cancelled["status"] == "cancelled"
    assert cancelled["replayed"] is False
    assert replayed["ok"] is True
    assert replayed["replayed"] is True
    operation = load_workflow_operation(
        service.writer.event_log,
        queued["synthesis_operation_id"],
    )
    assert operation is not None
    assert operation["status"] == "cancelled"
