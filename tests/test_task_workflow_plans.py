from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.config.loader import load_config
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.event_problem_registry import event_is_recovery_actionable
from zf.runtime.kanban_plan_requests import (
    PLAN_REQUESTED_EVENT,
    pending_kanban_plan_requests,
    plan_response_gate,
)
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.task_workflow_plans import (
    build_task_workflow_plan_request,
    task_workflow_binding_digest,
)
from zf.runtime.workflow_anchor import is_workflow_managed_task
from zf.runtime.workflow_origin import build_workflow_origin_binding
from zf.runtime.workflow_route_catalog import workflow_route_catalog
from zf.runtime.workflow_start_inputs import prepare_approved_workflow_start
from zf.runtime.wake_patterns import WAKE_PATTERNS
from zf.runtime.watcher import EventWatcher


ROOT = Path(__file__).resolve().parents[1]


class _RecordingTransport:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    def send_task(
        self,
        role_name,
        briefing_path,
        prompt,
        *,
        context=None,
    ):  # noqa: ANN001
        self.sent.append(
            (role_name, briefing_path, prompt, context)
        )

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


def _service(
    tmp_path: Path,
) -> tuple[Path, EventWriter, ControlledActionService]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    service = ControlledActionService(
        state_dir,
        writer,
        config=load_config(ROOT / "zf.yaml"),
        project_root=ROOT,
        actor="web",
        source="kanban-agent",
        surface="web",
    )
    return state_dir, writer, service


def _execute(
    service: ControlledActionService,
    writer: EventWriter,
    action: str,
    payload: dict,
) -> dict:
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        payload={"action": action, "request": payload},
    )
    return service.execute(
        action=action,
        requested_action=action,
        payload=payload,
        requested=requested,
    )


def _workflow_plan() -> dict:
    return {
        "header": "Execution route",
        "question_id": "execution-route",
        "question": "How should this Task run?",
        "options": [
            {
                "id": "delivery",
                "label": "PRD delivery (Recommended)",
                "description": "Implement with writer and verify lanes.",
                "recommended": True,
                "route_id": "delivery:prd:standard",
                "parameters": {"target_root": "."},
            },
            {
                "id": "research",
                "label": "Research first",
                "description": "Collect evidence before delivery.",
                "route_id": "research:fixed",
                "parameters": {
                    "expected_output": "Evidence-backed recommendation.",
                },
            },
            {
                "id": "defer",
                "label": "No workflow yet",
                "description": "Track the Task without starting execution.",
                "mode": "defer",
            },
        ],
        "allow_other": True,
        "reason": "The execution topology changes cost and output.",
    }


def test_builder_binds_routes_to_task_and_active_config() -> None:
    config = load_config(ROOT / "zf.yaml")
    task = Task(
        id="TASK-WORKFLOW",
        title="Implement Task workflow planning",
        contract=TaskContract(
            behavior="Create a Task before choosing its workflow.",
            verification="Run focused workflow tests.",
        ),
    )

    request, warning = build_task_workflow_plan_request(
        _workflow_plan(),
        task=task,
        task_event_id="evt-task-created",
        config=config,
        context={
            "project_id": "zaofu",
            "conversation_id": "kanban:zaofu",
            "thread_id": "main",
        },
    )

    assert warning == ""
    assert request is not None
    assert request["subject_type"] == "task_workflow"
    assert request["task_id"] == task.id
    assert request["task_event_id"] == "evt-task-created"
    assert request["task_contract_digest"] == (
        task_workflow_binding_digest(task)
    )
    assert request["config_digest"].startswith("sha256:")
    delivery = request["options"][0]
    assert delivery["submit_mode"] == "propose"
    assert delivery["submit_action"] == "workflow-start"
    assert delivery["submit_payload"]["task_id"] == task.id
    assert delivery["submit_payload"]["task_contract_digest"] == (
        request["task_contract_digest"]
    )
    assert delivery["submit_details"]["lane_count"] == 2
    assert delivery["submit_details"]["writer_roles"] == [
        "prd-dev-lane-0",
        "prd-dev-lane-1",
    ]
    assert request["options"][2]["submit_mode"] == "continue"

    event = ZfEvent(
        type=PLAN_REQUESTED_EVENT,
        actor="kanban-agent",
        task_id=task.id,
        payload={"request": request, "plan_request": request},
    )
    modes = {}
    for option in request["options"]:
        gate = plan_response_gate(
            [event],
            request_event_id=event.id,
            request_id=request["request_id"],
            revision=request["revision"],
            question_id=request["question_id"],
            option_id=option["id"],
            answer=option["label"],
        )
        assert gate["ok"] is True, gate
        modes[option["id"]] = gate["submit_mode"]
    assert modes == {
        "delivery": "propose",
        "research": "propose",
        "defer": "continue",
    }


