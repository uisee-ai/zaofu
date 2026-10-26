"""The Observability route owns exactly its page data and complete metrics."""

from pathlib import Path

from fastapi.testclient import TestClient

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.web import server


def test_scoped_observability_keeps_metrics_without_building_board(tmp_path, monkeypatch):
    EventLog(tmp_path / "events.jsonl").append(ZfEvent(type="run.started"))
    (tmp_path / "kanban.json").write_text("[]")
    samples = {
        "metrics_snapshot": {"throughput_per_hour": 1.23456789, "tasks_done": 17, "orchestrator_agent": {"cost_usd": None}},
        "fleet_stats": {"task_flow": {"done": 17}},
        "provider_health": {"observations": 4},
        "long_run_truth": {"status": "completed"},
        "traces": [{"trace_id": "trace", "event_count": 42}],
        "fanouts": [{"fanout_id": "f", "children": [{"report": {"body": "detail"}}]}],
        "candidates": [{"id": "c"}],
        "runs": [{"run_id": "r"}],
        "active_runs": [{"run_id": "a"}],
        "agents": [{"instance_id": "dev", "context_usage_ratio": 0.123456789}],
        "runtime": {"live": True, "actions": {"mutation_enabled": False}},
        "mutation_audit": {"entries": [{"id": "audit"}]},
        "worktree_drift": {"entries": [{"action_required": True}]},
        "cost": {"total_usd": 0.00000123, "input_tokens": 123456789},
    }
    names = {
        "metrics_snapshot": "_metrics_snapshot_projection", "fleet_stats": "_fleet_stats_projection",
        "provider_health": "_provider_health_projection", "long_run_truth": "_read_long_run_truth",
        "traces": "_traces", "fanouts": "_fanouts", "candidates": "_candidates", "agents": "_agents",
        "runtime": "_runtime", "mutation_audit": "project_mutation_audit", "worktree_drift": "project_worktree_drift_audit", "cost": "_cost",
    }
    calls = []
    for key, name in names.items():
        def build(*args, _key=key, **kwargs):
            calls.append(_key)
            return samples[_key]
        monkeypatch.setattr(server, name, build)
    monkeypatch.setattr(server, "_runs_index", lambda *a, **kw: {"runs": samples["runs"]})
    monkeypatch.setattr(server, "_active_runs", lambda *a, **kw: {"active_runs": samples["active_runs"]})
    client = TestClient(server.create_app(tmp_path))
    scoped = client.get("/api/projects/default/snapshot/observability")
    assert scoped.status_code == 200
    data = scoped.json()
    assert data["snapshot_slice"] == "observability-summary"
    for key, value in samples.items():
        if key != "runtime":
            assert data[key] == value, key
    assert data["runtime"]["live"] is True
    assert data["tasks"] == data["archive_tasks"] == data["automations"] == []
    before = len(calls)
    repeat = client.get("/api/projects/default/snapshot/observability", headers={"If-None-Match": scoped.headers["etag"]})
    assert repeat.status_code == 304
    assert len(calls) == before
    # The diagnostic full endpoint still exposes its original task projection.
    monkeypatch.setattr(server, "_kanban", lambda *a, **kw: [{"id": "full-detail"}])
    assert client.get("/api/projects/default/snapshot").json()["tasks"] == [{"id": "full-detail"}]


def test_operator_skill_catalog_does_not_analyze_invocation_history(tmp_path, monkeypatch):
    from zf.runtime import skill_invocation_projection
    from zf.web.projections.operator import _operator_skills_available
    from zf.web.projections.summaries import _skills

    called = []

    def invocation(*args, **kwargs):
        called.append(True)
        return {"invocations": [{"name": "test", "tokens": 100}]}

    monkeypatch.setattr(skill_invocation_projection, "project_skill_invocations", invocation)
    catalog = _operator_skills_available(tmp_path)
    assert catalog["pool_count"] == catalog["enabled_role_count"] == 0
    assert catalog["warnings"] == 0 and catalog["names"] == []
    assert not called
    detailed = _skills(tmp_path)
    assert detailed["invocation"] == {"invocations": [{"name": "test", "tokens": 100}]}
    assert called == [True]
