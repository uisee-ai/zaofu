from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from zf.core.config.schema import (
    FanoutChildConfig,
    RoleConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.schema import TaskContract
from zf.core.task.store import TaskStore
from zf.web.server import create_app


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "kanban.json").write_text("[]", encoding="utf-8")
    (sd / "feature_list.json").write_text("[]", encoding="utf-8")
    EventLog(sd / "events.jsonl").append(ZfEvent(type="loop.started", actor="zf-cli"))
    return sd


def test_create_and_update_task_priority_through_web_action(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))

    created = client.post(
        "/api/actions/create-task",
        headers={"x-zf-web-token": "test-token"},
        json={
            "title": "Priority task",
            "priority": 1,
            "assigned_to": "dev",
            "skills_required": ["frontend"],
            "contract": {"behavior": "render board", "verification": "typecheck"},
        },
    )

    assert created.status_code == 201
    task_id = created.json()["task_id"]
    task = created.json()["result"]["task"]
    assert task["priority"] == 1
    assert task["contract"]["behavior"] == "render board"

    updated = client.post(
        "/api/actions/update-task",
        headers={"x-zf-web-token": "test-token"},
        json={"task_id": task_id, "priority": 0, "blocked_by": ["TASK-BLOCK"]},
    )

    assert updated.status_code == 200
    assert updated.json()["result"]["task"]["priority"] == 0
    assert updated.json()["result"]["task"]["blocked_by"] == ["TASK-BLOCK"]

    snapshot = client.get("/api/snapshot").json()
    projected = next(item for item in snapshot["tasks"] if item["id"] == task_id)
    assert projected["priority"] == 0


def test_snapshot_and_state_project_archived_done_tasks_separately(
    state_dir: Path,
) -> None:
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-ARCH-DONE", title="archived done", status="in_progress"))
    store.update("TASK-ARCH-DONE", status="done")
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))

    state = client.get("/api/state").json()
    snapshot = client.get("/api/snapshot").json()

    assert all(item["id"] != "TASK-ARCH-DONE" for item in state["tasks"])
    assert all(item["id"] != "TASK-ARCH-DONE" for item in snapshot["tasks"])
    state_archived = next(
        item for item in state["archive_tasks"]
        if item["id"] == "TASK-ARCH-DONE"
    )
    snapshot_archived = next(
        item for item in snapshot["archive_tasks"]
        if item["id"] == "TASK-ARCH-DONE"
    )
    assert state_archived["kanban_column"] == "done"
    assert snapshot_archived["kanban_column"] == "done"
    assert state_archived["terminal"] is True
    assert state_archived["terminal_outcome"] == "success"


def test_snapshot_archived_cancelled_keeps_cancelled_terminal_outcome(
    state_dir: Path,
) -> None:
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-ARCH-CANCEL", title="archived cancel", status="in_progress"))
    store.update("TASK-ARCH-CANCEL", status="cancelled")
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))

    snapshot = client.get("/api/snapshot").json()
    archived = next(
        item for item in snapshot["archive_tasks"]
        if item["id"] == "TASK-ARCH-CANCEL"
    )

    assert archived["status"] == "cancelled"
    assert archived["kanban_column"] == "done"
    assert archived["terminal"] is True
    assert archived["terminal_outcome"] == "cancelled"


def test_worker_runtime_action_queues_request_event(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))

    response = client.post(
        "/api/actions/worker-drain",
        headers={"x-zf-web-token": "test-token"},
        json={"instance_id": "dev-auto-0001", "reason": "test"},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert response.json()["event_type"] == "worker.drain.requested"
    types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
    assert "worker.drain.requested" in types
    assert "runtime.action.completed" in types
    assert "web.action.completed" in types


def test_workflow_graph_projection_endpoint_is_read_only(state_dir: Path):
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))

    response = client.get("/api/workflow/graph")

    assert response.status_code == 200
    data = response.json()
    assert {"nodes", "edges", "overlays", "counts"}.issubset(data)
    assert data["counts"]["stages"] == 0
    assert data["compiled_graph"] == {}
    assert data["workflow_node_runs"] == {}


