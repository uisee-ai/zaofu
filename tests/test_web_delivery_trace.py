"""Web API tests for delivery-trace endpoints (doc 68 S3 / doc 65 P1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.web.projections import read_model
from zf.web.server import create_app


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "feature_list.json").write_text("[]")
    store = TaskStore(sd / "kanban.json")
    store.add(Task(id="T1", title="schema", status="done", assigned_to="dev-1",
                   contract=TaskContract(feature_id="F-1", owner_role="dev", wave=1)))
    store.add(Task(id="T2", title="router", status="in_progress", assigned_to="dev-2",
                   blocked_by=["T1"],
                   contract=TaskContract(feature_id="F-1", owner_role="dev", wave=2)))
    log = event_log_from_project(sd, config=None, warn=False)
    log.append(ZfEvent(type="loop.started", actor="zf-cli"))
    log.append(ZfEvent(type="dev.build.done", id="e-build", task_id="T1"))
    artifacts = sd / "artifacts" / "F-1"
    artifacts.mkdir(parents=True)
    (artifacts / "task_map.json").write_text(json.dumps({
        "schema_version": "task-map.v1", "feature_id": "F-1",
        "tasks": [
            {"task_id": "T1", "title": "schema", "owner_role": "dev", "wave": 1},
            {"task_id": "T2", "title": "router", "owner_role": "dev", "wave": 2, "blocked_by": ["T1"]},
        ],
    }))
    return sd


@pytest.fixture
def client(state_dir: Path) -> TestClient:
    return TestClient(create_app(state_dir))


def _assert_complete_truncation_metadata(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith("_truncated"):
                prefix = key.removesuffix("_truncated")
                total = value[f"{prefix}_total"]
                included = value[f"{prefix}_included"]
                omitted = value[f"{prefix}_omitted"]
                assert total == included + omitted
                assert item is (omitted > 0)
            _assert_complete_truncation_metadata(item)
    elif isinstance(value, list):
        for item in value:
            _assert_complete_truncation_metadata(item)


def test_delivery_trace_endpoint(client: TestClient):
    r = client.get("/api/projects/default/delivery-traces/F-1")
    assert r.status_code == 200
    data = r.json()
    assert data["schema_version"] == "delivery-trace.v1"
    assert data["feature_id"] == "F-1"
    assert "refresh_scope" not in data
    assert data["status"] == "in_progress"
    assert data["execution_graph"]["task_count"] == 2
    assert data["execution_graph"]["done_count"] == 1
    assert data["workflow_spine"]["schema_version"] == "workflow-spine.v1"
    assert data["workflow_trace"]["schema_version"] == "workflow-trace.v1"
    assert data["workflow_trace"]["diagnostics"][0]["kind"] == "workflow_config_missing"
    assert data["task_flow"]["schema_version"] == "delivery-task-flow.v1"
    assert data["task_flow"]["metrics"]["task_total"] == 2
    assert data["run_groups"][0]["schema_version"] == "delivery-run-group.v1"
    assert data["trace"]["schema_version"] == "delivery-run-trace.v1"
    assert data["closed_loop"]["schema_version"] == "delivery-closed-loop.v1"
    assert data["cursor"]["schema_version"] == "delivery-cursor.v1"
    assert data["deltas"] == []
    assert data["related_loop_ids"] == []
    assert data["related_loop_count"] == 0
    assert data["thick_trace"]["schema_version"] == "delivery-thick-trace.v1"
    assert data["goal_coverage_graph"]["schema_version"] == "goal-coverage-graph.v1"
    assert data["goal_coverage_graph"]["summary"]["mandatory_claims"] >= 1
    assert data["thick_trace"]["graph"]["node_count"] >= 2
    assert data["thick_trace"]["spans"]
    assert any(node["node_id"] == "task:T1" for node in data["closed_loop"]["nodes"])
    assert any(edge["kind"] == "blocked_by" for edge in data["closed_loop"]["edges"])


def test_delivery_trace_cache_version_ignores_legacy_payload(
    client: TestClient,
    state_dir: Path,
):
    first = client.get("/api/projects/default/delivery-traces/F-1")
    assert first.status_code == 200
    source_seq = read_model.current_projected_seq(state_dir, config=None)
    assert source_seq > 0
    read_model.set_cached_projection(
        state_dir,
        "delivery-trace:v2:default:F-1:-",
        kind="delivery-trace",
        source_seq=source_seq,
        payload={
            "schema_version": "delivery-trace.v1",
            "feature_id": "F-1",
            "legacy_cache_payload": True,
        },
    )

    data = client.get("/api/projects/default/delivery-traces/F-1").json()

    assert "legacy_cache_payload" not in data
    assert data["goal_coverage_graph"]["schema_version"] == "goal-coverage-graph.v1"


@pytest.mark.parametrize("view", ["overview", "runs", "graph", "work"])
def test_delivery_trace_v2_is_root_level_and_view_scoped(
    client: TestClient,
    view: str,
):
    response = client.get(
        "/api/projects/default/delivery-traces/F-1",
        params={"contract": "v2", "view": view},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["schema_version"] == "delivery-trace.v2"
    assert data["view"] == view
    assert data["feature_id"] == "F-1"
    assert data["refresh_scope"] == {
        "task_ids": ["T1", "T2"],
        "task_ids_total": 2,
        "task_ids_included": 2,
        "task_ids_omitted": 0,
        "task_ids_truncated": False,
    }
    assert data["task_map"]["task_count"] == 2
    assert data["run_summary"] == {
        "total": 0,
        "completed": 0,
        "running": 0,
        "failed": 0,
        "latest_label": "",
    }
    assert "loop_summary" not in data
    assert data["cursor"]["delta_bodies_included"] is False
    assert data["deltas"] == []
    assert data["execution_graph"]["task_count"] == 2
    assert data["ship"]["required_tasks"] == 2
    assert "diagnostics" not in data
    for omitted in ("thick_trace", "workflow_trace", "trace", "timeline"):
        assert omitted not in data

    if view == "overview":
        assert isinstance(data["attention"], list)
        assert isinstance(data["attention_summary"], dict)
        assert "canonical_trace_refs" not in data
        assert data["execution_graph"]["summary_only"] is True
        assert data["ship"]["status"] != "not_evaluated"
        assert "task_lifecycle" not in data
        assert "run_chain" not in data
        assert "goal_coverage_graph" not in data
    elif view == "runs":
        assert "attention" not in data
        assert isinstance(data["canonical_trace_refs"], list)
        assert data["task_lifecycle"]["schema_version"] == "task-lifecycle.v2"
        assert data["run_chain"]["schema_version"] == "run-chain.v2"
        assert data["task_flow"]["schema_version"] == "delivery-task-flow.v2"
        assert "goal_coverage_graph" not in data
        assert data["execution_graph"]["summary_only"] is True
        assert data["ship"]["status"] == "not_evaluated"
        assert data["ship"]["basis"] == "not_computed_for_runs_view"
    elif view == "graph":
        assert "attention" not in data
        assert "canonical_trace_refs" not in data
        assert data["execution_graph"]["schema_version"] == "execution-graph.v2"
        assert data["execution_graph"]["summary_only"] is True
        assert data["execution_graph"]["nodes"] == []
        assert data["goal_coverage_graph"]["schema_version"] == "goal-coverage-graph.v2"
        assert "task_lifecycle" not in data
        assert "run_chain" not in data
        assert "task_flow" not in data
    else:
        assert "attention" not in data
        assert "canonical_trace_refs" not in data
        assert data["execution_graph"]["nodes_only"] is True
        assert data["execution_graph"]["edges"] == []
        assert data["execution_graph"]["waves"] == []
        assert data["goal_coverage_graph"]["schema_version"] == "goal-coverage-graph.v2"
        assert data["task_lifecycle"]["schema_version"] == "task-lifecycle.v2"
        assert all(
            item["state_history"] == []
            for item in data["task_lifecycle"]["tasks"].values()
        )
        assert "run_chain" not in data
        assert "task_flow" not in data
        assert data["ship"]["basis"] == "not_computed_for_work_view"


def test_delivery_trace_v2_rejects_implicit_or_unknown_contracts(client: TestClient):
    assert client.get(
        "/api/projects/default/delivery-traces/F-1",
        params={"view": "overview"},
    ).status_code == 400
    assert client.get(
        "/api/projects/default/delivery-traces/F-1",
        params={"contract": "v2"},
    ).status_code == 400
    assert client.get(
        "/api/projects/default/delivery-traces/F-1",
        params={"contract": "v3", "view": "overview"},
    ).status_code == 400


def test_delivery_trace_v2_does_not_perturb_legacy_wire_payload(client: TestClient):
    # Warm the pre-existing cache first: the legacy cache itself adds its
    # projection_cache marker only on a warm hit.
    assert client.get("/api/projects/default/delivery-traces/F-1").status_code == 200
    before = client.get("/api/projects/default/delivery-traces/F-1")

    for view in ("overview", "runs", "graph", "work"):
        response = client.get(
            "/api/projects/default/delivery-traces/F-1",
            params={"contract": "v2", "view": view},
        )
        assert response.status_code == 200

    after = client.get("/api/projects/default/delivery-traces/F-1")
    assert after.content == before.content


@pytest.mark.parametrize("view", ["overview", "runs", "graph", "work"])
def test_delivery_trace_v2_never_calls_legacy_thick_loop_or_workflow_builders(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    view: str,
):
    import zf.runtime.workflow_trace as workflow_trace_module
    import zf.web.delivery_trace_routes as routes
    import zf.web.projections.delivery_views as views

    def forbidden(*_args, **_kwargs):
        raise AssertionError("forbidden heavy builder called")

    monkeypatch.setattr(routes, "resolve_delivery_trace", forbidden)
    monkeypatch.setattr(routes, "build_delivery_thick_trace", forbidden)
    monkeypatch.setattr(routes, "build_loop_projection", forbidden)
    monkeypatch.setattr(workflow_trace_module, "build_workflow_trace", forbidden)
    if view == "overview":
        monkeypatch.setattr(views, "_canonical_trace_projection", forbidden)
        monkeypatch.setattr(views, "build_goal_coverage_graph", forbidden)
        monkeypatch.setattr(views, "build_task_lifecycle", forbidden)
        monkeypatch.setattr(views, "build_run_chain", forbidden)
        monkeypatch.setattr(views, "build_task_flow", forbidden)
    elif view == "graph":
        monkeypatch.setattr(views, "_canonical_trace_projection", forbidden)
        monkeypatch.setattr(views, "build_task_lifecycle", forbidden)
        monkeypatch.setattr(views, "build_run_chain", forbidden)
        monkeypatch.setattr(views, "build_task_flow", forbidden)
    elif view == "runs":
        monkeypatch.setattr(views, "build_execution_graph", forbidden)
        monkeypatch.setattr(views, "build_drift_report", forbidden)
        monkeypatch.setattr(views, "build_goal_coverage_graph", forbidden)
    else:
        monkeypatch.setattr(views, "_canonical_trace_projection", forbidden)
        monkeypatch.setattr(views, "build_drift_report", forbidden)
        monkeypatch.setattr(views, "build_run_chain", forbidden)
        monkeypatch.setattr(views, "build_task_flow", forbidden)

    response = client.get(
        "/api/projects/default/delivery-traces/F-1",
        params={"contract": "v2", "view": view},
    )
    assert response.status_code == 200


def test_delivery_trace_v2_view_cache_is_watermark_scoped_not_cursor_scoped(
    client: TestClient,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import zf.web.delivery_trace_routes as routes

    original = routes.build_delivery_view
    calls: list[str] = []

    def counted(**kwargs):
        calls.append(str(kwargs["view"]))
        return original(**kwargs)

    monkeypatch.setattr(routes, "build_delivery_view", counted)
    path = "/api/projects/default/delivery-traces/F-1"
    params = {"contract": "v2", "view": "overview"}

    assert client.get(path, params=params).status_code == 200
    assert client.get(path, params=params).status_code == 200
    cursor_response = client.get(path, params={
        **params,
        "since_event_id": "e-build",
    })
    assert cursor_response.status_code == 200
    assert cursor_response.json()["cursor"]["since_seq"] == 2
    assert cursor_response.json()["deltas"] == []
    assert calls == ["overview"]

    assert client.get(path, params={"contract": "v2", "view": "graph"}).status_code == 200
    assert client.get(path, params=params).status_code == 200
    assert calls == ["overview", "graph"]

    event_log_from_project(state_dir, config=None, warn=False).append(ZfEvent(
        type="worker.progress",
        id="e-next-watermark",
        task_id="T2",
        payload={"feature_id": "F-1"},
    ))
    changed = client.get(path, params=params)
    assert changed.status_code == 200
    assert calls == ["overview", "graph", "overview"]
    assert changed.json()["as_of_event_id"] == "e-next-watermark"


def test_delivery_trace_v2_view_cache_tracks_task_store_freshness_without_event(
    client: TestClient,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    original_list = TaskStore.list_all_with_archive
    task_store_reads = 0

    def counted_list(self, *, last_days=None):
        nonlocal task_store_reads
        task_store_reads += 1
        return original_list(self, last_days=last_days)

    monkeypatch.setattr(TaskStore, "list_all_with_archive", counted_list)
    path = "/api/projects/default/delivery-traces/F-1"
    params = {"contract": "v2", "view": "runs"}

    first = client.get(path, params=params)
    source_seq = read_model.current_projected_seq(state_dir, config=None)
    assert first.status_code == 200
    assert first.json()["task_lifecycle"]["task_statuses"]["T2"] == "in_progress"

    updated = TaskStore(state_dir / "kanban.json").update("T2", status="blocked")
    assert updated is not None
    assert read_model.current_projected_seq(state_dir, config=None) == source_seq

    changed = client.get(path, params=params)
    assert changed.status_code == 200
    assert changed.json()["task_lifecycle"]["task_statuses"]["T2"] == "blocked"
    assert changed.json()["execution_graph"]["blocked_count"] == 1
    assert "projection_cache" not in changed.json()

    reads_before_warm_hit = task_store_reads
    warm = client.get(path, params=params)
    assert warm.status_code == 200
    assert warm.json()["task_lifecycle"]["task_statuses"]["T2"] == "blocked"
    assert "projection_cache" in warm.json()
    assert task_store_reads == reads_before_warm_hit


def test_delivery_trace_v2_canonical_refs_are_source_proven_and_resolvable(
    client: TestClient,
    state_dir: Path,
):
    long_trace_id = "delivery-trace-" + ("x" * 500)
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(
        type="worker.progress",
        id="e-foreign",
        correlation_id="foreign-trace",
        payload={"feature_id": "F-other"},
    ))
    log.append(ZfEvent(
        type="worker.progress",
        id="e-long-trace",
        task_id="T2",
        correlation_id=long_trace_id,
        payload={"feature_id": "F-1"},
    ))

    response = client.get(
        "/api/projects/default/delivery-traces/F-1",
        params={"contract": "v2", "view": "runs"},
    )

    assert response.status_code == 200
    refs = response.json()["canonical_trace_refs"]
    assert "foreign-trace" not in {item["trace_id"] for item in refs}
    assert any(item["trace_id"] == "task:T1" for item in refs)
    opaque = next(item for item in refs if item["trace_id_opaque"])
    assert opaque["membership"] == "trace-v2-source-event"
    assert opaque["source_event_ids"] == ["e-long-trace"]
    assert long_trace_id not in response.text

    detail = client.get(
        f"/api/projects/default/traces/{opaque['trace_id']}",
        params={"contract": "v2"},
    )
    assert detail.status_code == 200
    assert detail.json()["event_count"] == 1
    assert detail.json()["timeline"][0]["id"] == "e-long-trace"


def test_delivery_trace_v2_scope_tokens_are_namespace_typed(
    client: TestClient,
    state_dir: Path,
):
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(
        type="task.dispatched",
        id="e-local-dispatch",
        task_id="T2",
        payload={"feature_id": "F-1", "dispatch_id": "shared-identity"},
    ))
    log.append(ZfEvent(
        type="worker.progress",
        id="e-foreign-trace-collision",
        correlation_id="shared-identity",
        payload={"feature_id": "F-other"},
    ))

    response = client.get(
        "/api/projects/default/delivery-traces/F-1",
        params={"contract": "v2", "view": "runs"},
    )

    assert response.status_code == 200
    trace_ids = {
        item["trace_id"]
        for item in response.json()["canonical_trace_refs"]
    }
    assert "shared-identity" not in trace_ids
    assert response.json()["run_summary"]["total"] == 1


def test_delivery_trace_v2_scope_closes_causation_upward_and_downward(
    client: TestClient,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import zf.web.projections.delivery_views as views

    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(type="workflow.requested", id="e-parent-trigger"))
    log.append(ZfEvent(
        type="task.dispatched",
        id="e-scoped-task",
        task_id="T2",
        causation_id="e-parent-trigger",
        payload={"dispatch_id": "run-causation"},
    ))
    log.append(ZfEvent(
        type="artifact.published",
        id="e-child-artifact",
        causation_id="e-scoped-task",
    ))
    original = views.build_run_chain
    captured_ids: list[str] = []

    def capture(events, **kwargs):
        captured_ids.extend(str(event.id or "") for _seq, event in events)
        return original(events, **kwargs)

    monkeypatch.setattr(views, "build_run_chain", capture)
    response = client.get(
        "/api/projects/default/delivery-traces/F-1",
        params={"contract": "v2", "view": "runs"},
    )

    assert response.status_code == 200
    assert {"e-parent-trigger", "e-scoped-task", "e-child-artifact"} <= set(captured_ids)


def test_delivery_trace_v2_scope_does_not_expand_ancestor_siblings(
    client: TestClient,
    state_dir: Path,
):
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(
        type="workflow.requested",
        id="e-shared-parent",
        correlation_id="trace-parent",
        payload={"run_id": "parent-shared-run"},
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        id="e-f1-child",
        task_id="T2",
        causation_id="e-shared-parent",
        correlation_id="trace-f1-child",
        payload={"feature_id": "F-1", "dispatch_id": "run-f1"},
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        id="e-f2-sibling",
        causation_id="e-shared-parent",
        correlation_id="trace-f2-sibling",
        payload={
            "feature_id": "F-2",
            "dispatch_id": "run-f2",
            "run_id": "parent-shared-run",
        },
    ))

    response = client.get(
        "/api/projects/default/delivery-traces/F-1",
        params={"contract": "v2", "view": "runs"},
    )

    assert response.status_code == 200
    data = response.json()
    assert "trace-f2-sibling" not in {
        item["trace_id"]
        for item in data["canonical_trace_refs"]
    }
    assert data["run_summary"]["total"] == 2
    assert data["run_summary"]["running"] == 1
    assert data["run_summary"]["latest_label"] == "router · running"


def test_delivery_trace_v2_run_summary_keeps_terminal_status_sticky(
    client: TestClient,
    state_dir: Path,
):
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(
        type="task.dispatched",
        id="e-run-done-start",
        task_id="T2",
        payload={"dispatch_id": "run-done", "status": "dispatched"},
    ))
    log.append(ZfEvent(
        type="task.done",
        id="e-run-done-terminal",
        task_id="T2",
        payload={"dispatch_id": "run-done", "status": "done"},
    ))
    log.append(ZfEvent(
        type="artifact.published",
        id="e-run-done-artifact",
        task_id="T2",
        payload={"dispatch_id": "run-done"},
    ))
    log.append(ZfEvent(
        type="static_gate.failed",
        id="e-run-failed-terminal",
        task_id="T2",
        payload={"dispatch_id": "run-failed", "status": "failed"},
    ))
    log.append(ZfEvent(
        type="worker.heartbeat",
        id="e-run-failed-late-heartbeat",
        task_id="T2",
        payload={"dispatch_id": "run-failed", "status": "running"},
    ))

    response = client.get(
        "/api/projects/default/delivery-traces/F-1",
        params={"contract": "v2", "view": "overview"},
    )

    assert response.status_code == 200
    summary = response.json()["run_summary"]
    assert summary["total"] == 2
    assert summary["completed"] == 1
    assert summary["failed"] == 1
    assert summary["running"] == 0


def test_delivery_trace_v2_payload_budgets(client: TestClient):
    path = "/api/projects/default/delivery-traces/F-1"
    budgets = {"overview": 30, "graph": 80, "runs": 120, "work": 96}
    for view, kib in budgets.items():
        response = client.get(
            path,
            params={"contract": "v2", "view": view},
        )
        assert response.status_code == 200
        assert len(response.content) < kib * 1024


def test_delivery_work_explicit_goal_scope_is_bounded_and_legacy_query_stays_compatible(
    client: TestClient,
):
    path = "/api/projects/default/delivery-traces/F-1"

    legacy = client.get(path, params={"contract": "v2", "view": "work"})
    scoped = client.get(path, params={
        "contract": "v2",
        "view": "work",
        "goal_id": "F-1",
    })
    missing = client.get(path, params={
        "contract": "v2",
        "view": "work",
        "goal_id": "GOAL-MISSING",
    })

    assert legacy.status_code == 200
    assert "work_scope" not in legacy.json()
    assert scoped.status_code == 200
    scoped_data = scoped.json()
    assert scoped_data["work_scope"] == {
        "goal_id": "F-1",
        "goal_id_opaque": False,
        "matched": True,
        "claim_count": 1,
        "task_count": 2,
    }
    # A single-Goal feature keeps its canonical, not-yet-mapped work visible.
    assert scoped_data["execution_graph"]["task_count"] == 2
    assert len(scoped.content) < 96 * 1024
    _assert_complete_truncation_metadata(scoped_data)
    assert missing.status_code == 200
    missing_data = missing.json()
    assert missing_data["work_scope"]["matched"] is False
    assert missing_data["execution_graph"]["task_count"] == 0
    assert missing_data["execution_graph"]["nodes"] == []
    assert missing_data["goal_coverage_graph"]["nodes"] == []
    assert len(missing.content) < 16 * 1024
    _assert_complete_truncation_metadata(missing_data)


def test_delivery_work_goal_scope_rejects_unbounded_or_non_work_query(
    client: TestClient,
):
    path = "/api/projects/default/delivery-traces/F-1"
    adversarial_goal = "SECRET-" + ("x" * 4096)

    oversized = client.get(path, params={
        "contract": "v2",
        "view": "work",
        "goal_id": adversarial_goal,
    })
    wrong_view = client.get(path, params={
        "contract": "v2",
        "view": "graph",
        "goal_id": "F-1",
    })

    assert oversized.status_code == 400
    assert len(oversized.content) < 1024
    assert adversarial_goal[:100] not in oversized.text
    assert wrong_view.status_code == 400


def test_delivery_graph_marks_unexpandable_opaque_goal_identity():
    from zf.web.projections.delivery_view_graph import _compact_goal_coverage

    raw_goal_id = "GOAL-" + ("sensitive-" * 80)
    compact = _compact_goal_coverage({
        "nodes": [{
            "node_id": f"goal:{raw_goal_id}",
            "kind": "goal",
            "goal_id": raw_goal_id,
            "title": "Bounded Goal",
        }],
        "edges": [],
        "diagnostics": [],
    })

    goal = compact["nodes"][0]
    assert goal["goal_id_opaque"] is True
    assert goal["goal_id"].startswith("goal-ref:sha256:")
    assert raw_goal_id not in json.dumps(compact)
    assert len(json.dumps(compact)) < 4096


def test_delivery_work_goal_scope_follows_goal_edges_and_excludes_unclaimed_work():
    from zf.web.projections.delivery_view_work import (
        compact_work_projection,
        scope_work_goal_graph,
    )

    raw_goal_graph = {
        "identity": {"goal_id": "GOAL-A"},
        "summary": {},
        "nodes": [
            {"node_id": "goal:GOAL-A", "kind": "goal", "goal_id": "GOAL-A"},
            {"node_id": "goal:GOAL-B", "kind": "goal", "goal_id": "GOAL-B"},
            {
                "node_id": "claim:A",
                "kind": "goal_claim",
                "goal_claim_id": "A",
                "task_ids": ["T-A"],
            },
            {
                "node_id": "claim:B",
                "kind": "goal_claim",
                "goal_claim_id": "B",
                "task_ids": ["T-B"],
            },
            {
                "node_id": "task:T-A",
                "kind": "task",
                "task_id": "T-A",
                "goal_claim_ids": ["A"],
            },
            {
                "node_id": "task:T-B",
                "kind": "task",
                "task_id": "T-B",
                "goal_claim_ids": ["B"],
            },
        ],
        "edges": [
            {"from": "goal:GOAL-A", "to": "claim:A", "kind": "has_claim"},
            {"from": "goal:GOAL-B", "to": "claim:B", "kind": "has_claim"},
            {"from": "task:T-A", "to": "claim:A", "kind": "covers"},
            {"from": "task:T-B", "to": "claim:B", "kind": "covers"},
        ],
        "diagnostics": [],
    }
    scoped_graph, matched = scope_work_goal_graph(
        raw_goal_graph,
        goal_id="GOAL-A",
    )
    tasks = {
        task_id: Task(id=task_id, status="in_progress")
        for task_id in ("T-A", "T-B", "T-UNCLAIMED")
    }
    compact = compact_work_projection(
        execution_graph={
            "nodes": [
                {
                    "task_id": task_id,
                    "title": task_id,
                    "planned": {"blocked_by": []},
                    "actual": {"status": task.status, "evidence_events": []},
                }
                for task_id, task in tasks.items()
            ],
        },
        goal_graph=scoped_graph,
        compact_goal_graph=scoped_graph,
        evidence_by_task={},
        lifecycle={"tasks": {}},
        tasks=tasks,
        goal_scoped=True,
    )

    assert matched is True
    assert {node["node_id"] for node in scoped_graph["nodes"]} == {
        "goal:GOAL-A", "claim:A", "task:T-A",
    }
    assert compact["execution_graph"]["task_count"] == 1
    assert [
        node["task_id"] for node in compact["execution_graph"]["nodes"]
    ] == ["T-A"]


def test_delivery_work_projection_keeps_visible_mappings_and_true_unmapped_only():
    from zf.web.projections.delivery_view_wire import wire_id
    from zf.web.projections.delivery_view_work import compact_work_projection

    visible_claim = "CLAIM-" + ("v" * 180)
    omitted_claim = "CLAIM-OMITTED"
    compact_claim = wire_id(visible_claim, namespace="claim")[0]
    tasks = {
        "T-visible": Task(id="T-visible", status="in_progress"),
        "T-omitted": Task(id="T-omitted", status="in_progress"),
        "T-unmapped": Task(id="T-unmapped", status="backlog"),
        "T-goal-only": Task(id="T-goal-only", status="blocked"),
        "T-canonical-only": Task(id="T-canonical-only", status="backlog"),
    }
    result = compact_work_projection(
        execution_graph={
            "nodes": [
                {
                    "task_id": task_id,
                    "title": task_id,
                    "planned": {"blocked_by": []},
                    "actual": {"status": task.status, "evidence_events": []},
                }
                for task_id, task in tasks.items()
                if task_id != "T-goal-only"
            ],
        },
        goal_graph={
            "nodes": [
                {
                    "kind": "task",
                    "task_id": "T-visible",
                    "goal_claim_ids": [visible_claim],
                },
                {
                    "kind": "task",
                    "task_id": "T-omitted",
                    "goal_claim_ids": [omitted_claim],
                },
                {
                    "kind": "task",
                    "task_id": "T-goal-only",
                    "goal_claim_ids": [visible_claim],
                },
            ],
        },
        compact_goal_graph={
            "nodes": [
                {"kind": "goal_claim", "goal_claim_id": compact_claim},
                {"kind": "task", "task_id": "T-visible"},
                {"kind": "task", "task_id": "T-goal-only"},
            ],
        },
        evidence_by_task={"T-goal-only": ["evt-goal-only"]},
        lifecycle={
            "tasks": {
                "T-goal-only": {
                    "state_history": [],
                    "tries": [{"try": 1, "outcome": "failed", "gate_results": []}],
                },
            },
        },
        tasks=tasks,
    )

    nodes = {node["task_id"]: node for node in result["execution_graph"]["nodes"]}
    assert set(nodes) == {
        "T-visible", "T-goal-only", "T-unmapped", "T-canonical-only",
    }
    assert nodes["T-visible"]["goal_claim_ids"] == [compact_claim]
    assert nodes["T-goal-only"]["actual"]["status"] == "blocked"
    assert nodes["T-goal-only"]["actual"]["evidence_events"] == ["evt-goal-only"]
    assert result["execution_graph"]["task_count"] == 5
    assert result["execution_graph"]["nodes_omitted"] == 1
    assert result["execution_graph"]["blocked_count"] == 1
    assert result["task_lifecycle"]["tasks"]["T-goal-only"]["tries_included"] == 1


def test_delivery_work_keeps_event_evidence_for_task_outside_accepted_map(
    client: TestClient,
    state_dir: Path,
):
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T-EXTRA",
        title="canonical task outside accepted map",
        status="blocked",
        contract=TaskContract(feature_id="F-1"),
    ))
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(
        type="verify.failed",
        id="e-extra-verify",
        task_id="T-EXTRA",
    ))

    response = client.get(
        "/api/projects/default/delivery-traces/F-1",
        params={"contract": "v2", "view": "work"},
    )

    assert response.status_code == 200
    node = next(
        item
        for item in response.json()["execution_graph"]["nodes"]
        if item["task_id"] == "T-EXTRA"
    )
    assert node["actual"]["status"] == "blocked"
    assert node["actual"]["evidence_events"] == ["e-extra-verify"]
    assert node["actual"]["evidence_events_total"] == 1
    assert node["actual"]["evidence_events_truncated"] is False


def test_delivery_work_projection_keeps_actionable_task_beyond_goal_node_cap():
    from zf.web.projections.delivery_view_work import compact_work_projection

    tasks = {
        f"T-{index:02d}": Task(
            id=f"T-{index:02d}",
            status="blocked" if index == 39 else "done",
        )
        for index in range(40)
    }
    execution_nodes = [
        {
            "task_id": task_id,
            "title": task_id,
            "planned": {"blocked_by": []},
            "actual": {"status": task.status, "evidence_events": []},
        }
        for task_id, task in tasks.items()
    ]
    result = compact_work_projection(
        execution_graph={"nodes": execution_nodes},
        goal_graph={
            "nodes": [
                {"kind": "task", "task_id": task_id, "goal_claim_ids": []}
                for task_id in tasks
            ],
        },
        compact_goal_graph={
            "nodes": [
                {"kind": "task", "task_id": f"T-{index:02d}", "goal_claim_ids": []}
                for index in range(39)
            ],
        },
        evidence_by_task={},
        lifecycle={"tasks": {}},
        tasks=tasks,
    )

    selected_ids = {
        node["task_id"] for node in result["execution_graph"]["nodes"]
    }
    assert len(selected_ids) == 32
    assert "T-39" in selected_ids
    assert result["execution_graph"]["blocked_count"] == 1


def test_delivery_trace_v2_large_log_is_linear_and_payloads_remain_bounded(
    client: TestClient,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import zf.web.projections.delivery_views as views

    log = event_log_from_project(state_dir, config=None, warn=False)
    for index in range(180):
        log.append(ZfEvent(
            type="task.dispatched",
            id=f"e-many-{index}",
            task_id="T2",
            correlation_id="trace-many",
            payload={
                "feature_id": "F-1",
                "dispatch_id": f"dispatch-{index}",
                "status": "dispatched",
                "message": "x" * 5000,
            },
        ))
    event_count = len(log.read_all())
    original = views._event_scope_tokens
    calls = 0

    def counted(event):
        nonlocal calls
        calls += 1
        return original(event)

    monkeypatch.setattr(views, "_event_scope_tokens", counted)
    path = "/api/projects/default/delivery-traces/F-1"
    responses = {
        view: client.get(path, params={"contract": "v2", "view": view})
        for view in ("overview", "runs", "graph", "work")
    }

    assert calls == event_count * 4
    assert len(responses["overview"].content) < 64 * 1024
    assert len(responses["runs"].content) < 512 * 1024
    assert len(responses["graph"].content) < 512 * 1024
    assert len(responses["work"].content) < 512 * 1024
    trace_ref = next(
        item
        for item in responses["runs"].json()["canonical_trace_refs"]
        if item["trace_id"] == "trace-many"
    )
    assert trace_ref["source_event_ids_truncated"] is True
    assert "x" * 100 not in responses["overview"].text
    assert "x" * 100 not in responses["work"].text


def test_delivery_trace_v2_adversarial_lifecycle_and_claims_stay_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import zf.web.projections.delivery_views as views

    state = tmp_path / ".zf"
    state.mkdir()
    (state / "feature_list.json").write_text("[]")
    long_task_tail = "x" * 180
    long_claim_tail = "c" * 180
    task_ids = [f"ADV-{index:03d}-{long_task_tail}" for index in range(160)]
    claim_ids = [f"CLAIM-{index:03d}-{long_claim_tail}" for index in range(219)]
    task_indexes = {task_id: index for index, task_id in enumerate(task_ids)}
    TaskStore(state / "kanban.json").add_many([
        Task(
            id=task_id,
            title=f"adversarial task {index} " + ("t" * 300),
            status=(
                "done" if index < 152
                else "failed" if index < 156
                else "in_progress"
            ),
            assigned_to=f"dev-{index % 8}-" + ("a" * 180),
            blocked_by=task_ids[max(0, index - 8):index],
            contract=TaskContract(
                feature_id="F-BIG",
                owner_role="dev",
                owner_instance="owner-" + ("o" * 180),
                phase="implementation-" + ("p" * 180),
                wave=(index % 4) + 1,
                goal_claim_ids=[claim_ids[index]],
            ),
        )
        for index, task_id in enumerate(task_ids)
    ])
    artifacts = state / "artifacts" / "F-BIG"
    artifacts.mkdir(parents=True)
    (artifacts / "task_map.json").write_text(json.dumps({
        "schema_version": "task-map.v1",
        "feature_id": "F-BIG",
        "goal_claims": [
                {
                    "goal_claim_id": claim_ids[index],
                    "text": f"claim {index} " + ("q" * 300),
                    "mandatory": True,
                }
            for index in range(219)
        ],
        "tasks": [
                {
                    "task_id": task_id,
                    "title": f"adversarial task {index} " + ("t" * 300),
                    "owner_role": "dev",
                    "owner_instance": "owner-" + ("o" * 180),
                    "wave": (index % 4) + 1,
                    "blocked_by": task_ids[max(0, index - 8):index],
                    "goal_claim_ids": [claim_ids[index]],
                }
            for index, task_id in enumerate(task_ids)
        ],
    }))
    event_log_from_project(state, config=None, warn=False).append(ZfEvent(
        type="feature.started",
        id="e-big-feature",
        payload={"feature_id": "F-BIG"},
    ))
    log = event_log_from_project(state, config=None, warn=False)
    for index, task_id in enumerate(task_ids[:24]):
        log.append(ZfEvent(
            type="task.dispatched",
            id=f"event-{index}-" + ("e" * 240),
            task_id=task_id,
            correlation_id=f"trace-{index}-" + ("r" * 240),
            payload={
                "feature_id": "F-BIG",
                "dispatch_id": f"dispatch-{index}-" + ("d" * 240),
            },
        ))

    lifecycle = {"tasks": {
        task_id: {
            "state_history": [
                {
                        "state": (
                            "done" if task_indexes[task_id] < 152
                            else "failed" if task_indexes[task_id] < 156
                            else "running"
                        ),
                    "entered_at": f"2026-08-19T12:{attempt:02d}:00Z",
                    "dwell_seconds": attempt,
                        "via_event_id": f"state-{task_indexes[task_id]}-{attempt}-" + ("e" * 240),
                    "try": attempt + 1,
                }
                for attempt in range(12)
            ],
            "tries": [
                {
                    "try": attempt + 1,
                        "dispatch_id": f"dispatch-{task_indexes[task_id]}-{attempt}-" + ("d" * 240),
                        "dispatched_at": f"2026-08-19T12:{attempt:02d}:00Z",
                        "outcome": (
                            "done" if task_indexes[task_id] < 152
                            else "failed" if task_indexes[task_id] < 156
                            else "in_flight"
                        ),
                        "briefing_ref": f"briefing-{task_indexes[task_id]}-{attempt}-" + ("b" * 240),
                        "snapshot_ref": f"snapshot-{task_indexes[task_id]}-{attempt}-" + ("s" * 240),
                    "seq_first": attempt,
                    "seq_last": attempt + 1,
                    "gate_results": [
                        {
                            "type": "static_gate.failed",
                            "passed": False,
                                "event_id": (
                                    f"gate-{task_indexes[task_id]}-{attempt}-{gate}-"
                                    + ("g" * 240)
                                ),
                        }
                        for gate in range(12)
                    ],
                }
                for attempt in range(12)
            ],
        }
        for task_id in task_ids
    }}
    monkeypatch.setattr(views, "build_task_lifecycle", lambda _events: lifecycle)
    flow_tasks = [
        {
            "task_id": task_id,
            "title": f"flow task {index} " + ("t" * 300),
            "status": "failed" if index >= 152 else "done",
            "assigned_to": "assignee-" + ("a" * 240),
            "phase": "phase-" + ("p" * 240),
            "owner_role": "dev-" + ("o" * 240),
            "owner_instance": "instance-" + ("i" * 240),
            "blocked_by": task_ids[max(0, index - 8):index],
            "source_event_ids": [
                f"flow-event-{index}-{ref}-" + ("e" * 240)
                for ref in range(12)
            ],
        }
        for index, task_id in enumerate(task_ids)
    ]
    task_flow = {
        "stage_order": [f"stage-{index}-" + ("s" * 240) for index in range(32)],
        "active_stage_ids": ["stage-active-" + ("s" * 240)],
        "stages": [
            {
                "stage_id": f"stage-{index}-" + ("s" * 240),
                "label": f"stage label {index} " + ("l" * 300),
                "status": "active",
                "tasks_total": 160,
                "tasks_done": 152,
                "tasks_running": 4,
                "tasks_failed": 4,
                "tasks_blocked": 0,
                "active_task_ids": task_ids[-8:],
                "task_ids": task_ids,
                "tasks": flow_tasks,
                "run_group_ids": [
                    f"run-group-{index}-{ref}-" + ("r" * 240)
                    for ref in range(20)
                ],
                "source_event_ids": [
                    f"stage-event-{index}-{ref}-" + ("e" * 240)
                    for ref in range(20)
                ],
            }
            for index in range(32)
        ],
        "metrics": {"task_total": 160},
    }
    monkeypatch.setattr(views, "build_task_flow", lambda **_kwargs: task_flow)
    run_chain = {
        "status": "running",
        "trigger": {"type": "workflow.requested", "id": "trigger-" + ("e" * 240)},
        "stages": [
            {
                "stage": f"stage-{index}-" + ("s" * 240),
                "status": "active",
                "entered_at": "2026-08-19T12:00:00Z",
                "completed_at": "",
                "via_event_id": f"chain-event-{index}-" + ("e" * 240),
                "causation_id": f"chain-parent-{index}-" + ("c" * 240),
                "occurrences": 1,
                "seq_first": index,
                "seq_last": index,
                "task_ids": task_ids,
            }
            for index in range(32)
        ],
    }
    monkeypatch.setattr(views, "build_run_chain", lambda *_args, **_kwargs: run_chain)
    big_client = TestClient(create_app(state))
    path = "/api/projects/default/delivery-traces/F-BIG"

    runs = big_client.get(path, params={"contract": "v2", "view": "runs"})
    graph = big_client.get(path, params={"contract": "v2", "view": "graph"})
    work = big_client.get(path, params={"contract": "v2", "view": "work"})

    assert runs.status_code == 200
    assert graph.status_code == 200
    assert work.status_code == 200
    assert len(runs.content) < 80 * 1024
    assert len(graph.content) < 120 * 1024
    assert len(work.content) < 128 * 1024
    for response in (runs, graph, work):
        refresh_scope = response.json()["refresh_scope"]
        assert refresh_scope["task_ids"] == []
        assert refresh_scope["task_ids_total"] == 160
        assert refresh_scope["task_ids_included"] == 0
        assert refresh_scope["task_ids_omitted"] == 160
        assert refresh_scope["task_ids_truncated"] is True
    compact = runs.json()["task_lifecycle"]
    assert compact["task_count"] == 160
    assert compact["tasks_included"] < compact["task_count"]
    assert compact["tasks_omitted"] == compact["task_count"] - compact["tasks_included"]
    assert compact["tasks_truncated"] is True
    from zf.web.projections.delivery_view_wire import wire_task_id

    actionable_wire_ids = {wire_task_id(task_id)[0] for task_id in task_ids[-8:]}
    assert actionable_wire_ids <= set(compact["tasks"])
    assert compact["task_status_count"] == 160
    assert 0 < compact["task_statuses_included"] < compact["task_status_count"]
    assert compact["task_statuses_omitted"] == (
        compact["task_status_count"] - compact["task_statuses_included"]
    )
    assert compact["task_statuses_truncated"] is True
    assert actionable_wire_ids <= set(compact["task_statuses"])
    visible_wire_ids = {wire_task_id(task_id)[0] for task_id in task_ids[:16]}
    assert visible_wire_ids <= set(compact["task_statuses"])
    assert compact["state_history_total"] == 160 * 12
    assert compact["state_history_included"] == 12
    assert compact["state_history_truncated"] is True
    assert compact["tries_total"] == 160 * 12
    assert compact["tries_included"] == 8
    assert compact["tries_truncated"] is True
    assert compact["gate_results_total"] == 160 * 12 * 12
    assert compact["gate_results_included"] == 8
    assert compact["gate_results_truncated"] is True
    flow = runs.json()["task_flow"]
    assert flow["stage_count"] == 32
    assert flow["stages_included"] == 8
    assert flow["stages_omitted"] == 24
    assert flow["stages_truncated"] is True
    assert flow["task_rows_total"] == 32 * 160
    assert flow["task_rows_included"] == 8
    assert flow["task_rows_truncated"] is True
    chain = runs.json()["run_chain"]
    assert chain["stage_count"] == 32
    assert chain["stages_included"] == 8
    assert chain["task_ids_total"] == 32 * 160
    assert chain["task_ids_included"] == 16
    assert chain["task_ids_omitted"] == (
        chain["task_ids_total"] - chain["task_ids_included"]
    )
    assert chain["task_ids_truncated"] is True
    assert len(runs.json()["canonical_trace_refs"]) == 8
    assert runs.json()["canonical_trace_refs_total"] == 24
    assert runs.json()["canonical_trace_refs_included"] == 8
    assert runs.json()["canonical_trace_refs_omitted"] == 16
    assert runs.json()["canonical_trace_refs_truncated"] is True
    assert "task_lifecycle" not in graph.json()
    assert work.json()["execution_graph"]["nodes_only"] is True
    assert work.json()["execution_graph"]["nodes_truncated"] is True
    assert work.json()["task_lifecycle"]["state_history_included"] == 0
    assert work.json()["task_lifecycle"]["state_history_truncated"] is True
    assert "x" * 100 not in runs.text
    assert "r" * 100 not in runs.text
    assert "x" * 100 not in graph.text
    assert "c" * 100 not in graph.text
    assert "x" * 100 not in work.text

    _assert_complete_truncation_metadata(runs.json())
    _assert_complete_truncation_metadata(graph.json())
    _assert_complete_truncation_metadata(work.json())

    goal_graph = graph.json()["goal_coverage_graph"]
    assert goal_graph["nodes_truncated"] is True
    selected_ids = {node["node_id"] for node in goal_graph["nodes"]}
    claims = [node for node in goal_graph["nodes"] if node["kind"] == "goal_claim"]
    assert claims
    for claim in claims:
        for task_id in claim.get("task_ids", []):
            assert (
                f"task:{task_id}" in selected_ids
                or claim["task_details"]["missing_count"] > 0
            )


def test_delivery_trace_endpoint_includes_goal_closure_loop(
    client: TestClient,
    state_dir: Path,
):
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="ISSUE-GAP-001",
        title="fill issue gap",
        status="todo",
        assigned_to="dev-gap",
        contract=TaskContract(feature_id="F-1", owner_role="dev", wave=3),
    ))
    base_task_map_ref = ".zf/artifacts/F-1/task_map.json"
    amended_task_map_ref = ".zf/artifacts/F-1/gap-amends/evt-gap/task_map.json"
    gap_plan_ref = "reports/F-1/goal-gap-plan.json"
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(
        type="goal.rescan.requested",
        id="goal-scan-1",
        payload={"pdd_id": "F-1", "task_map_ref": base_task_map_ref},
    ))
    log.append(ZfEvent(
        type="goal.rescan.completed",
        id="goal-scan-2",
        payload={"pdd_id": "F-1", "task_map_ref": base_task_map_ref},
    ))
    log.append(ZfEvent(
        type="goal.gap_plan.ready",
        id="goal-gap-1",
        payload={
            "pdd_id": "F-1",
            "goal_kind": "issue",
            "gap_category": "issue_gap",
            "task_map_ref": base_task_map_ref,
            "gap_plan_ref": gap_plan_ref,
            "replan_history_ref": "docs/plans/F-1/replan-history.jsonl",
            "gap_tasks": [{"task_id": "ISSUE-GAP-001"}],
        },
    ))
    log.append(ZfEvent(
        type="task_map.amended",
        id="goal-amend-1",
        payload={
            "pdd_id": "F-1",
            "task_map_ref": base_task_map_ref,
            "new_task_map_ref": amended_task_map_ref,
            "gap_plan_ref": gap_plan_ref,
            "gap_task_ids": ["ISSUE-GAP-001"],
        },
    ))
    log.append(ZfEvent(
        type="task_map.ready",
        id="goal-ready-1",
        payload={
            "pdd_id": "F-1",
            "task_map_ref": amended_task_map_ref,
            "resume_scope": "gap_tasks_only",
            "task_ids": ["ISSUE-GAP-001"],
        },
    ))

    r = client.get("/api/projects/default/delivery-traces/F-1")

    assert r.status_code == 200
    loop = r.json()["goal_closure_loop"]
    assert loop["schema_version"] == "goal-closure-loop.v2"
    assert loop["status"] == "gap_tasks_dispatched"
    assert loop["gap_task_ids"] == ["ISSUE-GAP-001"]
    assert loop["latest_replan_history_ref"] == "docs/plans/F-1/replan-history.jsonl"


def test_delivery_trace_endpoint_includes_flow_neutral_goal_closure_loop(
    client: TestClient,
    state_dir: Path,
):
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="PRD-GAP-001",
        title="fill product gap",
        status="todo",
        assigned_to="dev-gap",
        contract=TaskContract(feature_id="F-1", owner_role="dev", wave=3),
    ))
    base_task_map_ref = ".zf/artifacts/F-1/task_map.json"
    amended_task_map_ref = ".zf/artifacts/F-1/gap-amends/evt-flow/task_map.json"
    gap_plan_ref = "reports/F-1/flow-gap-plan.json"
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(
        type="flow.discovery.requested",
        id="flow-scan-1",
        payload={"pdd_id": "F-1", "flow_kind": "prd", "task_map_ref": base_task_map_ref},
    ))
    log.append(ZfEvent(
        type="flow.discovery.completed",
        id="flow-scan-2",
        payload={"pdd_id": "F-1", "flow_kind": "prd", "task_map_ref": base_task_map_ref},
    ))
    log.append(ZfEvent(
        type="flow.gap_plan.ready",
        id="flow-gap-1",
        payload={
            "pdd_id": "F-1",
            "flow_kind": "prd",
            "goal_kind": "prd",
            "gap_category": "acceptance_gap",
            "task_map_ref": base_task_map_ref,
            "gap_plan_ref": gap_plan_ref,
            "gap_tasks": [{"task_id": "PRD-GAP-001"}],
        },
    ))
    log.append(ZfEvent(
        type="task_map.amended",
        id="flow-amend-1",
        payload={
            "pdd_id": "F-1",
            "task_map_ref": base_task_map_ref,
            "new_task_map_ref": amended_task_map_ref,
            "gap_plan_ref": gap_plan_ref,
            "gap_task_ids": ["PRD-GAP-001"],
        },
    ))
    log.append(ZfEvent(
        type="task_map.ready",
        id="flow-ready-1",
        payload={
            "pdd_id": "F-1",
            "task_map_ref": amended_task_map_ref,
            "resume_scope": "gap_tasks_only",
            "task_ids": ["PRD-GAP-001"],
        },
    ))

    r = client.get("/api/projects/default/delivery-traces/F-1")

    assert r.status_code == 200
    loop = r.json()["goal_closure_loop"]
    assert loop["schema_version"] == "goal-closure-loop.v2"
    assert loop["status"] == "gap_tasks_dispatched"
    assert loop["scan_request_count"] == 1
    assert loop["scan_result_count"] == 1
    assert loop["gap_task_ids"] == ["PRD-GAP-001"]
    assert loop["latest_gap_plan_ref"] == gap_plan_ref


def test_delivery_features_endpoint(client: TestClient):
    r = client.get("/api/projects/default/delivery-features")

    assert r.status_code == 200
    data = r.json()
    feature_ids = {
        item["id"]
        for item in [*data["delivery_features"], *data["features"]]
    }
    assert "F-1" in feature_ids


def test_delivery_thick_trace_sibling_endpoint(client: TestClient, state_dir: Path):
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(
        type="task.rework.triage.completed",
        id="e-rework",
        task_id="T2",
        payload={"classification": "evidence_payload_gap", "reason": "missing test evidence"},
    ))

    r = client.get("/api/projects/default/delivery-traces/F-1/thick")

    assert r.status_code == 200
    data = r.json()
    assert data["schema_version"] == "delivery-thick-trace.v1"
    assert data["target"]["id"] == "F-1"
    assert any(item["kind"] == "missing_evidence" for item in data["behaviors"])
    assert any(node["kind"] == "behavior" for node in data["graph"]["nodes"])


def test_delivery_trace_includes_related_loop_refs(client: TestClient, state_dir: Path):
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(
        type="static_gate.failed",
        id="gate-fail",
        task_id="T2",
        payload={"feature_id": "F-1", "reason": "pytest failed"},
    ))
    kanban = state_dir / "kanban.json"
    before = (kanban.stat().st_mtime_ns, kanban.read_bytes())

    r = client.get("/api/projects/default/delivery-traces/F-1")

    after = (kanban.stat().st_mtime_ns, kanban.read_bytes())
    assert r.status_code == 200
    data = r.json()
    assert data["related_loop_count"] == 1
    assert data["related_loop_ids"][0].startswith("loop:gate_failure:")
    assert data["thick_trace"]["related_loop_ids"] == data["related_loop_ids"]
    assert before == after


def test_web_delivery_includes_replan_gate_projection(
    client: TestClient,
    state_dir: Path,
):
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(
        type="replan.contract_eval.completed",
        id="eval-web",
        payload={
            "feature_id": "F-1",
            "eval_id": "eval-web",
            "decision": "revise",
            "profile": "baseline",
            "failed_checks": ["resume_safety"],
            "new_task_map_ref": "artifacts/F-1/task_map-v2.json",
        },
    ))

    r = client.get("/api/projects/default/delivery-traces/F-1")

    assert r.status_code == 200
    gate = r.json()["replan_contract_gate"]
    closed_loop = r.json()["closed_loop"]
    assert gate["latest_eval"]["eval_id"] == "eval-web"
    assert gate["latest_eval"]["failed_checks"] == ["resume_safety"]
    assert any(node["kind"] == "contract_gate" for node in closed_loop["nodes"])
    assert "adopt" not in {
        route.path
        for route in client.app.routes
        if "replan" in route.path and "adopt" in route.path
    }


def test_delivery_trace_cursor_deltas(client: TestClient, state_dir: Path):
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(
        type="verify.failed",
        id="e-after",
        task_id="T2",
        payload={"feature_id": "F-1", "stage_id": "verify", "reason": "coverage gap"},
    ))

    r = client.get("/api/projects/default/delivery-traces/F-1?since_event_id=e-build")

    assert r.status_code == 200
    data = r.json()
    assert data["schema_version"] == "delivery-trace.v1"
    assert data["cursor"]["since_event_id"] == "e-build"
    assert data["cursor"]["new_event_count"] == 1
    assert data["cursor"]["degraded"] is False
    assert data["deltas"][0]["event_id"] == "e-after"
    assert data["deltas"][0]["type"] == "stage.status_changed"
    assert data["deltas"][0]["stage_id"] == "verify"


def test_delivery_trace_unknown_cursor_degrades(client: TestClient):
    r = client.get("/api/projects/default/delivery-traces/F-1?since_event_id=missing")

    assert r.status_code == 200
    data = r.json()
    assert data["cursor"]["degraded"] is True
    assert data["deltas"][0]["type"] == "cursor.degraded"
    assert "missing" in data["cursor"]["reason"]


def test_execution_graph_endpoint(client: TestClient):
    r = client.get("/api/projects/default/delivery-traces/F-1/execution-graph")
    assert r.status_code == 200
    data = r.json()
    assert data["schema_version"] == "execution-graph.v1"
    assert {n["task_id"] for n in data["nodes"]} == {"T1", "T2"}
    # blocked_by edge T1->T2 satisfied (T1 done)
    edges = {(e["from"], e["to"]): e for e in data["edges"]}
    assert edges[("T1", "T2")]["status"] == "satisfied"


def test_drift_report_endpoint(client: TestClient):
    r = client.get("/api/projects/default/delivery-traces/F-1/drift-report")
    assert r.status_code == 200
    data = r.json()
    assert data["schema_version"] == "drift-report.v1"
    assert "summary" in data


def test_workflow_run_endpoint(state_dir: Path):
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(type="fanout.started", payload={
        "fanout_id": "FX", "trace_id": "tr", "stage_id": "review", "topology": "fanout_reader",
        "expected_children": [{"child_id": "c1", "role_instance": "review-c1"}],
    }, correlation_id="tr"))
    log.append(ZfEvent(type="fanout.child.dispatched", payload={"fanout_id": "FX", "child_id": "c1"}))
    client = TestClient(create_app(state_dir))
    r = client.get("/api/projects/default/workflow-runs/FX")
    assert r.status_code == 200
    data = r.json()
    assert data["schema_version"] == "workflow-run.v1"
    assert data["fanout_id"] == "FX"
    assert data["status"] == "running"


def test_delivery_trace_does_not_mutate_state(client: TestClient, state_dir: Path):
    kanban = state_dir / "kanban.json"
    before = (kanban.stat().st_mtime_ns, kanban.read_bytes())
    client.get("/api/projects/default/delivery-traces/F-1")
    after = (kanban.stat().st_mtime_ns, kanban.read_bytes())
    assert before == after  # read-only projection


def test_delivery_v2_invalidates_task_map_sidecar_without_event(client, state_dir):
    path = "/api/projects/default/delivery-traces/F-1?contract=v2&view=overview"
    before = client.get(path).json()
    task_map = state_dir / "artifacts" / "F-1" / "task_map.json"
    data = json.loads(task_map.read_text())
    data["tasks"].append({"task_id": "T3", "owner_role": "dev", "wave": 3})
    task_map.write_text(json.dumps(data))
    after = client.get(path).json()
    assert before["as_of_seq"] == after["as_of_seq"]
    assert before["execution_graph"]["task_count"] == 2
    assert after["execution_graph"]["task_count"] == 3


def test_delivery_v2_tracks_external_referenced_task_map(client, state_dir, tmp_path):
    external = tmp_path / "plan.json"
    external.write_text(json.dumps({"tasks": [{"task_id": "T1"}]}))
    log = event_log_from_project(state_dir, config=None, warn=False)
    log.append(ZfEvent(type="task_map.ready", payload={"feature_id": "F-1", "task_map_ref": str(external)}))
    path = "/api/projects/default/delivery-traces/F-1?contract=v2&view=overview"
    before = client.get(path).json()
    external.write_text(json.dumps({"tasks": [{"task_id": "T1"}, {"task_id": "T2"}]}))
    after = client.get(path).json()
    assert before["as_of_seq"] == after["as_of_seq"]
    assert before["execution_graph"]["task_count"] == 1
    assert after["execution_graph"]["task_count"] == 2


def test_delivery_v2_retries_when_task_changes_during_build(client, state_dir, monkeypatch):
    from zf.web import delivery_trace_routes as routes
    original = routes.build_delivery_view
    calls = []
    def changing(**kwargs):
        value = original(**kwargs)
        calls.append(1)
        if len(calls) == 1:
            TaskStore(state_dir / "kanban.json").update("T2", status="done")
        return value
    monkeypatch.setattr(routes, "build_delivery_view", changing)
    response = client.get("/api/projects/default/delivery-traces/F-1?contract=v2&view=overview")
    assert response.status_code == 200
    assert len(calls) == 2
    assert response.json()["execution_graph"]["done_count"] == 2
