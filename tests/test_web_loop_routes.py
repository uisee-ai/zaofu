"""Web API tests for loop.v1 routes (doc94)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.loop_closure import append_loop_closure_events
from zf.web.server import create_app


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "feature_list.json").write_text("[]")
    store = TaskStore(sd / "kanban.json")
    store.add(Task(
        id="T1",
        title="gateway",
        status="in_progress",
        assigned_to="dev-1",
        contract=TaskContract(feature_id="F-1", owner_role="dev", wave=1),
    ))
    log = event_log_from_project(sd, config=None, warn=False)
    log.append(ZfEvent(
        type="static_gate.failed",
        id="gate-fail",
        task_id="T1",
        payload={"feature_id": "F-1", "reason": "pytest failed"},
    ))
    log.append(ZfEvent(
        type="task.rework.triage.completed",
        id="triage-gap",
        task_id="T1",
        payload={"feature_id": "F-1", "classification": "evidence_payload_gap"},
    ))
    return sd


@pytest.fixture
def client(state_dir: Path) -> TestClient:
    return TestClient(create_app(state_dir))


def test_loop_projection_endpoint(client: TestClient) -> None:
    response = client.get("/api/projects/default/loops")

    assert response.status_code == 200
    data = response.json()
    assert data["schema_version"] == "loop.v1"
    assert data["summary"]["total"] == 2
    assert data["summary"]["by_kind"]["gate_failure"] == 1
    assert data["summary"]["by_kind"]["missing_evidence"] == 1
    assert data["behaviors"][0]["kind"] == "missing_evidence"
    assert data["loops"][0]["loop_id"].startswith("loop:")


def test_loop_detail_endpoint(client: TestClient) -> None:
    page = client.get("/api/projects/default/loops").json()
    loop_id = next(loop["loop_id"] for loop in page["loops"] if loop["kind"] == "missing_evidence")

    response = client.get(f"/api/projects/default/loops/{loop_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["schema_version"] == "loop-detail.v1"
    assert data["loop"]["loop_id"] == loop_id
    assert data["behaviors"][0]["loop_id"] == loop_id
    assert data["candidates"][0]["loop_id"] == loop_id


def test_loop_detail_unknown_returns_404(client: TestClient) -> None:
    response = client.get("/api/projects/default/loops/missing-loop")

    assert response.status_code == 404


def test_loop_routes_do_not_mutate_kanban(client: TestClient, state_dir: Path) -> None:
    kanban = state_dir / "kanban.json"
    before = (kanban.stat().st_mtime_ns, kanban.read_bytes())

    client.get("/api/projects/default/loops")

    after = (kanban.stat().st_mtime_ns, kanban.read_bytes())
    assert before == after


def test_loop_action_requires_web_token(state_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    client = TestClient(create_app(state_dir))
    page = client.get("/api/projects/default/loops").json()
    candidate = page["candidates"][0]

    response = client.post(
        f"/api/projects/default/loops/{candidate['loop_id']}/actions",
        json={"candidate_id": candidate["candidate_id"]},
    )

    assert response.status_code == 403
    events = event_log_from_project(state_dir, config=None, warn=False).read_all()
    assert "loop.action.requested" not in [event.type for event in events]


def test_loop_action_maps_gate_failure_to_autoresearch_request(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    client = TestClient(create_app(state_dir))
    page = client.get("/api/projects/default/loops").json()
    candidate = next(item for item in page["candidates"] if item["source_kind"] == "gate_failure")

    response = client.post(
        f"/api/projects/default/loops/{candidate['loop_id']}/actions",
        headers={"x-zf-web-token": "test-token", "x-idempotency-key": "loop-gate-1"},
        json={"candidate_id": candidate["candidate_id"]},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "mapped"
    assert body["mapped_event_type"] == "autoresearch.loop.requested"
    events = event_log_from_project(state_dir, config=None, warn=False).read_all()
    by_type = [event.type for event in events]
    assert "loop.action.requested" in by_type
    assert "loop.action.mapped" in by_type
    mapped = next(event for event in events if event.type == "autoresearch.loop.requested")
    assert mapped.payload["proposal_only"] is True
    assert mapped.payload["source_loop_action_id"] == body["action_id"]


def test_loop_action_idempotency_replays_without_duplicate_events(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    client = TestClient(create_app(state_dir))
    page = client.get("/api/projects/default/loops").json()
    candidate = next(item for item in page["candidates"] if item["source_kind"] == "missing_evidence")
    path = f"/api/projects/default/loops/{candidate['loop_id']}/actions"
    payload = {"candidate_id": candidate["candidate_id"]}
    headers = {"x-zf-web-token": "test-token", "x-idempotency-key": "loop-missing-1"}

    first = client.post(path, headers=headers, json=payload)
    second = client.post(path, headers=headers, json=payload)

    assert first.status_code == 202
    assert second.status_code == 202
    events = event_log_from_project(state_dir, config=None, warn=False).read_all()
    assert [event.type for event in events].count("loop.action.requested") == 1
    assert second.json()["idempotency"]["status"] == "replayed"


def test_loop_action_maps_stuck_worker_to_restart_repair(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(
        type="worker.stuck",
        id="stuck-worker",
        actor="dev-1",
        task_id="T1",
        payload={"feature_id": "F-1", "role_instance": "dev-1"},
    ))
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    client = TestClient(create_app(state_dir))
    page = client.get("/api/projects/default/loops").json()
    candidate = next(item for item in page["candidates"] if item["source_kind"] == "stuck_worker")

    response = client.post(
        f"/api/projects/default/loops/{candidate['loop_id']}/actions",
        headers={"x-zf-web-token": "test-token", "x-idempotency-key": "loop-worker-1"},
        json={"candidate_id": candidate["candidate_id"]},
    )

    assert response.status_code == 202
    events = event_log_from_project(state_dir, config=None, warn=False).read_all()
    repair = next(event for event in events if event.type == "repair.action.requested")
    assert repair.payload["kind"] == "restart_worker"
    assert repair.payload["worker_id"] == "dev-1"
    assert repair.payload["source_loop_action_id"] == response.json()["action_id"]


def test_loop_action_maps_replan_candidate_to_contract_eval(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(
        type="replan.proposal.created",
        id="replan-proposal",
        task_id="T1",
        payload={
            "feature_id": "F-1",
            "candidate_task_map_ref": "artifacts/F-1/task-map-v2.json",
        },
    ))
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    client = TestClient(create_app(state_dir))
    page = client.get("/api/projects/default/loops").json()
    candidate = next(item for item in page["candidates"] if item["source_kind"] == "replan")

    response = client.post(
        f"/api/projects/default/loops/{candidate['loop_id']}/actions",
        headers={"x-zf-web-token": "test-token", "x-idempotency-key": "loop-replan-1"},
        json={"candidate_id": candidate["candidate_id"]},
    )

    assert response.status_code == 202
    events = event_log_from_project(state_dir, config=None, warn=False).read_all()
    replan = next(event for event in events if event.type == "replan.contract_eval.requested")
    assert replan.payload["candidate_task_map_ref"] == "artifacts/F-1/task-map-v2.json"
    assert replan.payload["proposal_only"] is True


def test_loop_learning_promotion_requires_web_token(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    client = TestClient(create_app(state_dir))
    learning = _seed_learning(client, state_dir)

    response = client.post(
        f"/api/projects/default/loops/{learning['loop_id']}/learning/{learning['learning_id']}/promotions",
        json={"target": "runbook_note"},
    )

    assert response.status_code == 403
    events = event_log_from_project(state_dir, config=None, warn=False).read_all()
    assert "loop.learning.promotion.requested" not in [event.type for event in events]


def test_loop_learning_promotion_materializes_runtime_proposal(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    client = TestClient(create_app(state_dir))
    learning = _seed_learning(client, state_dir)

    response = client.post(
        f"/api/projects/default/loops/{learning['loop_id']}/learning/{learning['learning_id']}/promotions",
        headers={"x-zf-web-token": "test-token", "x-idempotency-key": "promote-learning-1"},
        json={"target": "runbook_note"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "materialized"
    assert body["proposal_ref"].startswith("loop/promotions/")
    assert (state_dir / body["proposal_ref"]).exists()
    events = event_log_from_project(state_dir, config=None, warn=False).read_all()
    by_type = [event.type for event in events]
    assert "loop.learning.promotion.requested" in by_type
    assert "loop.learning.promotion.materialized" in by_type

    projection = client.get("/api/projects/default/loops").json()
    row = next(item for item in projection["learning"] if item["learning_id"] == learning["learning_id"])
    assert row["promotion_status"] == "materialized"
    assert row["promotion_target"] == "runbook_note"
    assert row["promotion_ref"] == body["proposal_ref"]


def test_loop_closed_loop_action_verify_learn_promote_flow(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    client = TestClient(create_app(state_dir))
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(
        type="worker.stuck",
        id="stuck-worker-flow",
        actor="dev-1",
        task_id="T1",
        payload={"feature_id": "F-1", "role_instance": "dev-1"},
    ))
    page = client.get("/api/projects/default/loops").json()
    candidate = next(item for item in page["candidates"] if item["source_kind"] == "stuck_worker")

    action_response = client.post(
        f"/api/projects/default/loops/{candidate['loop_id']}/actions",
        headers={"x-zf-web-token": "test-token", "x-idempotency-key": "closed-loop-flow-1"},
        json={"candidate_id": candidate["candidate_id"]},
    )

    assert action_response.status_code == 202
    action_body = action_response.json()
    assert action_body["status"] == "mapped"
    assert action_body["mapped_event_type"] == "repair.action.requested"
    repair_requested = next(
        event for event in log.read_all()
        if event.type == "repair.action.requested"
        and event.payload.get("source_loop_action_id") == action_body["action_id"]
    )
    repair_applied = ZfEvent(
        type="repair.action.applied",
        id="repair-applied-flow",
        task_id="T1",
        payload={
            "action_id": repair_requested.payload["action_id"],
            "kind": "restart_worker",
            "source_loop_action_id": action_body["action_id"],
            "reason": "worker restarted",
        },
        causation_id=action_body["mapped_event_id"],
    )
    log.append(repair_applied)
    recovered = ZfEvent(
        type="worker.stuck.recovered",
        id="worker-recovered-flow",
        actor="dev-1",
        task_id="T1",
        payload={"feature_id": "F-1"},
        causation_id=repair_applied.id,
    )
    log.append(recovered)
    emitted = append_loop_closure_events(
        events=log.read_all(),
        source_event=recovered,
        writer=EventWriter(log),
        state_dir=state_dir,
        project_id="default",
    )

    assert [event.type for event in emitted] == [
        "loop.verify.requested",
        "loop.verify.completed",
        "loop.learning.materialized",
    ]
    projection = client.get("/api/projects/default/loops").json()
    verification = next(item for item in projection["verifications"] if item["source_action_id"] == action_body["action_id"])
    assert verification["result"] == "passed"
    assert verification["missing_evidence"] == []
    learning = next(item for item in projection["learning"] if item["verification_id"] == verification["verification_id"])
    assert learning["artifact_kind"] == "runbook_note"

    promotion_response = client.post(
        f"/api/projects/default/loops/{learning['loop_id']}/learning/{learning['learning_id']}/promotions",
        headers={"x-zf-web-token": "test-token", "x-idempotency-key": "closed-loop-promote-1"},
        json={"target": "runbook_note"},
    )

    assert promotion_response.status_code == 202
    promotion_body = promotion_response.json()
    assert promotion_body["status"] == "materialized"
    assert (state_dir / promotion_body["proposal_ref"]).exists()
    promoted = client.get("/api/projects/default/loops").json()
    promoted_learning = next(item for item in promoted["learning"] if item["learning_id"] == learning["learning_id"])
    assert promoted_learning["promotion_status"] == "materialized"
    assert promoted_learning["promotion_ref"] == promotion_body["proposal_ref"]


def _seed_learning(client: TestClient, state_dir: Path) -> dict:
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(
        type="worker.stuck",
        id="stuck-worker",
        actor="dev-1",
        task_id="T1",
        payload={"feature_id": "F-1", "role_instance": "dev-1"},
    ))
    page = client.get("/api/projects/default/loops").json()
    candidate = next(item for item in page["candidates"] if item["source_kind"] == "stuck_worker")
    loop_id = candidate["loop_id"]
    candidate_id = candidate["candidate_id"]
    log.append(ZfEvent(type="loop.action.requested", id="loop-action", task_id="T1", payload={
        "action_id": "la-1",
        "loop_id": loop_id,
        "candidate_id": candidate_id,
        "suggested_action": "inspect_worker_liveness",
        "source_kind": "stuck_worker",
        "evidence_refs": ["stuck-worker"],
    }))
    log.append(ZfEvent(type="repair.action.requested", id="repair-request", task_id="T1", causation_id="loop-action", payload={
        "action_id": "ra-1",
        "kind": "restart_worker",
        "source_loop_action_id": "la-1",
    }))
    log.append(ZfEvent(type="loop.action.mapped", id="loop-mapped", task_id="T1", causation_id="loop-action", payload={
        "action_id": "la-1",
        "loop_id": loop_id,
        "candidate_id": candidate_id,
        "suggested_action": "inspect_worker_liveness",
        "mapped_event_id": "repair-request",
        "mapped_event_type": "repair.action.requested",
        "mapped_action": "restart_worker",
        "downstream_action_id": "ra-1",
    }))
    log.append(ZfEvent(type="repair.action.applied", id="repair-applied", task_id="T1", causation_id="repair-request", payload={
        "action_id": "ra-1",
        "kind": "restart_worker",
        "source_loop_action_id": "la-1",
        "reason": "worker restarted",
    }))
    log.append(ZfEvent(type="worker.stuck.recovered", id="worker-recovered", task_id="T1", actor="dev-1", payload={
        "feature_id": "F-1",
    }))
    projection = client.get("/api/projects/default/loops").json()
    return projection["learning"][0]