def test_workflow_graph_projects_star_orchestrator_loop(state_dir: Path):
    config = ZfConfig(
        roles=[
            RoleConfig(name="orchestrator", backend="claude-code"),
            RoleConfig(name="dev", backend="codex"),
            RoleConfig(name="review-security", backend="codex"),
            RoleConfig(name="review-architecture", backend="codex"),
        ],
    )
    config.workflow.stages = [
        WorkflowStageConfig(
            id="candidate-review",
            trigger="candidate.ready",
            topology="fanout_reader",
            roles=["review-security", "review-architecture"],
            children=[
                FanoutChildConfig(role="review-security", scope="security"),
                FanoutChildConfig(role="review-architecture", scope="architecture"),
            ],
        )
    ]
    client = TestClient(create_app(state_dir, config=config, project_root=state_dir.parent))

    response = client.get("/api/workflow/graph")

    assert response.status_code == 200
    data = response.json()
    node_ids = {node["id"] for node in data["nodes"]}
    edge_kinds = {edge["kind"] for edge in data["edges"]}
    assert "role:orchestrator" in node_ids
    assert "aggregate:candidate-review" in node_ids
    assert "fanout_child" in edge_kinds
    assert "return_to_orchestrator" in edge_kinds
    stage = next(node for node in data["nodes"] if node["id"] == "candidate-review")
    assert stage["pattern_id"] == "candidate-review"
    assert stage["barrier"]["mode"] == "wait_for_all"


def test_execution_patterns_endpoint_projects_configured_stage(state_dir: Path):
    config = ZfConfig(
        roles=[
            RoleConfig(name="review-security", backend="codex"),
            RoleConfig(name="review-architecture", backend="codex"),
        ],
    )
    config.workflow.stages = [
        WorkflowStageConfig(
            id="candidate-review",
            trigger="candidate.ready",
            topology="fanout_reader",
            roles=["review-security", "review-architecture"],
        )
    ]
    client = TestClient(create_app(state_dir, config=config, project_root=state_dir.parent))

    response = client.get("/api/execution-patterns")

    assert response.status_code == 200
    data = response.json()
    assert data["patterns"][0]["pattern_id"] == "candidate-review"
    assert data["patterns"][0]["kind"] == "fanout_reader"


def test_channel_message_endpoint_uses_token_gated_action_path(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    # 2026-06-10 review P1-7: a raw instance_id only reaches a worker pane
    # when it backs a channel member with message permission.
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    writer.emit("channel.created", payload={"channel_id": "ch-zaofu", "name": "# zaofu"})
    writer.emit(
        "channel.member.added",
        payload={
            "channel_id": "ch-zaofu",
            "member_id": "dev-worker",
            "member_type": "provider-agent",
            "backing_worker_session_id": "dev-1",
        },
    )
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))

    rejected = client.post(
        "/api/channels/ch-zaofu/messages",
        json={"text": "hello"},
    )
    accepted = client.post(
        "/api/channels/ch-zaofu/messages",
        headers={"x-zf-web-token": "test-token"},
        json={
            "thread_id": "th-plan",
            "text": "请 QA 看下",
            "mentions": ["qa"],
            "instance_id": "dev-1",
        },
    )
    non_member = client.post(
        "/api/channels/ch-zaofu/messages",
        headers={"x-zf-web-token": "test-token"},
        json={
            "thread_id": "th-plan",
            "text": "direct dispatch attempt",
            "instance_id": "judge-1",
        },
    )

    assert rejected.status_code == 403
    assert accepted.status_code == 202
    assert non_member.status_code == 202
    events = EventLog(state_dir / "events.jsonl").read_all()
    types = [event.type for event in events]
    assert "channel.message.posted" in types
    reply_targets = [
        event.payload.get("instance_id")
        for event in events
        if event.type == "worker.reply.requested"
    ]
    assert reply_targets == ["dev-1"]
    assert any(
        event.type == "channel.route.blocked"
        and event.payload.get("reason") == "worker_not_channel_member"
        and event.payload.get("instance_id") == "judge-1"
        for event in events
    )
    detail = client.get("/api/channels/ch-zaofu").json()
    assert any(m["text"] == "请 QA 看下" for m in detail["messages"])


