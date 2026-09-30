"""Cancellation sync publishes one confirmed mirror snapshot, never partial rows."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest

from tests.test_external_issue_ingress import _mirror, _service
from zf.core.issue_triage.store import IssueMirrorStore
from zf.core.state.locks import locked_path


def cancelled_pair(tmp_path, writer):
    first = _mirror(created_at="2026-09-01T00:00:00Z", labels=("bug", "triage_queued"))
    first = replace(first, label_colors={"bug": "D73A4A", "triage_queued": "123456"})
    service, _ = _service(tmp_path, first, lifecycle_label_writer=writer)
    second = replace(first, issue_key="github:123:9", number=9, node_id="I_node_9",
                     html_url="https://github.com/rzwu-uisee/zaofu-self-issue/issues/9")
    service.mirrors.upsert(second, "Second issue body")
    for item in (first, second):
        service.writer.emit("external_issue.triage.queued_cancelled", actor="test", payload={
            "source_key": item.issue_key, "workflow_run_id": f"RUN-{item.number}", "source_revision": "r1",
        })
    return service


def test_slow_batch_publishes_all_names_and_colors_in_one_store_write(tmp_path, monkeypatch):
    fast_done, slow_started, release = Event(), Event(), Event()
    def writer(mirror, desired):
        if mirror.number == 8:
            fast_done.set()
        else:
            slow_started.set()
            assert release.wait(5)
        return {"status": "reconciled", "label_colors": {desired: "D73A4A", "bug": "000000"}}
    service = cancelled_pair(tmp_path, writer)
    published = []
    original = service.mirrors.project_lifecycle_labels
    def publish(updates):
        published.append(dict(updates))
        return original(updates)
    monkeypatch.setattr(service.mirrors, "project_lifecycle_labels", publish)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.reconcile_queue_cancellations)
        try:
            assert fast_done.wait(3) and slow_started.wait(3)
            assert not future.done()
            assert published == []
            assert all(item.labels == ("bug", "triage_queued") for item in service.mirrors.list())
            # A concurrent upstream refresh cannot expose half the remote batch.
            with pytest.raises(TimeoutError):
                with locked_path(service.mirrors.refresh_lock_path, timeout_seconds=0):
                    pass
        finally:
            release.set()
        assert future.result()["status"] == "reconciled"
    assert len(published) == 1 and len(published[0]) == 2
    for item in IssueMirrorStore(service.state_dir).list():
        assert item.labels == ("bug", "triage_cancelled")
        assert item.label_colors == {"bug": "D73A4A", "triage_cancelled": "D73A4A"}
    assert service.mirrors.read_body(service.mirrors.get(9)) == "Second issue body"


def test_batch_does_not_project_failed_writes_and_retries_only_pending(tmp_path):
    failed = True
    calls = []
    def writer(mirror, desired):
        calls.append(mirror.number)
        if failed and mirror.number == 9:
            return {"status": "outcome_unknown", "reason": "timeout"}
        return {"status": "reconciled", "label_colors": {desired: "D73A4A"}}
    service = cancelled_pair(tmp_path, writer)
    assert service.reconcile_queue_cancellations()["pending"] == 1
    assert service.mirrors.get(8).labels == ("bug", "triage_cancelled")
    assert service.mirrors.get(9).labels == ("bug", "triage_queued")
    calls.clear()
    failed = False
    assert service.reconcile_queue_cancellations()["status"] == "reconciled"
    assert calls == [9]
    assert all(item.labels == ("bug", "triage_cancelled") for item in service.mirrors.list())


def test_batch_does_not_overwrite_new_admission(tmp_path):
    calls = []
    def writer(mirror, desired):
        calls.append(mirror.number)
        return {"status": "reconciled", "label_colors": {desired: "D73A4A"}}
    service = cancelled_pair(tmp_path, writer)
    service.writer.emit("external_issue.triage.queued", actor="test", payload={"source_key": "github:123:9"})
    service.reconcile_queue_cancellations()
    assert calls == [8]
    assert service.mirrors.get(9).labels == ("bug", "triage_queued")


def test_intake_lock_contention_is_busy_not_evidence_failure(tmp_path, monkeypatch):
    service = cancelled_pair(tmp_path, lambda *_: {"status": "reconciled"})
    with locked_path(service.state_dir / "external-issues/intake"):
        result = service.process_mirrors()
        assert result.ok and result.status == "syncing" and not result.error
        assert service.reconcile_queue_cancellations()["status"] == "syncing"
    def fail(**_):
        raise TimeoutError("real capture timeout")
    monkeypatch.setattr(service, "_process_mirrors_locked", fail)
    with pytest.raises(TimeoutError, match="real capture timeout"):
        service.process_mirrors()


def test_refresh_lock_contention_is_retryable_without_partial_updates(tmp_path):
    service = cancelled_pair(tmp_path, lambda *_: pytest.fail("must not write during upstream refresh"))
    with locked_path(service.mirrors.refresh_lock_path):
        assert service.reconcile_queue_cancellations()["status"] == "syncing"
        assert service.process_mirrors().status == "syncing"
    assert all(item.labels == ("bug", "triage_queued") for item in service.mirrors.list())


def test_github_color_reaches_mirror_through_production_writer(tmp_path, monkeypatch):
    from zf.integrations.forge.base import ForgeResult
    from zf.integrations.forge.github import GitHubComProvider
    from zf.runtime.external_issue_ingress import build_github_lifecycle_label_writer
    monkeypatch.setattr("zf.runtime.issue_candidate_delivery._github_access_token", lambda: "test")
    def reconcile(self, project, number, state, *, access_token):
        assert access_token == "test"
        return ForgeResult(status="reconciled", label_colors={state: "B60205"})
    monkeypatch.setattr(GitHubComProvider, "reconcile_lifecycle_label", reconcile)
    service = cancelled_pair(tmp_path, build_github_lifecycle_label_writer())
    assert service.reconcile_queue_cancellations()["status"] == "reconciled"
    assert all(item.label_colors["triage_cancelled"] == "B60205" for item in service.mirrors.list())


def test_cross_project_cancel_event_reconciles_viewer_mirror(tmp_path, monkeypatch):
    """A viewer project must publish cancellation owned by another project."""
    from zf.core.events import EventLog, EventWriter
    import zf.runtime.external_issue_label_sync as label_sync

    calls = []
    def writer(mirror, desired):
        calls.append((mirror.number, desired))
        return {"status": "reconciled", "label_colors": {desired: "D73A4A"}}

    service = cancelled_pair(tmp_path, writer)
    (tmp_path / "owner").mkdir(exist_ok=True)
    owner_log = EventLog(tmp_path / "owner" / "events.jsonl")
    owner = EventWriter(owner_log)
    owner.emit("external_issue.triage.queued", actor="kernel", payload={
        "source_key": "github:123:8", "workflow_run_id": "RUN-8",
    })
    owner.emit("run.cancelled", actor="operator", payload={"run_id": "RUN-8"})
    monkeypatch.setattr(label_sync, "_workspace_event_logs", lambda _service: [
        service.writer.event_log, owner_log,
    ])
    assert service.reconcile_queue_cancellations()["status"] == "reconciled"
    assert set(calls) == {(8, "triage_cancelled"), (9, "triage_cancelled")}


def test_run_cancelled_reconciles_without_queued_cancel_event(tmp_path, monkeypatch):
    """Terminal run cancellation also converges the remote lifecycle label."""
    from zf.core.events import EventLog, EventWriter
    import zf.runtime.external_issue_label_sync as label_sync

    calls = []
    service = cancelled_pair(
        tmp_path,
        lambda mirror, desired: calls.append((mirror.number, desired)) or {
            "status": "reconciled", "label_colors": {desired: "D73A4A"},
        },
    )
    # Remove the fixture's queued-cancel events from consideration by using a
    # separate owner ledger containing only the admission and terminal event.
    owner_dir = tmp_path / "owner-run"
    owner_dir.mkdir()
    owner_log = EventLog(owner_dir / "events.jsonl")
    owner = EventWriter(owner_log)
    owner.emit("external_issue.triage.queued", actor="kernel", payload={
        "source_key": "github:123:8", "workflow_run_id": "RUN-8",
    })
    owner.emit("run.cancelled", actor="operator", payload={"run_id": "RUN-8"})
    viewer_log = EventLog(tmp_path / "viewer-only.jsonl")
    monkeypatch.setattr(label_sync, "_workspace_event_logs", lambda _service: [
        viewer_log, owner_log,
    ])
    # Only the owner cancellation is pending in this isolated view.
    assert service.reconcile_queue_cancellations()["status"] == "reconciled"
    assert calls == [(8, "triage_cancelled")]


@pytest.mark.parametrize("cancel_type", ["run.cancelled", "external_issue.triage.queued_cancelled"])
@pytest.mark.parametrize("cross_project", [False, True])
def test_delayed_old_run_cancellation_cannot_cancel_new_admission(tmp_path, monkeypatch, cancel_type, cross_project):
    from zf.core.events import EventLog, EventWriter
    import zf.runtime.external_issue_label_sync as label_sync

    service, _ = _service(tmp_path, replace(_mirror(created_at="2026-09-01T00:00:00Z", labels=("triage_queued",)),
                                                        label_colors={"triage_queued": "2563EB"}),
                          lifecycle_label_writer=lambda *_: pytest.fail("new run must stay queued"))
    owner = service.writer
    viewer = EventWriter(EventLog(tmp_path / "viewer.jsonl")) if cross_project else owner
    key = "github:123:8"
    owner.emit("external_issue.triage.queued", actor="test", payload={"source_key": key, "workflow_run_id": "OLD"})
    viewer.emit("workflow.invoke.requested", actor="test", payload={"source_key": key, "workflow_run_id": "NEW"})
    owner.emit(cancel_type, actor="test", payload={"source_key": key, "run_id": "OLD"})
    monkeypatch.setattr(label_sync, "_workspace_event_logs", lambda _: list({id(w.event_log): w.event_log for w in (owner, viewer)}.values()))
    assert service.reconcile_queue_cancellations()["status"] == "reconciled"
    assert service.mirrors.get(8).labels == ("triage_queued",)


def test_fix_run_cancelled_resolves_issue_from_task_binding(tmp_path):
    calls = []
    service, _ = _service(tmp_path, _mirror(created_at="2026-09-01T00:00:00Z", labels=("fix_queued",)),
                          lifecycle_label_writer=lambda m, s: calls.append(s) or {"status": "reconciled"})
    service.writer.emit("external_issue.triage.queued", actor="test", task_id="TASK", payload={
        "source_key": "github:123:8", "workflow_run_id": "TRIAGE",
    })
    service.writer.emit("workflow.invoke.requested", actor="test", task_id="TASK", payload={
        "workflow_run_id": "FIX", "flow_kind": "issue",
    })
    service.writer.emit("run.cancelled", actor="test", payload={"run_id": "FIX"})
    assert service.reconcile_queue_cancellations()["status"] == "reconciled"
    assert calls == ["fix_cancelled"]


@pytest.mark.parametrize("ingress_enabled", [False, True])
def test_refresh_api_discovers_registered_owner_and_publishes_one_batch(tmp_path, monkeypatch, ingress_enabled):
    """Exercise real registry resolution + refresh route, not an injected ledger."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from zf.core.events import EventLog, EventWriter
    from zf.core.workspace.registry import WorkspaceRegistry, WorkspaceProject
    from tests.test_issue_triage_mirror import configured_context
    from zf.web.issue_triage_routes import build_issue_triage_router

    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace"))
    owner_root = tmp_path / "owner"
    owner_root.mkdir()
    config_path = owner_root / "zf.yaml"
    config_path.write_text("project:\n  name: owner\n  state_dir: .custom-state\n")
    owner_state = owner_root / ".custom-state"
    owner_state.mkdir()
    WorkspaceRegistry().upsert(WorkspaceProject(
        project_id="owner", name="owner", root=str(owner_root), config_path=str(config_path),
        state_dir_hint=str(owner_root / "WRONG-HINT"), last_opened_at="",
    ))
    owner = EventWriter(EventLog(owner_state / "events.jsonl"))
    ctx = configured_context(tmp_path / "viewer")
    ctx.config.self_issue = replace(ctx.config.self_issue, ingress=replace(ctx.config.self_issue.ingress, enabled=ingress_enabled))
    store = IssueMirrorStore(ctx.state_dir)
    for number, cancel_type in [(8, "external_issue.triage.queued_cancelled"), (9, "run.cancelled")]:
        item = replace(_mirror(created_at="2026-09-01T00:00:00Z", labels=("bug", "triage_queued")),
                       number=number, issue_key=f"github:123:{number}",
                       node_id=f"I_node_{number}",
                       html_url=f"https://github.com/rzwu-uisee/zaofu-self-issue/issues/{number}")
        store.upsert(item, "body")
        owner.emit("external_issue.triage.queued", actor="test", payload={"source_key": item.issue_key, "workflow_run_id": f"R{number}"})
        owner.emit(cancel_type, actor="test", payload={"source_key": item.issue_key, "run_id": f"R{number}"})
    # Simulate a real remote label store. A GET must not change it.
    remote = {8: "triage_queued", 9: "triage_queued"}
    def write(mirror, desired):
        remote[mirror.number] = desired
        return {"status": "reconciled", "label_colors": {desired: "DC2626"}}
    monkeypatch.setattr("zf.web.issue_triage_routes.build_github_lifecycle_label_writer", lambda: write)
    published = []
    original = IssueMirrorStore.project_lifecycle_labels
    def publish(self, updates):
        if self.state_dir == ctx.state_dir:
            published.append(dict(updates))
        return original(self, updates)
    monkeypatch.setattr(IssueMirrorStore, "project_lifecycle_labels", publish)
    class Reconciler:
        def refresh(self, **_):
            return {"ok": True, "status": "fresh", "changed": 0}
    app = FastAPI()
    app.include_router(build_issue_triage_router(
        resolve_ctx=lambda _: ctx, reconciler_factory=lambda *_: Reconciler(),
        mutation_auth_error=lambda *args, **kwargs: None,
    ))
    client = TestClient(app)
    assert client.get("/api/projects/viewer/issue-triage").status_code == 200
    assert remote == {8: "triage_queued", 9: "triage_queued"}
    result = client.post("/api/projects/viewer/issue-triage/refresh?force=true")
    assert result.status_code == 200, result.text
    assert remote == {8: "triage_cancelled", 9: "triage_cancelled"}
    assert len(published) == 1 and len(published[0]) == 2
    items = client.get("/api/projects/viewer/issue-triage").json()["items"]
    assert all(item["labels"] == ["bug", "triage_cancelled"] for item in items)
    assert all(item["label_colors"]["triage_cancelled"] == "DC2626" for item in items)
