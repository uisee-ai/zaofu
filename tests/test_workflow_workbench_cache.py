"""Request graph cache parity, source invalidation and cold/hot read behavior."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from zf.core.config.loader import load_config
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskExecutionBinding
from zf.core.task.store import TaskStore
from zf.runtime.sdlc_graph import build_sdlc_graph
from zf.runtime.workflow_requests import mark_workflow_request
from zf.web.projections import read_model, workflow_workbench_cache as cache
from zf.web.projections.workflow_workbench_sources import read_workbench_sources
from tests.test_workflow_preview_review import setup  # noqa: F401


def url(binding):
    return f"/api/projects/default/workflow-requests/{binding['request_id']}/graph"


def submit(service, binding, run="run-a"):
    for status in ("approved", "submitted"):
        mark_workflow_request(service.state_dir, binding["request_id"],
                              status=status, actor="test", run_id=run)


def oracle(client, service, binding, as_of=0):
    context = SimpleNamespace(state_dir=service.state_dir, project_root=service.project_root,
                              config=load_config(service.config_path))
    body = client.get(url(binding).removesuffix("/graph")).json()
    from zf.core.security.redaction import redact_obj
    return redact_obj(build_sdlc_graph(read_workbench_sources(context, body, as_of=as_of)))


def test_hot_read_and_new_app_reuse_sqlite_without_hydrating_or_rebuilding(setup, monkeypatch):
    client, service, binding = setup
    first = client.get(url(binding))
    assert first.status_code == 200, first.text
    assert first.json() == oracle(client, service, binding)
    with sqlite3.connect(read_model.db_path(service.state_dir)) as conn:
        assert conn.execute("SELECT count(*) FROM projection_cache WHERE kind='workflow-workbench'").fetchone()[0] == 1
    fail = Mock(side_effect=AssertionError("hot read rebuilt the graph"))
    monkeypatch.setattr(cache, "read_workbench_sources", fail)
    monkeypatch.setattr(cache, "build_sdlc_graph", fail)
    monkeypatch.setattr(cache, "event_log_from_project", fail)
    from zf.web.server import create_app
    fresh = TestClient(create_app(service.state_dir, config=load_config(service.config_path),
                                  project_root=service.project_root))
    assert fresh.get(url(binding)).json() == first.json()
    unchanged = fresh.get(url(binding), headers={"If-None-Match": first.headers["etag"]})
    assert unchanged.status_code == 304 and unchanged.content == b""
    assert unchanged.headers["etag"] == first.headers["etag"]
    fail.assert_not_called()


def test_cold_dependency_discovery_reuses_the_same_unchanged_ledger(setup, monkeypatch):
    client, service, binding = setup
    factory = cache.event_log_from_project
    reads = []
    def observed(*args, **kwargs):
        log = factory(*args, **kwargs)
        def read():
            reads.append(True)
            return log.read_all()
        return SimpleNamespace(read_all=read)
    monkeypatch.setattr(cache, "event_log_from_project", observed)
    monkeypatch.setattr(read_model, "hydrate_events_by_ref", Mock(side_effect=AssertionError("decoded ledger was indexed again")))
    response = client.get(url(binding))
    assert response.status_code == 200, response.text
    assert response.json() == oracle(client, service, binding)
    assert len(reads) == 1


def test_event_and_history_generations_remain_exact_and_isolated(setup):
    client, service, binding = setup
    submit(service, binding)
    first = client.get(url(binding))
    service.writer.append(ZfEvent(id="foreign", type="run.goal.blocked", origin="kernel",
                                 payload={"workflow_run_id": "run-b"}))
    service.writer.append(ZfEvent(id="ours", type="run.goal.blocked", origin="kernel",
                                 payload={"workflow_run_id": "run-a"}))
    latest = client.get(url(binding), headers={"If-None-Match": first.headers["etag"]})
    assert latest.status_code == 200 and latest.json() == oracle(client, service, binding)
    nodes = {n["id"]: n for n in latest.json()["nodes"]}
    assert nodes["run:run-a"]["status"] == "blocked"
    assert "run:run-b" not in nodes and "event:foreign" not in nodes
    historical = client.get(url(binding) + "?as_of=1")
    assert historical.json() == oracle(client, service, binding, as_of=1)
    assert historical.headers["etag"] != latest.headers["etag"]
    assert client.get(url(binding)).json() == latest.json()


@pytest.mark.parametrize("archived", [False, True])
def test_store_only_changes_including_archives_invalidate(setup, archived):
    client, service, binding = setup
    submit(service, binding)
    service.writer.append(ZfEvent(type="task.created", task_id="T-CACHE",
                                 correlation_id="run-a", payload={"workflow_run_id": "run-a"}))
    first = client.get(url(binding))
    store = TaskStore(service.state_dir / "kanban.json")
    store.add(Task(id="T-CACHE", title="New task", status="done" if archived else "pending",
                   execution_binding=TaskExecutionBinding(workflow_run_id="run-a")))
    updated = client.get(url(binding), headers={"If-None-Match": first.headers["etag"]})
    assert updated.status_code == 200 and updated.json() == oracle(client, service, binding)
    assert any(node["id"] == "task:T-CACHE" for node in updated.json()["nodes"])


@pytest.mark.parametrize("external", [False, True])
def test_plan_sidecar_edit_delete_and_repair_without_events_invalidates(setup, external):
    client, service, binding = setup
    submit(service, binding)
    path = (service.project_root / "docs/plan-cache.md" if external
            else service.state_dir / "artifacts/plan-cache.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# First plan\n")
    service.writer.append(ZfEvent(type="plan.approval.requested", payload={
        "workflow_run_id": "run-a", "plan_id": "PLAN-CACHE", "plan_artifact_ref": str(path),
    }))
    previous = client.get(url(binding))
    assert previous.json()["plans"][0]["markdown"] == "# First plan\n"
    for content in ("# Revised plan\n", None, "# Repaired plan\n"):
        if content is None:
            path.unlink()
        else:
            path.write_text(content)
        response = client.get(url(binding), headers={"If-None-Match": previous.headers["etag"]})
        assert response.status_code == 200, response.text
        assert response.json() == oracle(client, service, binding)
        if content:
            assert response.json()["plans"][0]["markdown"] == content
        previous = response


def test_conditional_read_does_not_deserialize_cached_graph(setup, monkeypatch):
    client, service, binding = setup
    first = client.get(url(binding))
    original = read_model.get_cached_projection
    def metadata_only(state, key, **kwargs):
        assert key.endswith(":source"), "304 read loaded the full graph body"
        return original(state, key, **kwargs)
    monkeypatch.setattr(read_model, "get_cached_projection", metadata_only)
    response = client.get(url(binding), headers={"If-None-Match": first.headers["etag"]})
    assert response.status_code == 304 and not response.content


def test_sqlite_write_failure_still_returns_canonical_graph(setup, monkeypatch):
    client, service, binding = setup
    monkeypatch.setattr(read_model, "set_cached_projection", Mock(side_effect=sqlite3.OperationalError("locked")))
    response = client.get(url(binding))
    assert response.status_code == 200, response.text
    assert response.json() == oracle(client, service, binding)


def test_nested_package_artifact_changes_invalidate_without_ledger_changes(setup):
    client, service, binding = setup
    submit(service, binding)
    plan = service.project_root / "docs/nested-plan.md"
    plan.write_text("# Bound Plan\n")
    package = service.state_dir / "artifacts/nested-package.json"
    package.write_text(json.dumps({
        "schema_version": "plan-preview-bundle.v1", "workflow_run_id": "run-a",
        "artifacts": [{"name": "accepted_plan", "ref": str(plan),
                       "sha256": hashlib.sha256(plan.read_bytes()).hexdigest()}],
    }))
    service.writer.append(ZfEvent(type="plan.approval.requested", payload={
        "workflow_run_id": "run-a", "plan_id": "NESTED",
        "plan_artifact_package_ref": str(package),
        "plan_artifact_package_digest": hashlib.sha256(package.read_bytes()).hexdigest(),
    }))
    first = client.get(url(binding))
    assert first.status_code == 200 and first.json()["plans"][0]["markdown"] == "# Bound Plan\n"
    plan.write_text("# Tampered Plan\n")
    changed = client.get(url(binding), headers={"If-None-Match": first.headers["etag"]})
    assert changed.status_code == 200 and changed.json() == oracle(client, service, binding)
    assert changed.json()["plans"][0]["markdown"] != "# Bound Plan\n"


def test_changing_source_never_publishes_a_mixed_generation(setup, monkeypatch):
    client, service, binding = setup
    original = cache.build_sdlc_graph
    def changing(inputs):
        value = original(inputs)
        service.writer.append(ZfEvent(type="user.message", payload={"request_id": binding["request_id"]}))
        return value
    monkeypatch.setattr(cache, "build_sdlc_graph", changing)
    response = client.get(url(binding))
    assert response.status_code == 503 and response.headers["retry-after"] == "1"
    assert not read_model.db_path(service.state_dir).exists() or not any(
        key.startswith("workflow-workbench.") for key, in sqlite3.connect(
            read_model.db_path(service.state_dir)).execute("SELECT key FROM projection_cache"))


def test_concurrent_reads_share_a_stable_build(setup, monkeypatch):
    client, service, binding = setup
    # Warm once to discover dependencies; a source change now needs one rebuild.
    client.get(url(binding))
    service.writer.append(ZfEvent(type="user.message", payload={"request_id": binding["request_id"]}))
    counter = Mock(wraps=cache.build_sdlc_graph)
    monkeypatch.setattr(cache, "build_sdlc_graph", counter)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: client.get(url(binding)), range(4)))
    assert all(r.status_code == 200 for r in responses)
    assert all(r.json() == responses[0].json() for r in responses)
    assert counter.call_count == 1


def test_review_packet_generation_invalidates_previous_graph_cache_and_etag(setup, monkeypatch):
    client, service, binding = setup
    submit(service, binding)
    plan = service.project_root / "docs/lineage-cache-plan.md"
    plan.write_text("# Bound plan\n")
    service.writer.append(ZfEvent(type="plan.approval.requested", payload={
        "workflow_run_id": "run-a", "pdd_id": "TASK-42", "plan_id": "PLAN-LINEAGE",
        "plan_artifact_ref": str(plan),
    }))

    # A previous server persisted the old review representation under v2.
    build = cache.build_sdlc_graph
    def old_graph(inputs):
        graph = build(inputs)
        section = next(s for s in graph["plans"][0]["review_packet"]["sections"]
                       if s["id"] == "provenance")
        section["counts"]["lineage_ids"] = 1
        section["notes"] = ["run-a"]
        return graph

    with monkeypatch.context() as old:
        old.setattr(cache, "CACHE_VERSION", "workflow-workbench.v2")
        old.setattr(cache, "build_sdlc_graph", old_graph)
        stale = client.get(url(binding))
    assert stale.status_code == 200
    assert stale.json()["plans"][0]["review_packet"]["sections"][-1]["notes"] == ["run-a"]

    # Unchanged source files must not let the old persisted graph or its 304
    # receipt suppress recomputation after the projection implementation changes.
    latest = client.get(url(binding), headers={"If-None-Match": stale.headers["etag"]})
    assert latest.status_code == 200
    assert latest.headers["etag"] != stale.headers["etag"]
    assert latest.json() == oracle(client, service, binding)
    section = next(s for s in latest.json()["plans"][0]["review_packet"]["sections"]
                   if s["id"] == "provenance")
    assert section["counts"]["lineage_ids"] == 2
    assert "pdd_id=TASK-42" in section["notes"][0]
    assert client.get(url(binding), headers={"If-None-Match": latest.headers["etag"]}).status_code == 304
    with monkeypatch.context() as old:
        old.setattr(cache, "CACHE_VERSION", "workflow-workbench.v2")
        assert client.get(url(binding), headers={"If-None-Match": stale.headers["etag"]}).status_code == 304