def test_channel_workflow_request_records_workflow_invoke_event(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    config = ZfConfig()
    config.workflow.stages = [
        WorkflowStageConfig(
            id="review-wave",
            trigger="candidate.ready",
            topology="fanout_reader",
            roles=["review"],
        )
    ]
    TaskStore(state_dir / "kanban.json").add(Task(id="TASK-1", title="Run review"))
    client = TestClient(create_app(state_dir, config=config, project_root=state_dir.parent))

    response = client.post(
        "/api/channels/ch-zaofu/workflow-request",
        headers={"x-zf-web-token": "test-token"},
        json={
            "thread_id": "th-plan",
            "task_id": "TASK-1",
            "pattern_id": "review-wave",
            "reason": "release risk",
            "expected_output": "review report",
        },
    )

    assert response.status_code == 202
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert any(event.type == "workflow.invoke.requested" for event in events)
    assert any(event.type == "channel.state_update.posted" for event in events)


def test_workflow_graph_projects_role_trigger_edges_without_stages(state_dir: Path):
    config = ZfConfig(
        roles=[
            RoleConfig(
                name="orchestrator",
                instance_id="orchestrator",
                backend="codex",
                publishes=["task.assigned"],
            ),
            RoleConfig(
                name="dev",
                instance_id="dev-1",
                backend="codex",
                triggers=["task.assigned"],
                publishes=["dev.build.done"],
            ),
            RoleConfig(
                name="review",
                instance_id="review-1",
                backend="codex",
                triggers=["dev.build.done"],
            ),
        ],
    )
    client = TestClient(create_app(state_dir, config=config, project_root=state_dir.parent))

    data = client.get("/api/workflow/graph").json()

    edges = {
        (edge["from"], edge["kind"], edge["to"], edge.get("trigger_event", ""))
        for edge in data["edges"]
    }
    assert ("role:orchestrator", "assign", "role:dev-1", "task.assigned") in edges
    assert ("role:orchestrator", "trigger", "role:dev-1", "task.assigned") not in edges
    assert ("role:dev-1", "trigger", "role:review-1", "dev.build.done") in edges
    assert data["counts"]["stages"] == 0
    assert data["counts"]["roles"] == 3


def test_agents_skip_duplicate_orchestrator_and_ignore_terminal_active_task(state_dir: Path):
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-DONE", title="done", status="in_progress", assigned_to="dev-1"))
    store.update("TASK-DONE", status="done")
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="dev.build.done",
        actor="dev-1",
        task_id="TASK-DONE",
        payload={"summary": "completed"},
    ))
    config = ZfConfig(
        roles=[
            RoleConfig(name="orchestrator", instance_id="orchestrator", backend="codex"),
            RoleConfig(name="dev", instance_id="dev-1", backend="codex"),
        ],
    )
    client = TestClient(create_app(state_dir, config=config, project_root=state_dir.parent))

    agents = client.get("/api/agents").json()

    ids = [agent["instance_id"] for agent in agents]
    assert ids.count("orchestrator") == 1
    by_id = {agent["instance_id"]: agent for agent in agents}
    assert by_id["dev-1"]["active_task"] == ""