def test_builder_lifts_nested_objective_out_of_parameters() -> None:
    config = load_config(ROOT / "zf.yaml")
    task = Task(id="TASK-NESTED-OBJECTIVE", title="Default objective")
    raw_plan = _workflow_plan()
    raw_plan["options"][0]["parameters"] = {
        "objective": "Use the explicit nested objective",
        "expected_output": "A verified delivery",
        "target_root": ".",
    }

    request, warning = build_task_workflow_plan_request(
        raw_plan,
        task=task,
        task_event_id="evt-task-created",
        config=config,
    )

    assert warning == ""
    assert request is not None
    delivery = request["options"][0]["submit_payload"]
    assert delivery["objective"] == "Use the explicit nested objective"
    assert delivery["parameters"] == {
        "target_root": ".",
        "expected_output": "A verified delivery",
    }


def test_builder_rejects_delivery_option_that_cannot_execute_after_approve() -> None:
    config = load_config(ROOT / "zf.yaml")
    task = Task(id="TASK-NOT-READY", title="Missing delivery target")
    raw_plan = _workflow_plan()
    raw_plan["options"][0].pop("parameters")

    request, warning = build_task_workflow_plan_request(
        raw_plan,
        task=task,
        task_event_id="evt-task-created",
        config=config,
    )

    assert request is None
    assert "missing executable parameter(s): target_root" in warning


def test_channel_prd_task_rejects_research_without_workflow_request_binding() -> None:
    config = load_config(ROOT / "zf.yaml")
    task = Task(
        id="TASK-CHANNEL-RESEARCH",
        title="Research a confirmed Channel PRD",
        contract=TaskContract(
            behavior="Research the confirmed PRD.",
            verification="Produce an evidence-backed report.",
            source_mode="channel_prd",
            source_ref="channel-artifacts/ch-prd/prd.md",
            source_revision="1",
            evidence_contract={
                "channel_id": "ch-prd",
                "thread_id": "main",
                "channel_member_id": "product_pm",
                "leader_revision": 1,
                "prd_revision": 1,
                "source_digest": "sha256:canonical",
            },
        ),
    )

    request, warning = build_task_workflow_plan_request(
        _workflow_plan(),
        task=task,
        task_event_id="evt-channel-task-created",
        config=config,
    )

    assert request is None
    assert "research route requires a canonical Workflow Request binding" in warning


def test_create_task_keeps_task_when_workflow_plan_is_invalid(
    tmp_path: Path,
) -> None:
    state_dir, writer, service = _service(tmp_path)
    invalid_plan = _workflow_plan()
    invalid_plan["options"][0]["route_id"] = "delivery:missing"

    created = _execute(service, writer, "create-task", {
        "title": "Keep this Task",
        "workflow_plan": invalid_plan,
    })
    task_only = _execute(service, writer, "create-task", {
        "title": "Track without execution",
    })

    assert created["ok"] is True
    assert "is not active" in created["workflow_plan_warning"]
    assert created["workflow_plan_event_id"] == ""
    retained = TaskStore(state_dir / "kanban.json").get(created["task_id"])
    tracked = TaskStore(state_dir / "kanban.json").get(task_only["task_id"])
    assert retained is not None
    assert tracked is not None
    assert is_workflow_managed_task(retained)
    assert is_workflow_managed_task(tracked)
    assert task_only["ok"] is True
    assert task_only["workflow_plan_event_id"] == ""
    assert task_only["workflow_plan_warning"] == ""
    assert not any(
        event.type == PLAN_REQUESTED_EVENT
        for event in writer.event_log.read_all()
    )


