"""Real Web/process wiring plus bounded admission and complete generations."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import signal
import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.locks import locked_path
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.web import dashboard_projection as dashboard
from zf.web.projection_worker import ProjectionJob, ProjectionWorker
from zf.web.projections import dashboard_store as store
from tests.dashboard_projection_helpers import ready_get


def seed(tmp_path):
    state = tmp_path / "custom-state"
    state.mkdir()
    TaskStore(state / "kanban.json").add(Task(id="T1", title="one", status="pending"))
    EventLog(state / "events.jsonl").append(ZfEvent(type="run.started", correlation_id="run-one"))
    return state


def wait_for(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "projection did not settle"
        time.sleep(0.02)


def test_web_uses_child_and_serves_requests_during_build_then_recovers(tmp_path):
    from zf.web.server import create_app
    state = seed(tmp_path)
    original_events = (state / "events.jsonl").read_bytes()
    app = create_app(state, project_root=tmp_path)
    path = "/api/projects/default/snapshot/board"
    with TestClient(app) as client:
        worker = app.state.projection_worker
        # Hold the cross-process build lock: the HTTP caller must not join it.
        with locked_path(state / "projections/dashboard-board"):
            first = client.get(path)
            assert first.status_code == 202 and "tasks" not in first.json()
            wait_for(lambda: worker.status()["pid"] is not None)
            pid = worker.status()["pid"]
            assert pid != os.getpid()
            with ThreadPoolExecutor(max_workers=1) as pool:
                health = pool.submit(client.get, "/health").result(timeout=2)
            assert health.status_code == 200
            assert health.json()["projection_worker"]["running"]
            assert client.get(path).status_code == 202
            assert worker.status()["queued"] == 0
        ready = ready_get(client, path)
        assert ready.status_code == 200, ready.text
        assert ready.json()["tasks"][0]["id"] == "T1"
        assert ready.json()["read_projection"]["state"] == "ready"
        assert (state / "events.jsonl").read_bytes() == original_events

        # Kill only this test's child. Persisted data remains immediately usable.
        os.kill(pid, signal.SIGTERM)
        wait_for(lambda: not worker._process.is_alive())
        assert client.get(path).json()["tasks"] == ready.json()["tasks"]
        TaskStore(state / "kanban.json").update("T1", title="after restart")
        updated = ready_get(client, path)
        assert updated.status_code == 200, updated.text
        assert updated.json()["tasks"][0]["title"] == "after restart"
        assert worker.status()["pid"] != pid
    assert not worker.started
    assert worker.status()["pid"] is None


def test_agents_process_result_preserves_metrics_and_store_only_changes(tmp_path):
    from zf.web.server import create_app
    from zf.web.agent_monitoring import build_agent_monitoring
    state = seed(tmp_path)
    app = create_app(state, project_root=tmp_path)
    with TestClient(app) as client:
        path = "/api/projects/default/agent-monitoring"
        result = ready_get(client, path)
        assert result.status_code == 200, result.text
        value = result.json()
        oracle = build_agent_monitoring(state, project_id="default", project_root=tmp_path)
        for field in ("metrics_snapshot", "cost", "agent_live"):
            assert value[field] == oracle[field], field
        assert value["read_projection"]["state"] == "ready"
        assert app.state.projection_worker.status()["pid"] != os.getpid()
        TaskStore(state / "kanban.json").update("T1", status="in_progress")
        latest = ready_get(client, path).json()
        assert latest["source_seq"] == value["source_seq"]
        assert latest["source_version"] != value["source_version"]


def test_queue_is_bounded_and_inflight_updates_are_coalesced(tmp_path, monkeypatch):
    worker = ProjectionWorker(capacity=2)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def execute(job):
        calls.append((job.group, job.trigger))
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return {"state": "ready"}
    monkeypatch.setattr(worker, "_execute", execute)
    worker.start()
    one = ProjectionJob(tmp_path, tmp_path, "board")
    two = ProjectionJob(tmp_path, tmp_path, "pulse")
    try:
        worker.request(one, "v1")
        assert entered.wait(2)
        for _ in range(20):
            worker.request(one, "v1")
        assert worker.status()["queued"] == 0
        worker.request(one, "v2")
        worker.request(one, "v3")
        worker.request(two, "v1")
        assert worker.request(ProjectionJob(tmp_path, tmp_path, "other"), "v1")["queue_full"]
        assert worker.status()["queued"] == 2
        release.set()
        wait_for(lambda: len(calls) == 3 and not worker.status()["running"])
        assert calls == [("board", "v1"), ("board", "v3"), ("pulse", "v1")]
    finally:
        release.set()
        worker.stop()


def test_changed_source_never_replaces_previous_generation(tmp_path):
    state = seed(tmp_path)
    old = {"metrics_snapshot": {"cost": 0.123456789, "tokens": 123456789}}
    dashboard.rebuild_group(state, tmp_path, None, "board", lambda: old)
    TaskStore(state / "kanban.json").update("T1", title="changed")
    def build():
        EventLog(state / "events.jsonl").append(ZfEvent(type="run.started", correlation_id="run-two"))
        return {"metrics_snapshot": {"cost": 999}}
    with pytest.raises(dashboard.ProjectionSourceChanged):
        dashboard.rebuild_group(state, tmp_path, None, "board", build)
    assert store.read_group(state, "board")[0] == old


def test_agent_source_movement_remains_refreshing_instead_of_a_worker_error(tmp_path, monkeypatch):
    from zf.web import agent_monitoring
    from zf.web.projection_jobs import build_job
    def moving(*args, **kwargs):
        raise agent_monitoring.MonitoringSourceChanged()
    monkeypatch.setattr(agent_monitoring, "build_agent_monitoring", moving)
    with pytest.raises(dashboard.ProjectionSourceChanged):
        build_job(ProjectionJob(tmp_path, tmp_path, "agent-monitoring:p", project_id="p"))


def test_queued_obsolete_generation_is_discarded_and_next_read_recovers(tmp_path):
    state = seed(tmp_path)
    worker = ProjectionWorker()
    job = ProjectionJob(state, tmp_path, "board")
    worker.start()
    try:
        worker.request(job, "obsolete-source")
        wait_for(lambda: bool(worker.result(job.key)))
        assert worker.result(job.key)["state"] == "refreshing"
        assert store.read_group(state, "board") is None
        worker.request(job, store.quick_fingerprint(state, tmp_path, None))
        wait_for(lambda: worker.result(job.key).get("state") == "ready")
        assert store.read_group(state, "board")[0]["tasks"][0]["id"] == "T1"
    finally:
        worker.stop()


def test_stop_reaps_a_child_waiting_on_a_build_lock(tmp_path):
    state = seed(tmp_path)
    worker = ProjectionWorker()
    worker.start()
    with locked_path(state / "projections/dashboard-board"):
        worker.request(ProjectionJob(state, tmp_path, "board"), store.quick_fingerprint(state, tmp_path, None))
        wait_for(lambda: worker.status()["pid"] is not None)
        pid = worker.status()["pid"]
        worker.stop()
    assert not worker.started
    assert worker.status()["pid"] is None
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_spawn_failure_does_not_kill_coordinator_or_leak_pipe(tmp_path, monkeypatch):
    from zf.web import projection_worker as module
    state = seed(tmp_path)
    real = module.multiprocessing.get_context("spawn")
    starts, closed = [], []
    class Unstarted:
        pid = None
        def start(self):
            raise OSError("simulated process resource exhaustion")
        def close(self):
            closed.append(True)
    def process(**kwargs):
        starts.append(True)
        return Unstarted() if len(starts) == 1 else real.Process(**kwargs)
    monkeypatch.setattr(module.multiprocessing, "get_context", lambda _: SimpleNamespace(Pipe=real.Pipe, Process=process))
    worker = ProjectionWorker()
    worker.start()
    job = ProjectionJob(state, tmp_path, "board")
    source = store.quick_fingerprint(state, tmp_path, None)
    try:
        worker.request(job, source)
        wait_for(lambda: worker.result(job.key).get("state") == "error")
        assert worker.started and closed == [True]
        time.sleep(1.05)  # Error retry backoff, not a new scheduling authority.
        worker.request(job, source)
        wait_for(lambda: worker.result(job.key).get("state") == "ready")
        assert store.read_group(state, "board")[0]["tasks"][0]["id"] == "T1"
    finally:
        worker.stop()