def test_snapshot_projects_workflow_handoffs_to_kanban_columns(state_dir: Path):
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-REVIEW", title="review", status="in_progress", assigned_to="review"))
    store.add(Task(id="TASK-GATE", title="static gate", status="in_progress", assigned_to="review"))
    store.add(Task(id="TASK-VERIFY", title="verify", status="in_progress", assigned_to="test"))
    store.add(Task(id="TASK-JUDGE", title="judge", status="in_progress", assigned_to="judge"))
    store.add(Task(id="TASK-DONE", title="done", status="in_progress", assigned_to="judge"))
    store.update("TASK-DONE", status="done")
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="dev.build.done", actor="dev", task_id="TASK-REVIEW"))
    log.append(ZfEvent(type="dev.build.done", actor="dev", task_id="TASK-GATE"))
    log.append(ZfEvent(type="static_gate.passed", actor="zf-cli", task_id="TASK-GATE"))
    log.append(ZfEvent(type="review.approved", actor="review", task_id="TASK-VERIFY"))
    log.append(ZfEvent(type="test.passed", actor="test", task_id="TASK-JUDGE"))
    log.append(ZfEvent(type="judge.passed", actor="judge", task_id="TASK-DONE"))
    config = ZfConfig(
        roles=[
            RoleConfig(name="judge", backend="mock", publishes=["judge.passed", "judge.failed"]),
        ],
    )
    client = TestClient(create_app(state_dir, config=config, project_root=state_dir.parent))

    snapshot = client.get("/api/snapshot").json()

    active = {task["id"]: task for task in snapshot["tasks"]}
    archived = {task["id"]: task for task in snapshot["archive_tasks"]}
    assert active["TASK-REVIEW"]["status"] == "in_progress"
    assert active["TASK-REVIEW"]["phase"] == "build_done"
    assert active["TASK-REVIEW"]["kanban_column"] == "testing"
    assert active["TASK-REVIEW"]["kanban_column_label"] == "Verify"
    assert active["TASK-REVIEW"]["kanban_column_reason"] == "role:review;phase:build_done"
    assert active["TASK-REVIEW"]["kanban_column_badges"] == []
    assert active["TASK-REVIEW"]["workflow_phase"] == "verify"
    assert active["TASK-REVIEW"]["impl_exit_gate_state"] == "pending"
    assert active["TASK-GATE"]["phase"] == "static_gate_passed"
    assert active["TASK-GATE"]["workflow_phase"] == "verify"
    assert active["TASK-GATE"]["impl_exit_gate_state"] == "passed"
    assert active["TASK-VERIFY"]["status"] == "in_progress"
    assert active["TASK-VERIFY"]["phase"] == "review_approved"
    assert active["TASK-VERIFY"]["kanban_column"] == "testing"
    assert active["TASK-VERIFY"]["verify_state"] == "partial"
    assert active["TASK-JUDGE"]["status"] == "in_progress"
    assert active["TASK-JUDGE"]["phase"] == "test_passed"
    assert active["TASK-JUDGE"]["kanban_column"] == "testing"
    assert active["TASK-JUDGE"]["verify_state"] == "passed"
    assert active["TASK-JUDGE"]["judge_state"] == "pending"
    assert archived["TASK-DONE"]["status"] == "done"
    assert archived["TASK-DONE"]["kanban_column"] == "done"
    assert archived["TASK-DONE"]["kanban_column_label"] == "Done"
    assert archived["TASK-DONE"]["kanban_column_reason"] == "status:done"
    assert archived["TASK-DONE"]["workflow_phase"] == "done"


