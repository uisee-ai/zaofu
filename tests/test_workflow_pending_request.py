"""A failed preflight can be corrected without adopting another Task's Request."""

from pathlib import Path

import pytest

from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.workflow_start import WorkflowStartService
from zf.runtime.workflow_requests import load_workflow_request
from tests.test_control_actions_workflow_request import _service, _execute


def _pending(tmp_path: Path):
    service, log = _service(tmp_path)
    from zf.core.config.loader import load_config

    config_path = tmp_path / "zf.yaml"
    config_path.write_text(
        config_path.read_text().replace("  lanes: 1", "  topology: fanout\n  lanes: 1")
    )
    with config_path.open("a") as stream:
        stream.write(
            "  workflow:\n    kind_routes:\n      issue: {pattern_id: issue-triage, default_tier: standard}\n"
        )
    service.config = load_config(config_path)
    task = Task(
        id="TASK-PENDING",
        title="Repair pending start",
        contract=TaskContract(
            behavior="Correct an issue.",
            verification="Run a focused regression.",
            verification_tiers=["runtime"],
            owner_instance="fix-lane-0",
            spec_skip_reason="isolated pending Request regression",
        ),
    )
    TaskStore(service.state_dir / "kanban.json").add(task)
    start = WorkflowStartService(
        service.state_dir, service.config, project_root=tmp_path
    )
    preview = start.preview(
        {
            "task_id": task.id,
            "route_id": "delivery:issue:standard",
            "objective": "Correct an issue.",
            "parameters": {"backend": "mock", "allow_missing_env": True},
        },
        require_bindings=False,
    )
    assert preview["ok"], preview
    from zf.runtime.workflow_start_inputs import prepare_approved_workflow_start

    prepared = prepare_approved_workflow_start(
        state_dir=service.state_dir,
        normalized=preview["payload"],
        route=preview["route"],
        actor="operator",
        reason="regression",
    )
    proposed = _execute(
        service, "workflow-request", {**prepared.common_payload, "kind": "issue"}
    )
    assert proposed["ok"], proposed
    request = load_workflow_request(service.state_dir, proposed["request_id"])
    payload = {
        "task_id": task.id,
        "route_id": "delivery:issue:standard",
        "objective": "Correct an issue.",
        "request_id": request["request_id"],
        "request_revision": request["revision"],
        "parameters": {"backend": "mock", "allow_missing_env": True},
    }
    return service, log, start, task, request, payload


def test_pending_request_preview_and_controlled_start(tmp_path: Path):
    service, log, start, task, request, payload = _pending(tmp_path)
    before = len(log.read_all())
    preview = start.preview(payload, require_bindings=False)
    assert preview["ok"], preview
    assert len(log.read_all()) == before
    assert (
        not TaskStore(service.state_dir / "kanban.json")
        .get(task.id)
        .execution_binding.request_id
    )
    result = _execute(service, "workflow-start", preview["payload"])
    assert result["ok"], result
    assert (
        TaskStore(service.state_dir / "kanban.json")
        .get(task.id)
        .execution_binding.request_id
        == request["request_id"]
    )
    assert sum(e.type == "workflow.invoke.requested" for e in log.read_all()) == 1


@pytest.mark.parametrize(
    "change",
    [
        "task",
        "contract",
        "revision",
        "origin",
        "accepted",
        "requested",
        "tampered",
        "task_run",
    ],
)
def test_pending_request_rejects_unproven_adoption(tmp_path: Path, change: str):
    service, log, start, task, request, payload = _pending(tmp_path)
    store = TaskStore(service.state_dir / "kanban.json")
    if change == "task":
        task.id = "TASK-OTHER"
        store.add(task)
        payload["task_id"] = task.id
    elif change == "contract":
        task.contract.behavior = "Different contract"
        store.update(task.id, contract=task.contract)
    elif change == "revision":
        payload["request_revision"] += 1
    elif change == "origin":
        payload["channel_id"] = "other-channel"
    elif change == "task_run":
        from dataclasses import replace

        store.update(
            task.id,
            execution_binding=replace(
                task.execution_binding,
                workflow_run_id="RUN-OTHER",
            ),
        )
    elif change == "tampered":
        path = Path(request["requirement_spec_ref"])
        if not path.is_absolute():
            path = service.state_dir / path
        path.write_text(path.read_text() + " ")
    else:
        from zf.core.events import EventWriter, ZfEvent

        EventWriter(log).append(
            ZfEvent(
                type="workflow.invoke."
                + ("requested" if change == "requested" else "accepted"),
                actor="kernel",
                payload={
                    "request_id": request["request_id"],
                    "workflow_run_id": "RUN-OTHER",
                },
            )
        )
    before = len(log.read_all())
    result = start.preview(payload, require_bindings=False)
    assert not result["ok"], result
    assert len(log.read_all()) == before
    assert not store.get(task.id).execution_binding.request_id
