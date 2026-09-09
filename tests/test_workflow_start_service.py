from __future__ import annotations

import json
from pathlib import Path

from zf.cli.main import main
from zf.core.config.loader import load_config
from zf.core.config.schema import WorkflowStageConfig
from zf.core.events import EventLog, EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.task_workflow_plans import task_workflow_binding_digest
from zf.runtime.workflow_anchor import (
    bind_workflow_request_to_task,
    mark_workflow_managed_task,
)
from zf.runtime.workflow_intake import build_flow_intake
from zf.runtime.workflow_origin import (
    build_workflow_origin_binding,
    workflow_origin_digest,
)
from zf.runtime.workflow_start import WorkflowStartService
from zf.web.operator_contract import canonical_action
from zf.web.proposal_extraction import default_validate_payload


ROOT = Path(__file__).resolve().parents[1]


def _project(tmp_path: Path) -> tuple[Path, Path]:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "zf.yaml").write_text(
        (ROOT / "zf.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (project_root / "examples").symlink_to(
        ROOT / "examples",
        target_is_directory=True,
    )
    (project_root / "skills").symlink_to(
        ROOT / "skills",
        target_is_directory=True,
    )
    state_dir = project_root / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(
        Task(
            id="TASK-WORKFLOW-START",
            title="Compare workflow start boundaries",
            contract=TaskContract(
                behavior="Start the selected research route.",
                verification="Observe one canonical workflow invoke.",
            ),
        )
    )
    return project_root, state_dir


def test_service_routes_preview_and_propose_are_side_effect_bounded(
    tmp_path: Path,
) -> None:
    project_root, state_dir = _project(tmp_path)
    config = load_config(project_root / "zf.yaml")
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    service = WorkflowStartService(state_dir, config)

    routes = service.routes(task_id="TASK-WORKFLOW-START")
    preview = service.preview(
        {
            "task_id": "TASK-WORKFLOW-START",
            "route_id": "research:fixed",
            "objective": "Compare workflow start boundaries.",
        },
        require_bindings=False,
        origin="coding_agent",
    )

    assert routes["ok"] is True
    assert routes["config_digest"].startswith("sha256:")
    assert routes["task_contract_digest"].startswith("sha256:")
    assert any(
        route["route_id"] == "research:fixed"
        for route in routes["routes"]
    )
    assert preview["ok"] is True
    assert preview["payload"]["task_contract_digest"] == (
        routes["task_contract_digest"]
    )
    assert preview["payload"]["config_digest"] == routes["config_digest"]
    assert log.read_all() == []

    proposed = service.propose(
        writer,
        preview["payload"],
        actor="coding-agent",
        origin="coding_agent",
    )
    replay = service.propose(
        writer,
        {**preview["payload"], "origin": "web"},
        actor="web",
        origin="web",
    )

    assert proposed["status"] == "proposal_ready"
    assert proposed["replayed"] is False
    assert replay["proposal_event_id"] == proposed["proposal_event_id"]
    assert replay["replayed"] is True
    proposal_events = [
        event
        for event in log.read_all()
        if event.type == "operator.action.proposed"
    ]
    assert len(proposal_events) == 1
    assert proposal_events[0].payload["proposal"]["action"] == (
        "workflow-start"
    )
    assert not any(
        event.type == "workflow.invoke.requested"
        for event in log.read_all()
    )


def test_intake_classification_ignores_parent_directory_keywords(
    tmp_path: Path,
) -> None:
    fixture_root = tmp_path / "refactor-fixture"
    source = fixture_root / "prd.md"
    source.parent.mkdir()
    source.write_text(
        "构建一个 todo 产品，包含新增和完成任务。\n",
        encoding="utf-8",
    )

    result = build_flow_intake(
        kind="auto",
        source_ref=str(source),
        output=fixture_root / "docs" / "intake" / "todo.md",
        request_id="wfint-parent-path",
    )

    assert result["effective_kind"] == "prd"


def test_service_rejects_missing_route_and_stale_bindings(
    tmp_path: Path,
) -> None:
    project_root, state_dir = _project(tmp_path)
    config = load_config(project_root / "zf.yaml")
    service = WorkflowStartService(state_dir, config)
    ready = service.preview(
        {
            "task_id": "TASK-WORKFLOW-START",
            "route_id": "research:fixed",
            "objective": "Compare workflow start boundaries.",
        },
        require_bindings=False,
    )
    assert ready["ok"] is True

    missing_route = service.preview(
        {
            **ready["payload"],
            "route_id": "general:not-registered",
        },
        require_bindings=True,
    )
    TaskStore(state_dir / "kanban.json").update(
        "TASK-WORKFLOW-START",
        title="Changed contract binding",
    )
    stale_task = service.preview(
        ready["payload"],
        require_bindings=True,
    )
    changed_task = TaskStore(state_dir / "kanban.json").get(
        "TASK-WORKFLOW-START"
    )
    assert changed_task is not None
    stale_config = service.preview(
        {
            **ready["payload"],
            "task_contract_digest": task_workflow_binding_digest(
                changed_task
            ),
            "config_digest": "sha256:stale",
        },
        require_bindings=True,
    )

    assert missing_route["status"] == "workflow_route_unavailable"
    assert stale_task["status"] == "workflow_task_stale"
    assert stale_config["status"] == "workflow_route_unavailable"


def test_channel_prd_task_marks_unbound_research_route_ineligible(
    tmp_path: Path,
) -> None:
    project_root, state_dir = _project(tmp_path)
    config = load_config(project_root / "zf.yaml")
    store = TaskStore(state_dir / "kanban.json")
    task = store.get("TASK-WORKFLOW-START")
    assert task is not None
    task.contract.source_mode = "channel_prd"
    task.contract.source_ref = "channel-artifacts/ch-prd/prd.md"
    task.contract.source_revision = "1"
    task.contract.evidence_contract = {
        "channel_id": "ch-prd",
        "thread_id": "main",
        "channel_member_id": "product_pm",
        "leader_revision": 1,
        "prd_revision": 1,
        "source_digest": "sha256:canonical",
    }
    store.update(task.id, contract=task.contract)
    service = WorkflowStartService(state_dir, config)

    routes = service.routes(task_id=task.id)
    preview = service.preview(
        {
            "task_id": task.id,
            "route_id": "research:fixed",
            "objective": "Research the confirmed PRD.",
        },
        require_bindings=False,
    )

    research = next(
        route for route in routes["routes"]
        if route["route_id"] == "research:fixed"
    )
    assert research["available"] is False
    assert research["task_eligibility"] == "blocked"
    assert preview["status"] == "workflow_route_ineligible"
    assert "Workflow Request binding" in preview["reason"]


def test_channel_prd_task_binds_lineage_for_general_route(
    tmp_path: Path,
) -> None:
    project_root, state_dir = _project(tmp_path)
    config = load_config(project_root / "zf.yaml")
    store = TaskStore(state_dir / "kanban.json")
    task = store.get("TASK-WORKFLOW-START")
    assert task is not None
    task.contract.source_mode = "channel_prd"
    task.contract.source_ref = "channel-artifacts/ch-prd/prd.md"
    task.contract.source_revision = "3"
    task.contract.evidence_contract = {
        "channel_id": "ch-prd",
        "thread_id": "main",
        "prd_revision": 3,
        "channel_prd_digest": "sha256:canonical",
        "readiness_ref": "channel-artifacts/ch-prd/readiness.md",
        "readiness_digest": "sha256:ready",
    }
    store.update(task.id, contract=task.contract)
    config.workflow.stages.append(
        WorkflowStageConfig(
            id="scope",
            trigger="workflow.invoke.requested",
            topology="single_reader",
            roles=["source_researcher"],
        )
    )
    service = WorkflowStartService(state_dir, config)

    preview = service.preview(
        {
            "task_id": task.id,
            "route_id": "general:scope",
            "objective": "Scope the confirmed Channel PRD.",
        },
        require_bindings=False,
    )

    assert preview["ok"] is True
    assert preview["payload"]["task_input_binding"]
    source_refs = preview["payload"]["parameters"]["source_refs"]
    assert source_refs["channel_prd_ref"] == (
        "channel-artifacts/ch-prd/prd.md"
    )
    assert source_refs["channel_prd_digest"] == "sha256:canonical"
    assert source_refs["channel_prd_revision"] == "3"


def test_delivery_route_rejects_incomplete_task_contract_before_invoke(
    tmp_path: Path,
) -> None:
    project_root, state_dir = _project(tmp_path)
    config = load_config(project_root / "zf.yaml")
    service = WorkflowStartService(
        state_dir,
        config,
        project_root=project_root,
    )

    preview = service.preview(
        {
            "task_id": "TASK-WORKFLOW-START",
            "route_id": "delivery:prd:standard",
            "objective": "Deliver the incomplete Task.",
        },
        require_bindings=False,
    )

    assert preview["ok"] is False
    assert preview["status"] == "task_contract_invalid"
    assert "verification_tiers" in preview["reason"]
    assert "owner_role or owner_instance" in preview["reason"]
    assert EventLog(state_dir / "events.jsonl").read_all() == []


def test_service_uses_request_origin_and_rejects_target_override(
    tmp_path: Path,
) -> None:
    project_root, state_dir = _project(tmp_path)
    config = load_config(project_root / "zf.yaml")
    origin = build_workflow_origin_binding(
        source="kanban-agent",
        project_id=config.project.name,
        channel_id="ch-product",
        thread_id="scope",
    )
    request_dir = state_dir / "workflow-requests"
    request_dir.mkdir()
    (request_dir / "REQ-START.json").write_text(
        json.dumps({
            "schema_version": "workflow.request.v1",
            "request_id": "REQ-START",
            "project_id": config.project.name,
            "kind": "prd",
            "status": "ready",
            "revision": 2,
            "origin_binding": origin,
        }),
        encoding="utf-8",
    )
    store = TaskStore(state_dir / "kanban.json")
    task = store.get("TASK-WORKFLOW-START")
    assert task is not None
    task = mark_workflow_managed_task(task)
    task = bind_workflow_request_to_task(
        task,
        request_id="REQ-START",
        request_revision=2,
        origin_binding_digest=workflow_origin_digest(origin),
    )
    store.update(task.id, contract=task.contract)
    service = WorkflowStartService(state_dir, config)

    ready = service.preview(
        {
            "task_id": task.id,
            "route_id": "research:fixed",
            "objective": "Research product scope.",
        },
        require_bindings=False,
    )
    mismatch = service.preview(
        {
            **ready["payload"],
            "channel_id": "ch-other",
        },
        require_bindings=True,
    )

    assert ready["ok"] is True
    assert ready["payload"]["request_id"] == "REQ-START"
    assert ready["payload"]["request_revision"] == 2
    assert ready["payload"]["origin_binding"] == origin
    assert ready["payload"]["channel_id"] == "ch-product"
    assert ready["payload"]["thread_id"] == "scope"
    assert mismatch["status"] == "origin_binding_mismatch"


def test_cli_routes_propose_apply_and_replay_without_web(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    project_root, state_dir = _project(tmp_path)
    monkeypatch.chdir(project_root)
    for key in ("ZF_PROJECT_ROOT", "ZF_STATE_DIR"):
        monkeypatch.delenv(key, raising=False)

    assert main([
        "workflow",
        "routes",
        "--task",
        "TASK-WORKFLOW-START",
        "--format",
        "json",
    ]) == 0
    routes = json.loads(capsys.readouterr().out)
    assert routes["task_id"] == "TASK-WORKFLOW-START"
    assert EventLog(state_dir / "events.jsonl").read_all() == []

    assert main([
        "workflow",
        "start",
        "--preview",
        "--task",
        "TASK-WORKFLOW-START",
        "--route",
        "research:fixed",
        "--objective",
        "Compare workflow start boundaries.",
        "--format",
        "json",
    ]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["status"] == "preview"
    assert EventLog(state_dir / "events.jsonl").read_all() == []

    assert main([
        "workflow",
        "start",
        "--propose",
        "--task",
        "TASK-WORKFLOW-START",
        "--route",
        "research:fixed",
        "--objective",
        "Compare workflow start boundaries.",
        "--format",
        "json",
    ]) == 0
    proposed = json.loads(capsys.readouterr().out)
    assert proposed["status"] == "proposal_ready"
    assert not any(
        event.type == "workflow.invoke.requested"
        for event in EventLog(state_dir / "events.jsonl").read_all()
    )

    unconfigured_args = [
        "workflow",
        "start",
        "--apply",
        "--proposal-event-id",
        proposed["proposal_event_id"],
        "--authorization-ref",
        "operator:test",
        "--authorization-token",
        "workflow-secret",
        "--format",
        "json",
    ]
    assert main(unconfigured_args) == 3
    unconfigured = json.loads(capsys.readouterr().out)
    assert unconfigured["status"] == "authorization_not_configured"

    monkeypatch.setenv("ZF_WORKFLOW_ACTION_TOKEN", "workflow-secret")
    assert main([
        "workflow",
        "start",
        "--apply",
        "--proposal-event-id",
        proposed["proposal_event_id"],
        "--authorization-ref",
        "operator:test",
        "--format",
        "json",
    ]) == 3
    unauthorized = json.loads(capsys.readouterr().out)
    assert unauthorized["status"] == "authorization_required"

    invalid_args = [
        "workflow",
        "start",
        "--apply",
        "--proposal-event-id",
        proposed["proposal_event_id"],
        "--authorization-ref",
        "operator:test",
        "--authorization-token",
        "wrong-secret",
        "--format",
        "json",
    ]
    assert main(invalid_args) == 3
    invalid = json.loads(capsys.readouterr().out)
    assert invalid["status"] == "authorization_invalid"

    apply_args = [
        "workflow",
        "start",
        "--apply",
        "--proposal-event-id",
        proposed["proposal_event_id"],
        "--authorization-ref",
        "operator:test",
        "--authorization-token",
        "workflow-secret",
        "--format",
        "json",
    ]
    assert main(apply_args) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["ok"] is True
    assert applied["action"] == "workflow-start"

    assert main(apply_args) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["status"] == "already_resolved"
    assert replay["task_id"] == "TASK-WORKFLOW-START"
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert sum(
        event.type == "workflow.invoke.requested"
        for event in events
    ) == 1
    assert sum(
        event.type == "operator.action.resolved"
        for event in events
    ) == 1
    resolved = next(
        event
        for event in events
        if event.type == "operator.action.resolved"
    )
    assert resolved.task_id == "TASK-WORKFLOW-START"
    assert "workflow-secret" not in (state_dir / "events.jsonl").read_text(
        encoding="utf-8"
    )


def test_workflow_runtime_has_no_cli_reverse_dependency() -> None:
    for relative in (
        "src/zf/runtime/control_actions_workflow_request.py",
        "src/zf/runtime/workflow_delivery.py",
        "src/zf/runtime/workflow_intake.py",
        "src/zf/runtime/workflow_preflight.py",
        "src/zf/runtime/workflow_start.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "from zf.cli" not in source
        assert "import zf.cli" not in source


def test_surface_aliases_use_the_plan_gated_workflow_action() -> None:
    assert canonical_action("workflow.start") == "workflow-start"
    assert canonical_action("task.workflow.start") == "workflow-start"
    assert canonical_action("workflow.route.start") == "workflow-start"
    assert "task_workflow Plan" in default_validate_payload(
        "workflow-start",
        {"task_id": "TASK-1", "route_id": "research:fixed"},
    )