def test_snapshot_projects_candidate_fanout_workflow_context(state_dir: Path) -> None:
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-CANDIDATE",
        title="candidate task",
        status="in_progress",
        assigned_to="dev-lane-0",
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="dev.build.done",
        actor="dev-lane-0",
        task_id="TASK-CANDIDATE",
    ))
    log.append(ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        payload={
            "stage_id": "cj-min-slice-implementation",
            "status": "completed",
            "completed_task_ids": ["TASK-CANDIDATE"],
        },
    ))
    log.append(ZfEvent(
        type="fanout.child.failed",
        actor="zf-cli",
        payload={
            "stage_id": "cj-min-candidate-verification",
            "child_id": "verify-lane-0-task-candidate",
            "upstream_task_id": "TASK-CANDIDATE",
            "reason": "typecheck failed",
        },
    ))
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))

    snapshot = client.get("/api/snapshot").json()
    task = next(item for item in snapshot["tasks"] if item["id"] == "TASK-CANDIDATE")

    assert task["impl_exit_gate_state"] == "passed"
    assert task["verify_state"] == "failed"
    assert task["workflow_phase"] == "impl"
    assert task["workflow_projection"]["rework_reason"] == "typecheck failed"
    labels = {badge["label"] for badge in task["workflow_badges"]}
    assert "impl gate passed" in labels
    assert "verify failed" in labels


def test_snapshot_ignores_superseded_plan_blocked_after_replan(
    state_dir: Path,
) -> None:
    store = TaskStore(state_dir / "kanban.json")
    task_map_ref = str(state_dir / "artifacts" / "fanout-new" / "task_map.json")
    old_task_map_ref = str(state_dir / "artifacts" / "fanout-old" / "task_map.json")
    store.add(Task(
        id="TASK-REPLAN",
        title="replanned task",
        status="in_progress",
        assigned_to="dev-lane-0",
        contract=TaskContract(
            feature_id="CANGJIE-R5-NEW",
            evidence_contract={"source_refs": {"task_map": task_map_ref}},
        ),
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="zaofu.refactor.plan.blocked",
        actor="zf-cli",
        payload={
            "pdd_id": "CANGJIE-R5-NEW",
            "feature_id": "CANGJIE-R5-NEW",
            "fanout_id": "fanout-old",
            "stage_id": "cangjie-refactor-plan",
            "task_map_ref": old_task_map_ref,
            "status": "failed",
        },
    ))
    log.append(ZfEvent(
        type="zaofu.refactor.plan.ready",
        actor="zf-cli",
        payload={
            "pdd_id": "CANGJIE-R5-NEW",
            "feature_id": "CANGJIE-R5-NEW",
            "fanout_id": "fanout-new",
            "stage_id": "cangjie-refactor-plan",
            "task_map_ref": task_map_ref,
            "status": "completed",
        },
    ))
    log.append(ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        payload={
            "pdd_id": "CANGJIE-R5-NEW",
            "feature_id": "CANGJIE-R5-NEW",
            "task_map_ref": task_map_ref,
            "task_ids": ["TASK-REPLAN"],
        },
    ))
    log.append(ZfEvent(
        type="fanout.started",
        actor="zf-cli",
        payload={
            "pdd_id": "CANGJIE-R5-NEW",
            "feature_id": "CANGJIE-R5-NEW",
            "fanout_id": "fanout-impl",
            "stage_id": "cangjie-slice-implementation",
            "task_map_ref": task_map_ref,
        },
    ))
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))

    snapshot = client.get("/api/snapshot").json()
    task = next(item for item in snapshot["tasks"] if item["id"] == "TASK-REPLAN")

    assert task["status"] == "in_progress"
    assert task["kanban_column"] == "in_progress"
    assert task["verify_state"] != "failed"
    assert task["kanban_column_reason"] != "workflow_failure:zaofu.refactor.plan.blocked"
    assert "verify failed" not in task["kanban_column_badges"]


def test_snapshot_projects_simplified_kanban_keeps_old_statuses_visible(state_dir: Path):
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-BACKLOG", title="backlog", status="backlog"))
    store.add(Task(id="TASK-READY", title="ready", status="ready"))
    store.add(Task(id="TASK-REVIEW-STATUS", title="review status", status="review"))
    store.add(Task(id="TASK-TESTING-STATUS", title="testing status", status="testing"))
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))

    snapshot = client.get("/api/snapshot").json()

    active = {task["id"]: task for task in snapshot["tasks"]}
    assert active["TASK-BACKLOG"]["status"] == "backlog"
    assert active["TASK-BACKLOG"]["kanban_column"] == "ready"
    assert active["TASK-READY"]["kanban_column"] == "ready"
    assert active["TASK-REVIEW-STATUS"]["kanban_column"] == "testing"
    assert active["TASK-TESTING-STATUS"]["kanban_column"] == "testing"