def test_kanban_create_task_preserves_explicit_direct_execution(
    tmp_path: Path,
) -> None:
    state_dir, writer, service = _service(tmp_path)

    created = _execute(service, writer, "create-task", {
        "title": "Run through the legacy direct scheduler",
        "execution_mode": "direct",
    })

    task = TaskStore(state_dir / "kanban.json").get(created["task_id"])
    assert task is not None
    assert not is_workflow_managed_task(task)


def test_create_task_publishes_plan_with_nested_route_objective(
    tmp_path: Path,
) -> None:
    state_dir, writer, service = _service(tmp_path)
    raw_plan = {
        "question": "Keep this passive or run maintenance?",
        "options": [
            {
                "mode": "defer",
                "label": "No workflow yet",
                "recommended": True,
            },
            {
                "route_id": "delivery:prd:standard",
                "label": "Run standard delivery",
                "parameters": {
                    "objective": "Check repository metadata",
                    "target_root": str(tmp_path),
                },
            },
        ],
    }

    created = _execute(service, writer, "create-task", {
        "title": "Check repository metadata",
        "workflow_plan": raw_plan,
    })

    assert created["workflow_plan_warning"] == ""
    assert created["workflow_plan_event_id"]
    task = TaskStore(state_dir / "kanban.json").get(created["task_id"])
    assert task is not None
    assert is_workflow_managed_task(task)
    plan_event = next(
        event
        for event in writer.event_log.read_all()
        if event.id == created["workflow_plan_event_id"]
    )
    executable = next(
        option
        for option in plan_event.payload["request"]["options"]
        if option.get("submit_action") == "workflow-start"
    )
    assert executable["submit_payload"]["objective"] == (
        "Check repository metadata"
    )
    assert executable["submit_payload"]["parameters"] == {
        "target_root": str(tmp_path),
    }


