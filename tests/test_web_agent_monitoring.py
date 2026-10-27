"""Complete, coherent metrics at the actual Agents API; probes stay in details."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from zf.core.cost.tracker import CostTracker
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.agent_live import project_agent_live
from zf.runtime.operator_reliability import project_agent_cockpit
from zf.web import agent_monitoring, server


def _state(tmp_path):
    state = tmp_path / "custom-state"
    state.mkdir()
    TaskStore(state / "kanban.json").add(Task(id="T1", title="one", assigned_to="dev-1"))
    (state / "feature_list.json").write_text("[]")
    (state / "role_sessions.yaml").write_text("roles:\n  dev-1: session-one\ninstance_meta:\n  dev-1:\n    backend: codex\n")
    EventLog(state / "events.jsonl").append(ZfEvent(type="worker.heartbeat", actor="dev-1", task_id="T1",
                                                    payload={"context_usage_ratio": 0.9}))
    return state


def test_endpoint_metrics_match_existing_algorithms_without_git_or_debug(tmp_path, monkeypatch):
    state = _state(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("detail probe blocks metrics")
    for name in ("_workdirs", "_git_branch_or_ref", "_git_commit", "_git_dirty", "_agent_debug_projection", "_operator_skills_available"):
        monkeypatch.setattr(server, name, forbidden)
    client = TestClient(server.create_app(state, project_root=tmp_path))
    response = client.get("/api/projects/default/agent-monitoring")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "ready" and data["source_seq"] == 1
    assert data["source_version"]
    now = datetime.fromisoformat(data["generated_at"])
    events = EventLog(state / "events.jsonl").read_days(7)
    assert data["agent_live"] == project_agent_live(state, events=events)
    assert data["agent_cockpit"] == project_agent_cockpit(state, events=events, agents=data["agents"], now=now)
    assert any(row["instance_id"] == "dev-1" for row in data["agents"])


def test_store_cost_and_events_invalidate_the_same_generation_without_double_count(tmp_path):
    state = _state(tmp_path)
    def read():
        return agent_monitoring.build_agent_monitoring(state, project_id="p", project_root=tmp_path)
    first = read()
    tracker = CostTracker(state / "cost.jsonl")
    for sample in ("run-a-call-1", "run-a-call-1", "run-b-call-1"):
        tracker.record_usage("dev-1", 200, 30, provider_cost_usd=0.123456789,
                             source_event_id=sample, usage_sample_id=sample)
    # Span/root usage is observational: costs remain admitted CostTracker facts.
    log = EventLog(state / "events.jsonl")
    for name in ("child", "root"):
        log.append(ZfEvent(type="agent.output", actor="dev-1", correlation_id=name,
                          payload={"usage": {"total_tokens": 230}, "cost": 0.123456789}))
    second = read()
    assert second["source_seq"] == 3 and second["source_version"] != first["source_version"]
    assert second["cost"] == server._cost(state)
    assert second["agent_live"]["usage_by_role"]["dev-1"]["entries"] == 2
    assert second["agent_live"]["usage_by_role"]["dev-1"]["input_tokens"] == 400
    TaskStore(state / "kanban.json").update("T1", status="in_progress")
    third = read()
    assert third["source_seq"] == second["source_seq"]
    assert third["source_version"] != second["source_version"]
    assert third["cost"] == second["cost"]
    (state / "role_sessions.yaml").write_text("roles:\n  dev-2: session-two\ninstance_meta:\n  dev-2:\n    backend: codex\n")
    fourth = read()
    assert fourth["source_version"] != third["source_version"]
    assert any(row["instance_id"] == "dev-2" for row in fourth["agents"])
    from zf.core.cost.billing import BillingReconciliationStore
    billing = BillingReconciliationStore(state)
    for amount in (0.2, 0.3):
        billing.persist({"provider": "fixture", "billed_usd": amount})
        updated = read()
        assert updated["source_version"] != fourth["source_version"]
        assert updated["cost"]["reconciliation"]["billed_usd"] == amount
        fourth = updated


def test_concurrent_change_retries_and_persistent_changes_return_503(tmp_path, monkeypatch):
    state = _state(tmp_path)
    original = server._agents
    calls = []
    def change_once(*args, **kwargs):
        agents = original(*args, **kwargs)
        calls.append(1)
        if len(calls) == 1:
            EventLog(state / "events.jsonl").append(ZfEvent(type="worker.stuck", actor="dev-1"))
        return agents
    monkeypatch.setattr(server, "_agents", change_once)
    data = agent_monitoring.build_agent_monitoring(state, project_id="p", project_root=tmp_path)
    assert len(calls) == 2 and data["source_seq"] == 2
    assert data["agent_cockpit"]["summary"]["stuck"] == 1
    versions = iter(str(i) for i in range(10))
    monkeypatch.setattr(agent_monitoring, "fingerprint", lambda *a: next(versions))
    response = TestClient(server.create_app(state, project_root=tmp_path)).get("/api/projects/default/agent-monitoring")
    assert response.status_code == 503
    assert "agents" not in response.json()


def test_metrics_keep_clock_ages_fresh_and_projects_isolated(tmp_path):
    one = _state(tmp_path)
    two_root = tmp_path / "other"
    two_root.mkdir()
    two = _state(two_root)
    EventLog(two / "events.jsonl").append(ZfEvent(type="worker.stuck", actor="dev-1"))
    a = agent_monitoring.build_agent_monitoring(one, project_id="a", project_root=tmp_path)
    b = agent_monitoring.build_agent_monitoring(two, project_id="b", project_root=two_root)
    assert a["source_version"] != b["source_version"]
    assert a["agent_cockpit"]["summary"]["stuck"] == 0
    assert b["agent_cockpit"]["summary"]["stuck"] == 1
    again = agent_monitoring.build_agent_monitoring(one, project_id="a", project_root=tmp_path)
    assert again["source_version"] == a["source_version"]
    assert again["generated_at"] > a["generated_at"]


@pytest.mark.parametrize("core_available", [False, True])
def test_cost_read_failure_is_not_admitted_as_zero_metrics(tmp_path, monkeypatch, core_available):
    from zf.web.projections.metric_costs import ProjectedCostTracker
    state = _state(tmp_path)
    CostTracker(state / "cost.jsonl").record_usage("dev-1", 10, 2, provider_cost_usd=0.1)
    def unavailable(*args, **kwargs):
        raise OSError("cost temporarily unavailable")
    monkeypatch.setattr(ProjectedCostTracker, "per_role_totals", unavailable)
    if core_available:
        monkeypatch.setattr(server, "_metrics_snapshot_projection", lambda *a, **kw: {})
    response = TestClient(server.create_app(state, project_root=tmp_path)).get("/api/projects/default/agent-monitoring")
    assert response.status_code == 503
    assert "cost" not in response.json()