def test_snapshot_projects_fanout_lane_affinity_and_progress_badges(state_dir: Path):
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-FANOUT", title="fanout child", status="in_progress", assigned_to="dev-lane-0"))
    fanout_dir = state_dir / "fanouts" / "fanout-1"
    fanout_dir.mkdir(parents=True)
    (fanout_dir / "manifest.json").write_text(json.dumps({
        "fanout_id": "fanout-1",
        "children": [
            {"child_id": "child-1", "task_id": "TASK-FANOUT", "status": "completed"},
            {"child_id": "child-2", "task_id": "TASK-OTHER", "status": "running"},
        ],
    }), encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="worker.dispatched",
        actor="orchestrator",
        task_id="TASK-FANOUT",
        payload={
            "fanout_id": "fanout-1",
            "child_id": "child-1",
            "lane_id": "lane0",
            "affinity_tag": "api",
            "assignment_strategy": "affinity_stage_slots",
        },
    ))
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))

    snapshot = client.get("/api/snapshot").json()

    task = next(item for item in snapshot["tasks"] if item["id"] == "TASK-FANOUT")
    assert task["fanout"]["fanout_id"] == "fanout-1"
    assert task["fanout"]["lane_id"] == "lane0"
    assert task["fanout"]["affinity_tag"] == "api"
    assert task["fanout"]["assignment_strategy"] == "affinity_stage_slots"
    assert task["fanout"]["progress"]["done"] == 1
    assert task["fanout"]["progress"]["total"] == 2
    assert {"kind": "fanout", "label": "fanout 1/2", "tone": "warn"} in task["evidence_badges"]


def test_snapshot_prefers_latest_active_fanout_child_over_stale_verify(
    state_dir: Path,
) -> None:
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-REWORK",
        title="rework child",
        status="in_progress",
        assigned_to="dev-lane-0",
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="dev.build.done", actor="dev-lane-0", task_id="TASK-REWORK"))
    log.append(ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-old",
            "stage_id": "impl",
            "status": "completed",
            "completed_task_ids": ["TASK-REWORK"],
        },
    ))
    fanout_dir = state_dir / "fanouts" / "fanout-rework"
    fanout_dir.mkdir(parents=True)
    (fanout_dir / "manifest.json").write_text(json.dumps({
        "fanout_id": "fanout-rework",
        "stage_id": "impl",
        "status": "started",
        "children": [
            {
                "child_id": "dev-lane-0-TASK-REWORK",
                "task_id": "TASK-REWORK",
                "role_instance": "dev-lane-0",
                "status": "dispatched",
                "run_id": "run-rework",
                "lane_id": "lane0",
                "affinity_tag": "gateway",
                "assignment_strategy": "affinity_stage_slots",
            },
            {
                "child_id": "dev-lane-1-TASK-OTHER",
                "task_id": "TASK-OTHER",
                "role_instance": "dev-lane-1",
                "status": "queued",
            },
        ],
    }), encoding="utf-8")
    log.append(ZfEvent(
        type="fanout.started",
        actor="zf-cli",
        payload={"fanout_id": "fanout-rework", "stage_id": "impl"},
    ))
    log.append(ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-rework",
            "child_id": "dev-lane-0-TASK-REWORK",
            "task_id": "TASK-REWORK",
        },
    ))
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))

    snapshot = client.get("/api/snapshot").json()
    task = next(item for item in snapshot["tasks"] if item["id"] == "TASK-REWORK")

    assert task["kanban_column"] == "in_progress"
    assert task["phase"] == "fanout_child_running"
    assert task["workflow_phase"] == "impl"
    assert task["impl_exit_gate_state"] == "pending"
    assert task["verify_state"] == "waiting"
    assert task["fanout"]["fanout_id"] == "fanout-rework"
    assert task["fanout"]["child_status"] == "dispatched"
    assert task["fanout"]["progress"]["done"] == 0
    labels = {badge["label"] for badge in task["workflow_badges"]}
    assert "fanout dispatched" in labels
    assert "verify pending" not in labels