@pytest.mark.parametrize(
    ("option_id", "expected_route"),
    [
        ("delivery", "delivery:prd:standard"),
        ("research", "research:fixed"),
    ],
)
def test_each_executable_task_workflow_option_requires_approve_then_starts(
    tmp_path: Path,
    monkeypatch,
    option_id: str,
    expected_route: str,
) -> None:
    monkeypatch.setattr(
        "zf.runtime.preflight._probe_provider_auth",
        lambda _backend: (True, "mocked authenticated provider"),
    )
    config_ref = tmp_path / "zf.yaml"
    config_ref.write_text(
        (ROOT / "zf.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "examples").symlink_to(
        ROOT / "examples",
        target_is_directory=True,
    )
    (tmp_path / "skills").symlink_to(
        ROOT / "skills",
        target_is_directory=True,
    )
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    service = ControlledActionService(
        state_dir,
        writer,
        config=load_config(config_ref),
        project_root=tmp_path,
        actor="web",
        source="kanban-agent",
        surface="web",
    )
    workflow_plan = _workflow_plan()
    workflow_plan["options"][0]["parameters"] = {
        "backend": "mock",
        "target_root": str(tmp_path),
    }

    created = _execute(service, writer, "create-task", {
        "title": "Assess the workflow planning model",
        "contract": {
            "behavior": "Compare the model with current runtime behavior.",
            "verification": "Verify a research run can start.",
            "verification_tiers": ["runtime"],
        },
        "workflow_plan": workflow_plan,
        "project_id": "zaofu",
        "conversation_id": "kanban:zaofu",
        "thread_id": "main",
    })

    assert created["ok"] is True
    assert created["workflow_plan_warning"] == ""
    task_id = created["task_id"]
    plan_event = next(
        event
        for event in writer.event_log.read_all()
        if event.id == created["workflow_plan_event_id"]
        and event.type == PLAN_REQUESTED_EVENT
    )
    request = plan_event.payload["request"]
    assert plan_event.task_id == task_id
    assert not any(
        event.type == "workflow.invoke.requested"
        for event in writer.event_log.read_all()
    )

    response = {
        "request_event_id": plan_event.id,
        "request_id": request["request_id"],
        "revision": request["revision"],
        "question_id": request["question_id"],
        "option_id": option_id,
        "answer": option_id,
    }
    proposed = _execute(service, writer, "kanban-plan-apply", {
        "plan_response": response,
    })
    replay = _execute(service, writer, "kanban-plan-apply", {
        "plan_response": response,
    })

    assert proposed["status"] == "proposal_ready"
    assert proposed["proposed_action"] == "workflow-start"
    assert replay["status"] == "proposal_ready"
    events = writer.event_log.read_all()
    proposals = [
        event
        for event in events
        if event.type == "operator.action.proposed"
    ]
    assert len(proposals) == 1
    assert not any(
        event.type == "workflow.invoke.requested"
        for event in events
    )

    proposal = proposals[0].payload["proposal"]
    started = _execute(service, writer, "workflow-start", {
        **proposal["payload"],
        "proposal_event_id": proposals[0].id,
    })
    start_replay = _execute(service, writer, "workflow-start", {
        **proposal["payload"],
        "proposal_event_id": proposals[0].id,
    })

    assert started["ok"] is True, started
    assert started["route_id"] == expected_route
    assert start_replay["status"] == "already_resolved"
    final_events = writer.event_log.read_all()
    invoke = next(
        event
        for event in final_events
        if event.type == "workflow.invoke.requested"
    )
    assert invoke.task_id == task_id
    assert sum(
        event.type == "operator.action.resolved"
        and event.payload.get("proposal_event_id") == proposals[0].id
        for event in final_events
    ) == 1
    assert sum(
        event.type == "workflow.invoke.requested"
        for event in final_events
    ) == 1
    assert TaskStore(state_dir / "kanban.json").get(task_id) is not None


def test_task_workflow_proposal_reject_has_no_invoke_side_effect(
    tmp_path: Path,
) -> None:
    _state_dir, writer, service = _service(tmp_path)
    created = _execute(service, writer, "create-task", {
        "title": "Reject this workflow route",
        "workflow_plan": _workflow_plan(),
    })
    plan_event = next(
        event for event in writer.event_log.read_all()
        if event.id == created["workflow_plan_event_id"]
    )
    request = plan_event.payload["request"]
    proposed = _execute(service, writer, "kanban-plan-apply", {
        "plan_response": {
            "request_event_id": plan_event.id,
            "request_id": request["request_id"],
            "revision": request["revision"],
            "question_id": request["question_id"],
            "option_id": "research",
            "answer": "Research first",
        },
    })
    assert proposed["status"] == "proposal_ready"
    proposal_event = next(
        event for event in writer.event_log.read_all()
        if event.type == "operator.action.proposed"
    )

    dismissed = _execute(service, writer, "kanban-proposal-dismiss", {
        "proposal_event_id": proposal_event.id,
        "reason": "operator rejected the workflow option",
    })

    assert dismissed["ok"] is True, dismissed
    assert not any(
        event.type == "workflow.invoke.requested"
        for event in writer.event_log.read_all()
    )


def test_task_workflow_start_runs_delivery_request_and_submit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "zf.runtime.preflight._probe_provider_auth",
        lambda _backend: (True, "mocked authenticated provider"),
    )
    config_ref = tmp_path / "zf.yaml"
    config_ref.write_text(
        (ROOT / "zf.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "examples").symlink_to(
        ROOT / "examples",
        target_is_directory=True,
    )
    (tmp_path / "skills").symlink_to(ROOT / "skills", target_is_directory=True)
    requirement = tmp_path / "requirement.md"
    requirement.write_text(
        "# Requirement\n\nImplement and verify the requested behavior.\n",
        encoding="utf-8",
    )
    specification = tmp_path / "phase1-spec.md"
    specification.write_text(
        "# Specification\n\nPreserve the approved phase-one contract.\n",
        encoding="utf-8",
    )
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    config = load_config(config_ref)
    task = Task(
        id="TASK-DELIVERY",
        title="Implement the selected delivery route",
        contract=TaskContract(
            behavior="Implement the selected route.",
            verification="Run deterministic tests.",
            validation={"commands": [{
                "id": "route-tests",
                "command": "uv run pytest tests/test_route.py -q --no-cov",
                "tier": "task_non_smoke",
            }]},
            verification_tiers=["runtime"],
            acceptance_criteria=[
                {
                    "id": "AC-ROUTE",
                    "statement": "The selected route is implemented and verified.",
                },
                {
                    "id": "AC-SPEC",
                    "statement": "The approved specification remains authoritative.",
                },
            ],
            source_ref="requirement.md",
            spec_ref="phase1-spec.md",
            handoff_artifacts=["requirement.md", "phase1-spec.md"],
            scope=["src/**"],
            exclusions=["docs/generated/**"],
            explicit_non_goals=["Do not rewrite the approved specification."],
            evidence_contract={
                "execution_owner": "workflow",
                "required_source_outputs": ["implementation-report.md"],
            },
        ),
    )
    TaskStore(state_dir / "kanban.json").add(task)
    service = ControlledActionService(
        state_dir,
        writer,
        config=config,
        project_root=tmp_path,
        actor="web",
        source="kanban-agent",
        surface="web",
    )
    catalog = workflow_route_catalog(service.config)

    result = _execute(service, writer, "workflow-start", {
        "task_id": task.id,
        "task_contract_digest": task_workflow_binding_digest(task),
        "route_id": "delivery:prd:standard",
        "config_digest": catalog["config_digest"],
        "objective": task.title,
        "project_id": "kanban-project",
        "conversation_id": "kanban:kanban-project",
        "thread_id": "delivery-thread",
        "parameters": {
            "backend": "mock",
            "target_root": str(tmp_path),
        },
    })

    assert result["ok"] is True, result
    assert result["route_id"] == "delivery:prd:standard"
    assert result["workflow_request"]["intake_ref"]
    persisted_task = TaskStore(state_dir / "kanban.json").get(task.id)
    assert persisted_task is not None
    contract_event = next(
        event
        for event in writer.event_log.read_all()
        if event.type == "task.contract.update"
        and event.payload.get("source") == "workflow_submit"
    )
    assert contract_event.payload["contract_digest"] == (
        task_workflow_binding_digest(persisted_task)
    )
    input_manifest = json.loads(Path(
        result["workflow_request"]["workflow_input_manifest_ref"]
    ).read_text(encoding="utf-8"))
    assert input_manifest["source_ref"] == str(requirement)
    assert input_manifest["source_refs"]["task_source_ref"] == "requirement.md"
    assert input_manifest["source_refs"]["task_spec_ref"] == "phase1-spec.md"
    task_input_ref = Path(input_manifest["task_input_contract_ref"])
    assert task_input_ref.exists()
    task_input_contract = json.loads(task_input_ref.read_text(encoding="utf-8"))
    assert task_input_contract["source_ref"] == "requirement.md"
    assert task_input_contract["spec_ref"] == "phase1-spec.md"
    assert task_input_contract["verification"] == "Run deterministic tests."
    assert task_input_contract["validation"]["commands"][0]["command"] == (
        "uv run pytest tests/test_route.py -q --no-cov"
    )
    assert task_input_contract["verification_tiers"] == ["runtime"]
    assert task_input_contract["evidence_contract"] == {
        "required_source_outputs": ["implementation-report.md"],
    }
    assert [
        item["id"] for item in task_input_contract["acceptance_criteria"]
    ] == ["AC-ROUTE", "AC-SPEC"]
    assert input_manifest["task_input_contract_digest"]
    request_id = result["workflow_request"]["request_id"]
    request_projection = json.loads(
        (state_dir / "workflow-requests" / f"{request_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert request_projection["origin_binding"] == {
        "schema_version": "workflow-origin-binding.v1",
        "surface": "kanban_agent",
        "source": "kanban-agent",
        "project_id": "kanban-project",
        "channel_id": "",
        "thread_id": "",
        "conversation_id": "kanban:kanban-project",
        "thread_key": "delivery-thread",
    }
    requirement_spec = json.loads(
        Path(request_projection["requirement_spec_ref"]).read_text(
            encoding="utf-8"
        )
    )
    assert requirement_spec["acceptance"] == [
        "The selected route is implemented and verified.",
        "The approved specification remains authoritative.",
    ]
    assert requirement_spec["source_refs"]["task_spec_ref"] == "phase1-spec.md"
    assert requirement_spec["task_input_contract_ref"] == str(task_input_ref)
    invoke = next(
        event
        for event in writer.event_log.read_all()
        if event.type == "workflow.invoke.requested"
    )
    assert invoke.task_id == task.id
    assert invoke.payload["pattern_id"] == "prd-scan"
    assert invoke.payload["origin_binding"] == request_projection[
        "origin_binding"
    ]
    assert invoke.payload["source_refs"]["task_source_ref"] == "requirement.md"
    assert invoke.payload["source_refs"]["task_spec_ref"] == "phase1-spec.md"
    assert any(
        item.get("ref") == "requirement.md"
        or item.get("path") == "requirement.md"
        for item in invoke.payload["artifact_refs"]
        if isinstance(item, dict)
    )
    assert any(
        item.get("ref") == "phase1-spec.md"
        or item.get("path") == "phase1-spec.md"
        for item in invoke.payload["artifact_refs"]
        if isinstance(item, dict)
    )
    assert any(
        item.get("ref") == str(task_input_ref)
        or item.get("path") == str(task_input_ref)
        for item in invoke.payload["artifact_refs"]
        if isinstance(item, dict)
    )


def test_approved_workflow_start_rechecks_current_task_identity(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    task = Task(id="TASK-RACE", title="Original title")
    store = TaskStore(state_dir / "kanban.json")
    store.add(task)
    normalized = {
        "task_id": task.id,
        "task_contract_digest": task_workflow_binding_digest(task),
        "route_id": "research:fixed",
        "objective": "Inspect the Task",
        "parameters": {},
    }

    store.update(task.id, title="Changed after preview")

    with pytest.raises(ValueError, match="changed after preview"):
        prepare_approved_workflow_start(
            state_dir=state_dir,
            normalized=normalized,
            route={"entry_pattern_id": "research"},
            actor="web",
            reason="",
        )

    normalized["task_id"] = "TASK-MISSING"
    with pytest.raises(ValueError, match="does not exist"):
        prepare_approved_workflow_start(
            state_dir=state_dir,
            normalized=normalized,
            route={"entry_pattern_id": "research"},
            actor="web",
            reason="",
        )


def test_plan_selection_rejects_a_changed_task_contract(
    tmp_path: Path,
) -> None:
    state_dir, writer, service = _service(tmp_path)
    created = _execute(service, writer, "create-task", {
        "title": "Original Task",
        "workflow_plan": _workflow_plan(),
    })
    plan_event = next(
        event
        for event in writer.event_log.read_all()
        if event.id == created["workflow_plan_event_id"]
    )
    request = plan_event.payload["request"]
    TaskStore(state_dir / "kanban.json").update(
        created["task_id"],
        title="Changed Task",
    )

    result = _execute(service, writer, "kanban-plan-apply", {
        "plan_response": {
            "request_event_id": plan_event.id,
            "request_id": request["request_id"],
            "revision": request["revision"],
            "question_id": request["question_id"],
            "option_id": "research",
            "answer": "Research first",
        },
    })

    assert result["ok"] is False
    assert result["status"] == "workflow_task_stale"
    assert result["replacement_plan_event_id"]
    assert result["replacement_revision"] == request["revision"] + 1
    events = writer.event_log.read_all()
    pending = pending_kanban_plan_requests(events)
    assert len(pending) == 1
    assert pending[0]["request_event_id"] == (
        result["replacement_plan_event_id"]
    )
    assert pending[0]["revision"] == result["replacement_revision"]
    failure = next(
        event
        for event in reversed(events)
        if event.id == result["event_id"]
    )
    assert failure.payload["actionability"] == "observation"
    assert not event_is_recovery_actionable(
        failure.type,
        failure.payload,
    )
    from zf.runtime.run_manager import _pending_semantic_event_actions

    assert _pending_semantic_event_actions([failure]) == []
    assert not any(
        event.type in {
            "operator.action.proposed",
            "workflow.invoke.requested",
        }
        for event in events
    )


def test_plan_selection_remints_when_the_route_binding_is_stale(
    tmp_path: Path,
) -> None:
    _state_dir, writer, service = _service(tmp_path)
    workflow_plan = _workflow_plan()
    workflow_plan["options"] = [
        workflow_plan["options"][1],
        workflow_plan["options"][2],
    ]
    workflow_plan["options"][0]["parameters"].update({
        "source_ref": "channel-artifacts/ch-prd/prd.md",
        "target_root": ".",
        "source_refs": {
            "channel_prd_digest": "sha256:canonical",
        },
    })
    created = _execute(service, writer, "create-task", {
        "title": "Route-bound Task",
        "workflow_plan": workflow_plan,
    })
    plan_event = next(
        event
        for event in writer.event_log.read_all()
        if event.id == created["workflow_plan_event_id"]
    )
    request = plan_event.payload["request"]
    research_stage = next(
        stage
        for stage in service.config.workflow.stages
        if stage.id == "research-fanout"
    )
    research_stage.id = "research-fanout-v2"

    result = _execute(service, writer, "kanban-plan-apply", {
        "plan_response": {
            "request_event_id": plan_event.id,
            "request_id": request["request_id"],
            "revision": request["revision"],
            "question_id": request["question_id"],
            "option_id": "research",
            "answer": "Research first",
        },
    })

    assert result["ok"] is False
    assert result["status"] == "workflow_route_stale"
    assert result["replacement_plan_event_id"]
    assert result["replacement_revision"] == request["revision"] + 1
    pending = pending_kanban_plan_requests(
        writer.event_log.read_all()
    )
    assert [item["request_event_id"] for item in pending] == [
        result["replacement_plan_event_id"]
    ]
    executable = [
        option
        for option in pending[0]["options"]
        if option.get("submit_action") == "workflow-start"
    ]
    assert executable
    assert all(
        option["submit_payload"]["parameters"]["source_ref"]
        == "channel-artifacts/ch-prd/prd.md"
        and option["submit_payload"]["parameters"]["source_refs"][
            "channel_prd_digest"
        ] == "sha256:canonical"
        for option in executable
    )


def test_create_task_marks_every_requested_workflow_plan_as_workflow_managed(
    tmp_path: Path,
) -> None:
    state_dir, writer, service = _service(tmp_path)
    valid = _execute(service, writer, "create-task", {
        "task_id": "TASK-MANAGED",
        "title": "Run through a selected workflow",
        "workflow_plan": _workflow_plan(),
    })
    invalid = _execute(service, writer, "create-task", {
        "task_id": "TASK-DIRECT",
        "title": "Remain an ordinary task",
        "workflow_plan": {"options": []},
    })

    store = TaskStore(state_dir / "kanban.json")
    managed = store.get(valid["task_id"])
    invalid_plan_task = store.get(invalid["task_id"])
    assert managed is not None
    assert invalid_plan_task is not None
    assert is_workflow_managed_task(managed)
    assert is_workflow_managed_task(invalid_plan_task)
    created = next(
        event
        for event in writer.event_log.read_all()
        if event.type == "task.created" and event.task_id == managed.id
    )
    assert (
        created.payload["task"]["contract"]["evidence_contract"][
            "execution_owner"
        ]
        == "workflow"
    )


def test_create_task_pins_workflow_owner_and_request_before_task_event(
    tmp_path: Path,
) -> None:
    state_dir, writer, service = _service(tmp_path)
    origin = build_workflow_origin_binding(
        source="kanban-agent",
        project_id="test",
        conversation_id="kanban:test",
        thread_key="main",
    )
    request_dir = state_dir / "workflow-requests"
    request_dir.mkdir()
    (request_dir / "REQ-TASK.json").write_text(
        json.dumps({
            "schema_version": "workflow.request.v1",
            "request_id": "REQ-TASK",
            "project_id": "test",
            "kind": "prd",
            "status": "ready",
            "revision": 3,
            "origin_binding": origin,
        }),
        encoding="utf-8",
    )

    result = _execute(service, writer, "create-task", {
        "task_id": "TASK-REQUEST-BOUND",
        "title": "Research before PRD",
        "execution_mode": "workflow",
        "request_id": "REQ-TASK",
        "request_revision": 3,
    })

    assert result["ok"] is True
    task = TaskStore(state_dir / "kanban.json").get(
        "TASK-REQUEST-BOUND"
    )
    assert task is not None
    evidence = task.contract.evidence_contract
    assert evidence["execution_owner"] == "workflow"
    assert evidence["workflow_request_id"] == "REQ-TASK"
    assert evidence["workflow_request_revision"] == 3
    task_event = next(
        event
        for event in writer.event_log.read_all()
        if event.type == "task.created"
        and event.task_id == "TASK-REQUEST-BOUND"
    )
    task_contract = task_event.payload["task"]["contract"]
    assert task_contract["evidence_contract"]["execution_owner"] == "workflow"
    assert not any(
        event.type == PLAN_REQUESTED_EVENT
        and event.task_id == "TASK-REQUEST-BOUND"
        for event in writer.event_log.read_all()
    )


def test_event_watcher_does_not_dispatch_new_workflow_managed_task(
    tmp_path: Path,
) -> None:
    state_dir, writer, service = _service(tmp_path)
    origin = build_workflow_origin_binding(
        source="kanban-agent",
        project_id="test",
        conversation_id="kanban:test",
        thread_key="main",
    )
    request_dir = state_dir / "workflow-requests"
    request_dir.mkdir()
    (request_dir / "REQ-WATCH.json").write_text(
        json.dumps({
            "schema_version": "workflow.request.v1",
            "request_id": "REQ-WATCH",
            "project_id": "test",
            "kind": "prd",
            "status": "ready",
            "revision": 1,
            "origin_binding": origin,
        }),
        encoding="utf-8",
    )
    transport = _RecordingTransport()
    orchestrator = Orchestrator(
        state_dir,
        service.config,
        transport,  # type: ignore[arg-type]
    )

    def consume(line: str) -> None:
        event = writer.event_log.decode_line(line)
        if event is not None and event.type in WAKE_PATTERNS:
            orchestrator.run_once(events=[event])

    watcher = EventWatcher(
        state_dir / "events.jsonl",
        on_event=consume,
        event_log=writer.event_log,
        wake_patterns=list(WAKE_PATTERNS),
    )
    created = _execute(service, writer, "create-task", {
        "task_id": "TASK-WATCH",
        "title": "Research before delivery",
        "execution_mode": "workflow",
        "request_id": "REQ-WATCH",
        "request_revision": 1,
    })

    watcher.poll_once()

    assert created["ok"] is True
    assert transport.sent == []
    assert not any(
        event.type == "task.dispatched"
        and event.task_id == "TASK-WATCH"
        for event in writer.event_log.read_all()
    )
