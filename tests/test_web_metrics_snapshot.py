"""Snapshot exposes kernel metrics_snapshot + fleet_stats (additive keys)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.web.server import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    sd = tmp_path / ".zf"
    sd.mkdir()
    store = TaskStore(sd / "kanban.json")
    store.add(Task(id="T1", title="t1", status="done", assigned_to="dev-1",
                   contract=TaskContract(feature_id="F-1", owner_role="dev")))
    store.add(Task(id="T2", title="t2", status="in_progress", assigned_to="dev-2",
                   contract=TaskContract(feature_id="F-1", owner_role="dev")))
    log = event_log_from_project(sd, config=None, warn=False)
    log.append(ZfEvent(type="task.dispatched", id="e-1", task_id="T1"))
    log.append(ZfEvent(type="task.done.accepted", id="e-2", task_id="T1"))
    return TestClient(create_app(sd))


def test_snapshot_has_metrics_snapshot(client: TestClient):
    data = client.get("/api/snapshot").json()
    metrics = data.get("metrics_snapshot")
    assert isinstance(metrics, dict) and metrics
    # kernel 12-metric fields surface as-is
    for key in ("throughput_per_hour", "rework_ratio", "cost_per_task",
                "avg_task_duration_minutes", "mtts", "stuck_recovery_rate"):
        assert key in metrics
    assert metrics["tasks_done"] == 1
    oa = metrics["orchestrator_agent"]
    assert oa["schema_version"] == "orchestrator-agent-metrics.v1"
    assert oa["summary"]["operation_count"] == 0


def test_snapshot_has_fleet_stats(client: TestClient):
    data = client.get("/api/snapshot").json()
    fleet = data.get("fleet_stats")
    assert isinstance(fleet, dict)
    flow = fleet.get("task_flow")
    assert flow and flow["schema_version"] == "task-flow-stats.v1"
    assert flow["done_24h"] == 1
    roles = {row["role"]: row for row in fleet.get("role_efficiency", [])}
    assert roles["dev"]["done"] == 1


def test_unknown_oa_pricing_does_not_erase_kernel_metrics(client: TestClient, tmp_path: Path):
    log = event_log_from_project(tmp_path / ".zf", config=None, warn=False)
    log.append(ZfEvent(type="workflow.operation.requested", payload={
        "operation_id": "op", "operation_type": "orchestrator_agent_semantic",
    }))
    log.append(ZfEvent(type="agent.usage", payload={
        "operation_id": "op", "model": "unlisted-local-model",
        "usage": {"input_tokens": 17, "output_tokens": 3},
    }))
    metrics = client.get("/api/snapshot").json()["metrics_snapshot"]
    assert metrics["tasks_done"] == 1
    assert "throughput_per_hour" in metrics and "error" not in metrics
    assert metrics["orchestrator_agent"]["summary"]["total_tokens"] == 20
    assert metrics["orchestrator_agent"]["summary"]["cost_usd"] is None
    assert metrics["orchestrator_agent"]["summary"]["unpriced_sample_count"] == 1


def test_auxiliary_metric_failure_is_scoped(client: TestClient, monkeypatch):
    from zf.runtime import orchestrator_agent_metrics

    def failed(*args, **kwargs):
        raise ValueError("auxiliary metric unavailable")

    monkeypatch.setattr(orchestrator_agent_metrics, "build_orchestrator_agent_metrics", failed)
    metrics = client.get("/api/snapshot").json()["metrics_snapshot"]
    assert metrics["tasks_done"] == 1
    assert "throughput_per_hour" in metrics and "error" not in metrics
    assert metrics["orchestrator_agent"] == {
        "projection": "orchestrator_agent", "error": "auxiliary metric unavailable",
    }