def test_agent_view_workers_projects_runtime_only_autoscale_worker(state_dir: Path):
    import yaml

    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-AUTO",
        title="autoscaled worker task",
        status="in_progress",
        assigned_to="dev-auto-0001",
    ))
    (state_dir / "role_sessions.yaml").write_text(yaml.safe_dump({
        "project_root": str(state_dir.parent),
        "roles": {
            "dev-auto-0001": "11111111-1111-1111-1111-111111111111",
        },
        "instance_meta": {
            "dev-auto-0001": {
                "origin": "autoscale",
                "parent_role": "dev",
                "role_kind": "writer",
                "backend": "codex",
                "spawned_at": "2026-05-12T00:00:00+00:00",
            },
        },
    }), encoding="utf-8")
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="worker.context.critical",
        actor="dev-auto-0001",
        task_id="TASK-AUTO",
        payload={
            "ratio": 0.91,
            "summary": "context usage is critical",
        },
    ))
    config = ZfConfig(
        roles=[
            RoleConfig(name="dev", instance_id="dev-1", backend="codex"),
        ],
    )
    client = TestClient(create_app(state_dir, config=config, project_root=state_dir.parent))

    data = client.get("/api/views/workers").json()

    by_id = {worker["instance_id"]: worker for worker in data["workers"]}
    assert "dev-1" in by_id
    assert "dev-auto-0001" in by_id
    assert by_id["dev-1"]["origin"] == "static"
    assert by_id["dev-auto-0001"]["origin"] == "autoscale"
    assert by_id["dev-auto-0001"]["parent_role"] == "dev"
    assert by_id["dev-auto-0001"]["task_id"] == "TASK-AUTO"
    assert by_id["dev-auto-0001"]["attention_state"] == "context_critical"
    assert by_id["dev-auto-0001"]["context_usage_ratio"] == 0.91
    dev_group = next(group for group in data["role_groups"] if group["role"] == "dev")
    assert dev_group["count"] == 2
    assert dev_group["static_count"] == 1
    assert dev_group["autoscale_count"] == 1
    assert dev_group["attention_count"] == 1
    assert data["attention"][0]["instance_id"] == "dev-auto-0001"

    snapshot = client.get("/api/snapshot").json()
    assert snapshot["agent_view"]["selected_instance_id"] == "dev-auto-0001"


def test_agent_view_projects_queue_waiting_and_exception_owner(state_dir: Path):
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-READY",
        title="ready dev work",
        status="backlog",
        assigned_to="dev",
    ))
    store.add(Task(
        id="TASK-BLOCKED",
        title="blocked dev work",
        status="in_progress",
        assigned_to="dev-1",
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="task.assigned",
        actor="zf-cli",
        task_id="TASK-READY",
        payload={"assignee": "dev", "role": "dev"},
    ))
    log.append(ZfEvent(
        type="dev.blocked",
        actor="dev-1",
        task_id="TASK-BLOCKED",
        payload={"reason": "missing API contract"},
    ))
    config = ZfConfig(
        roles=[
            RoleConfig(name="dev", instance_id="dev-1", backend="codex"),
            RoleConfig(name="orchestrator", backend="codex"),
        ],
    )
    client = TestClient(create_app(state_dir, config=config, project_root=state_dir.parent))

    data = client.get("/api/views/workers").json()

    queue = data["queue_waiting"]
    assert queue["schema_version"] == "agent-queue.v1"
    assert queue["summary"]["waiting_tasks"] == 2
    assert queue["summary"]["needs_attention"] == 1
    by_role = queue["by_role"]
    assert by_role["dev"]["waiting_task_count"] == 1
    assert by_role["dev"]["ready_task_count"] == 1
    assert by_role["orchestrator"]["needs_attention_count"] == 1
    blocked = next(row for row in queue["tasks"] if row["task_id"] == "TASK-BLOCKED")
    assert blocked["waiting_role"] == "orchestrator"
    assert blocked["blocking_event"] == "dev.blocked"
    assert blocked["next_expected_event"] == "orchestrator.decision"
    orchestrator_group = next(
        group for group in data["role_groups"] if group["role"] == "orchestrator"
    )
    assert orchestrator_group["queue"]["needs_attention_count"] == 1


