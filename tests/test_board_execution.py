"""Execution summaries are scoped to the current task, run and attempt."""

from copy import deepcopy

from fastapi.testclient import TestClient

from tests.dashboard_projection_helpers import ready_get
from tests.test_handoff_pipeline import _setup
from tests.test_task_pipeline_projection import _event, _operation_events
from zf.core.task.schema import Task, TaskExecutionBinding
from zf.core.task.store import TaskStore
from zf.web.projections.tasks import _kanban
from zf.web.server import create_app


def _dispatch(operation, attempt, dispatch, worker):
    return _event(f"dispatch-{attempt}", "task.pipeline.stage.dispatched", payload={
        "task_map_generation": "map-g1", "operation_generation": 1, "workflow_run_id": "run-1",
        "operation_id": operation, "task_pipeline_stage": "verify", "role_instance": worker,
        "attempt_id": attempt, "dispatch_id": dispatch,
    })


def test_board_scopes_members_progress_and_expired_bindings(tmp_path):
    state, config, task, writer = _setup(tmp_path, "verify")
    parent = _dispatch("op-a-impl-1", "attempt-a-1", "current", "impl-2")
    writer.append(parent)
    # A second concurrent verification operation for this task has an independent binding.
    for row in deepcopy(_operation_events()):
        row.id += "-peer"
        row.payload.update(operation_id="peer", task_pipeline_stage="verify", role_instance="peer-worker")
        if row.type == "workflow.operation.started":
            row.payload["active_attempt_id"] = "attempt-peer"
        writer.append(row)
    writer.append(_dispatch("peer", "attempt-peer", "peer-dispatch", "peer-worker"))
    # A retry receipt without a matching current attempt cannot replace the active binding.
    writer.append(_dispatch("peer", "old-attempt", "old-dispatch", "other-worker"))
    receipt = _event("current-result", "workflow.call.result.admitted",
                     payload={"workflow_run_id": "run-1", "dispatch_id": "current", "semantic_verdict": "passed",
                              "task_map_generation": "map-g1", "admission_status": "admitted",
                              "control_result_ref": {"ref": "refs/result.json", "sha256": "a" * 64}})
    writer.append(receipt)
    old_receipt = deepcopy(receipt)
    old_receipt.id = "prior-generation-result"
    old_receipt.payload.update(dispatch_id="old-dispatch", operation_generation=0, semantic_verdict="failed")
    writer.append(old_receipt)
    retry_receipt = deepcopy(receipt)
    retry_receipt.id = "retired-attempt-result"
    retry_receipt.payload.update(operation_id="peer", attempt_id="old-attempt", dispatch_id="old-dispatch")
    writer.append(retry_receipt)
    writer.append(_event("late-result", "dev.build.done", payload={"workflow_run_id":"run-1", "dispatch_id":"old-dispatch"}))
    writer.append(_event("late-heartbeat", "worker.heartbeat", payload={"workflow_run_id":"run-1", "dispatch_id":"current"}))
    store = TaskStore(state / "kanban.json")
    store.add(Task(id="SEPARATE", title=task.id, assigned_to="prospective-owner", skills_required=["writer"]))
    with TestClient(create_app(state, config=config, project_root=tmp_path)) as client:
        response = ready_get(client, "/api/projects/default/board-cards")
        response.raise_for_status()
        cards = {r["id"]: r["task_card"]["execution"] for r in response.json()["tasks"]}
        active = cards[task.id]
        assert active["workflow_run_id"] == "run-1"
        assert (active["stage"], active["state"]) == ("verify", "running")
        assert {(m["instance_id"], m["attempt_id"]) for m in active["active_instances"]} == {
            ("impl-2", "attempt-a-1"), ("peer-worker", "attempt-peer"),
        }
        assert active["milestone"] == {"at": receipt.ts, "label": "已接纳阶段结果 · passed"}
        assert active["last_output"] == {**active["milestone"], "event_id": receipt.id}
        assert not cards["SEPARATE"]["active_instances"]
        assert not cards["SEPARATE"]["workflow_run_id"]
        detail = ready_get(client, f"/api/projects/default/tasks/{task.id}")
        detail.raise_for_status()
        assert detail.json()["execution"] == active
    store.update(task.id, execution_binding=TaskExecutionBinding(workflow_run_id="next-run"))
    rebound = next(row["task_card"]["execution"] for row in _kanban(state, config) if row["id"] == task.id)
    assert rebound["workflow_run_id"] == "next-run"
    assert not rebound["active_instances"] and not rebound["milestone"]


def test_board_wait_and_archive_keep_scope(tmp_path):
    state, config, task, writer = _setup(tmp_path)
    store = TaskStore(state / "kanban.json")
    store.update(task.id, status="blocked", blocked_reason="waiting for test account")
    row = _kanban(state, config)[0]["task_card"]["execution"]
    assert row["state"] == "blocked" and row["waiting_for"] == "waiting for test account"
    assert not row["active_instances"]
    store.update(task.id, status="cancelled")
    with TestClient(create_app(state, config=config, project_root=tmp_path)) as client:
        response = ready_get(client, "/api/projects/default/board-cards")
        archived = response.json()["archive_tasks"][0]["task_card"]["execution"]
        assert archived["state"] == "cancelled" and not archived["active_instances"]
        assert archived["workflow_run_id"] == "run-1" and not archived["waiting_for"]


def test_card_run_detail_reads_only_observed_run_before_archive(tmp_path):
    state, config, _, writer = _setup(tmp_path)
    other = _event("other-event", "task.started", payload={"workflow_run_id": "other-run"})
    other.correlation_id = "other-run"
    writer.append(other)
    unknown = _event("other-comment", "worker.heartbeat", payload={"workflow_run_id": "unobserved"})
    unknown.origin = "agent"
    writer.append(unknown)
    with TestClient(create_app(state, config=config, project_root=tmp_path)) as client:
        url = "/api/projects/default/runs/"
        observed = client.get(url + "run-1")
        observed.raise_for_status()
        body = observed.json()
        assert body["run"]["run_id"] == "run-1" and body["manifest"]["source"] == "events.jsonl"
        ids = {e["id"] for e in body["events"]}
        assert "generation" in ids and not {"other-event", "other-comment"} & ids
        assert client.get(url + "unobserved").status_code == 404

def test_board_and_detail_follow_actual_dependency_state(tmp_path):
    state, config, _, _ = _setup(tmp_path)
    store = TaskStore(state / "kanban.json")
    store.add(Task(id="producer", title="Prepare test account"))
    store.add(Task(id="consumer", title="Run acceptance", blocked_by=["producer"]))
    with TestClient(create_app(state, config=config, project_root=tmp_path)) as client:
        for status in ("backlog", "cancelled"):
            if status == "cancelled":
                store.update("producer", status="cancelled")
            cards = _kanban(state, config)
            card = next(row["task_card"]["execution"] for row in cards if row["id"] == "consumer")
            detail = client.get("/api/projects/default/tasks/consumer").json()["execution"]
            assert detail == card
            assert card["waiting_for"] == "producer"