def test_agent_view_clears_context_warning_after_recycle_to_idle(state_dir: Path):
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="worker.context.warning",
        actor="dev-1",
        payload={"ratio": 0.61, "summary": "context usage is high"},
    ))
    log.append(ZfEvent(
        type="worker.recycled",
        actor="dev-1",
        payload={"role": "dev", "new_session": ""},
    ))
    log.append(ZfEvent(
        type="worker.state.changed",
        actor="dev-1",
        payload={
            "from": "recycling",
            "to": "idle",
            "reason": "recycle complete (new session None)",
        },
    ))
    config = ZfConfig(
        roles=[
            RoleConfig(name="dev", instance_id="dev-1", backend="codex"),
        ],
    )
    client = TestClient(create_app(state_dir, config=config, project_root=state_dir.parent))

    data = client.get("/api/views/workers").json()

    worker = next(item for item in data["workers"] if item["instance_id"] == "dev-1")
    assert worker["attention_state"] == "idle"
    assert worker["context_usage_ratio"] is None
    assert "recycle_after_task" not in worker["allowed_actions"]
    assert data["attention"] == []


def test_runs_index_falls_back_to_task_event_log_when_no_archive(state_dir: Path):
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="judge.passed",
        actor="judge-1",
        task_id="TASK-LOG",
        correlation_id="trace-log",
        payload={"result": "passed"},
    ))
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))

    runs = client.get("/api/runs").json()
    detail = client.get("/api/runs/event-log-latest").json()
    events = client.get("/api/runs/event-log-latest/events").json()
    task_runs = client.get("/api/tasks/TASK-LOG/runs").json()

    assert runs["runs"][0]["run_id"] == "event-log-latest"
    assert runs["runs"][0]["summary"]["task_ids"] == ["TASK-LOG"]
    assert detail["run"]["projection"] == "event_log_fallback"
    assert any(item["task_id"] == "TASK-LOG" for item in events["items"])
    assert task_runs["runs"][0]["run_id"] == "event-log-latest"


def test_recent_events_exposes_autopilot_proposal_for_triage(state_dir: Path):
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="autopilot.proposal.created",
        actor="autopilot",
        task_id="TASK-AUTO",
        payload={
            "proposal_id": "ap-test",
            "dedupe_key": "stale_task:TASK-AUTO:evt-1",
            "kind": "stale_task",
            "severity": "medium",
            "title": "TASK-AUTO looks stale",
            "reason": "no task-scoped event for 24 hours",
            "source": "autopilot",
            "mode": "proposal_only",
            "action_proposal": {
                "action": "update-task",
                "payload": {
                    "task_id": "TASK-AUTO",
                    "status": "blocked",
                    "blocked_reason": "autopilot stale scan",
                },
            },
        },
    ))
    client = TestClient(create_app(state_dir, project_root=state_dir.parent))

    response = client.get("/api/views/recent?limit=10")

    assert response.status_code == 200
    events = response.json()
    assert any(event["type"] == "autopilot.proposal.created" for event in events)
